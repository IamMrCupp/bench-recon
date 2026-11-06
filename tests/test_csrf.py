"""CSRF enforcement on state-changing requests.

A signed-in session is necessary but not sufficient: every mutating request also
needs the session's CSRF token, so a cross-site POST that rides the cookie still
can't change anything. Uses a plain client (not the auto-token one) so the token
can be omitted or corrupted deliberately.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from bench_recon.app import app, get_store
from bench_recon.db import connect
from bench_recon.schema import initialise
from bench_recon.store import EngagementStore
from conftest import TEST_PASSWORD


@pytest.fixture
def client(tmp_path):
    conn = connect(tmp_path / "bench-recon.db")
    initialise(conn)
    store = EngagementStore(conn, year=26)
    app.dependency_overrides[get_store] = lambda: store
    c = TestClient(app)
    # Sign in by hand (the token comes from the login form).
    token = re.search(r'name="csrf_token"\s+value="([^"]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"password": TEST_PASSWORD, "csrf_token": token})
    yield c
    app.dependency_overrides.clear()
    conn.close()


def _session_csrf(client: TestClient) -> str:
    return re.search(
        r'name="csrf_token"\s+value="([^"]+)"', client.get("/engagements/new").text
    ).group(1)


def test_post_without_a_token_is_refused(client) -> None:
    resp = client.post("/engagements", data={"safe_title": "x", "target_class": "y"})
    assert resp.status_code == 403
    assert "could not be verified" in resp.text.lower()


def test_post_with_a_forged_token_is_refused(client) -> None:
    resp = client.post(
        "/engagements",
        data={"safe_title": "x", "target_class": "y", "csrf_token": "forged"},
    )
    assert resp.status_code == 403


def test_post_with_the_session_token_is_accepted(client) -> None:
    resp = client.post(
        "/engagements",
        data={
            "safe_title": "Blue sedan head unit",
            "target_class": "automotive-ecu",
            "csrf_token": _session_csrf(client),
        },
        follow_redirects=False,
    )
    # Not a CSRF rejection — the create went through to a redirect.
    assert resp.status_code == 303
