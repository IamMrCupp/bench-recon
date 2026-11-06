"""Shared test configuration.

Two jobs here. First, the **store fixtures** (``conn``/``store``) — a fresh
on-disk SQLite database per test, initialised to the current schema, year pinned
to 26 so refs are predictable (``R26-0001`` …). On-disk rather than ``:memory:``
because the production concern — NFS pragmas, a connection per request — is about
a real file.

Second, the **auth configuration**. ``bench_recon.app`` resolves its session
secret at import time, which means importing it without a configured password
raises — deliberately, so a misconfigured deployment crashloops visibly rather
than starting up serving sensitive records. The suite therefore has to configure
itself the same way a deployment does, and it's done here at collection so no
future test file can import the app unconfigured and get a confusing error a long
way from the cause.
"""

import os

import pytest
from fastapi.testclient import TestClient

from bench_recon.auth import COOKIE_SECURE_ENV_VAR, ENV_VAR, SESSION_ENV_VAR, hash_password
from bench_recon.db import connect
from bench_recon.schema import initialise
from bench_recon.store import EngagementStore

TEST_PASSWORD = "bench recon password"

# Hashed once at collection: scrypt is deliberately not cheap, and this runs
# before every test module that touches the app.
os.environ.setdefault(ENV_VAR, hash_password(TEST_PASSWORD))
os.environ.setdefault(SESSION_ENV_VAR, "test-session-secret-not-a-real-one")
os.environ.setdefault(COOKIE_SECURE_ENV_VAR, "0")


# -- store fixtures ---------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "bench-recon.db"
    connection = connect(path)
    initialise(connection)
    yield connection
    connection.close()


@pytest.fixture
def store(conn):
    return EngagementStore(conn, year=26)


# -- web fixtures -----------------------------------------------------------


class CsrfClient(TestClient):
    """A client that carries its CSRF token, the way a browser does.

    Tests about page behaviour shouldn't each re-implement "read the hidden
    field, post it back" — that's browser mechanics, not the thing under test.
    Tests *about* CSRF use a plain ``TestClient`` so they can omit or corrupt the
    token deliberately. An explicitly supplied ``csrf_token`` always wins.
    """

    def post(self, url, *, data=None, files=None, **kwargs):
        if not (isinstance(data, dict) and "csrf_token" in data):
            data = {**(data or {}), "csrf_token": self._csrf()}
        return super().post(url, data=data, files=files, **kwargs)

    def _csrf(self) -> str:
        import re

        for page in ("/engagements", "/login"):
            match = re.search(r'name="csrf_token"\s+value="([^"]+)"', self.get(page).text)
            if match:
                return match.group(1)
        raise AssertionError("could not find a CSRF token on any accessible page")


def sign_in(client):
    """Give a test client a session.

    Every route except ``/healthz`` and ``/login`` needs one, so any test about
    page content has to authenticate first. Tests *about* the gate deliberately
    don't call this — see ``tests/test_auth_required.py``.
    """
    import re

    # /login is exempt from *auth* but not from CSRF, so the token has to come
    # from the rendered form — exactly as a browser would do it.
    page = client.get("/login").text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page)
    assert match, "login form is missing its CSRF token"

    response = client.post("/login", data={"password": TEST_PASSWORD, "csrf_token": match.group(1)})
    assert response.status_code in (200, 303), f"test sign-in failed: {response.status_code}"
    return client


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """Give every test a clean rate-limit bucket.

    The limiter is process-global and ``TestClient`` always reports the same host,
    so without this every wrong-password test in the suite shares one counter.
    """
    from bench_recon.app import login_limiter

    login_limiter._failures.clear()
    yield
    login_limiter._failures.clear()
