"""The login flow: sign in, wrong password, lockout, logout, and ``?next=``.

Drives a plain ``TestClient`` on purpose — the CSRF token is read from the
rendered form by hand, the way a browser carries it, because the gate is part of
what's under test here.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from bench_recon.app import app
from conftest import TEST_PASSWORD


def _csrf(client: TestClient) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', client.get("/login").text)
    assert match, "login form is missing its CSRF token"
    return match.group(1)


def _login(client: TestClient, password: str, **extra):
    data = {"password": password, "csrf_token": _csrf(client), **extra}
    return client.post("/login", data=data, follow_redirects=False)


def test_right_password_signs_in_and_opens_the_app() -> None:
    client = TestClient(app)
    resp = _login(client, TEST_PASSWORD)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/engagements"
    # The session now opens a gated page that was a redirect before.
    assert client.get("/engagements/new").status_code == 200


def test_wrong_password_is_401_and_says_nothing_useful() -> None:
    client = TestClient(app)
    resp = _login(client, "not the password")
    assert resp.status_code == 401
    assert "not right" in resp.text
    # Still locked out of gated pages.
    assert client.get("/engagements/new", follow_redirects=False).status_code == 303


def test_lockout_after_too_many_attempts() -> None:
    client = TestClient(app)
    for _ in range(5):
        assert _login(client, "wrong").status_code == 401
    locked = _login(client, "wrong")
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers
    # Even the correct password is refused while locked out.
    assert _login(client, TEST_PASSWORD).status_code == 429


def test_logout_clears_the_session() -> None:
    client = TestClient(app)
    _login(client, TEST_PASSWORD)
    assert client.get("/engagements/new").status_code == 200

    out = client.post("/logout", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert out.status_code == 303
    assert client.get("/engagements/new", follow_redirects=False).status_code == 303


def test_next_carries_a_safe_path_through_login() -> None:
    client = TestClient(app)
    resp = _login(client, TEST_PASSWORD, next="/engagements/R26-0001")
    assert resp.headers["location"] == "/engagements/R26-0001"


def test_next_refuses_an_offsite_bounce() -> None:
    client = TestClient(app)
    resp = _login(client, TEST_PASSWORD, next="//evil.example.com/phish")
    assert resp.headers["location"] == "/engagements"
