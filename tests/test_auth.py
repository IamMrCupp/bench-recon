"""Password hashing, the API token, and CSRF helpers.

Unit-level: these are the primitives the middleware is built from. The gate
itself (routes redirecting, 401s on the API) is ``test_auth_required.py``.
"""

from __future__ import annotations

import pytest

from bench_recon import auth

# -- password hashing -------------------------------------------------------


def test_hash_then_verify_roundtrips() -> None:
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("wrong horse", stored)


def test_hash_is_salted() -> None:
    # Same password, two different stored hashes — a leaked DB can't be reversed
    # by comparing identical rows.
    assert auth.hash_password("same") != auth.hash_password("same")


def test_hash_is_self_describing() -> None:
    assert auth.hash_password("x").startswith("scrypt$16384$8$1$")


@pytest.mark.parametrize("bad", ["", "not-a-hash", "scrypt$only$three", "md5$1$1$1$aa$bb"])
def test_verify_denies_malformed_hashes(bad) -> None:
    # A corrupted env var must deny, never raise into a state read as a pass.
    assert auth.verify_password("anything", bad) is False


# -- configured hash --------------------------------------------------------


def test_configured_hash_raises_when_unset(monkeypatch) -> None:
    monkeypatch.delenv(auth.ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="refuses to start"):
        auth.configured_hash()


def test_session_secret_derives_from_the_hash(monkeypatch) -> None:
    monkeypatch.delenv(auth.SESSION_ENV_VAR, raising=False)
    monkeypatch.setenv(auth.ENV_VAR, "scrypt$16384$8$1$aa$bb")
    # Deterministic across calls (not a per-process random) and non-empty.
    assert auth.session_secret() == auth.session_secret()
    assert auth.session_secret()


# -- API token --------------------------------------------------------------


def test_api_token_fails_closed_when_unset(monkeypatch) -> None:
    monkeypatch.delenv(auth.API_TOKEN_ENV_VAR, raising=False)
    # No configured token must reject bearer auth outright, never accept any token.
    assert auth.valid_api_token("Bearer whatever") is False


def test_api_token_accepts_the_configured_token(monkeypatch) -> None:
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, "s3cret-token")
    assert auth.valid_api_token("Bearer s3cret-token") is True
    assert auth.valid_api_token("Bearer wrong") is False
    assert auth.valid_api_token("s3cret-token") is False  # missing scheme
    assert auth.valid_api_token(None) is False


# -- cookie secure ----------------------------------------------------------


def test_cookie_secure_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(auth.COOKIE_SECURE_ENV_VAR, raising=False)
    assert auth.cookie_secure() is False
    monkeypatch.setenv(auth.COOKIE_SECURE_ENV_VAR, "1")
    assert auth.cookie_secure() is True


# -- CSRF -------------------------------------------------------------------


def test_csrf_token_is_minted_once_and_stable() -> None:
    session: dict = {}
    first = auth.csrf_token(session)
    assert first and auth.csrf_token(session) == first  # same token on re-read


def test_csrf_valid_requires_a_match() -> None:
    session: dict = {}
    token = auth.csrf_token(session)
    assert auth.csrf_valid(session, token)
    assert not auth.csrf_valid(session, "forged")
    assert not auth.csrf_valid(session, None)
    assert not auth.csrf_valid({}, token)  # no token in this session
