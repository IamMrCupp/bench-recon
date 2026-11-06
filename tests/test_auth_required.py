"""Route protection — the real deliverable of the auth gate.

``test_every_route_redirects_when_signed_out`` is the point. The middleware
itself is a dozen lines; what's hard is guaranteeing that a route added in a
later todo — the projection endpoints, a future upload path — can't quietly ship
unprotected. This enumerates the app's own routing table, so it fails on a new
unlisted route rather than trusting anyone to remember.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from bench_recon.app import EXEMPT_PATHS, app

client = TestClient(app)  # plain + unauthenticated, on purpose


def _concrete(path_format: str) -> str:
    """Fill path params with a placeholder. The value is irrelevant — the auth
    middleware redirects on the raw path before any handler or validation runs."""
    return re.sub(r"\{[^}]+\}", "x", path_format)


def _browser_paths() -> dict[str, str]:
    """Every concrete browser path the app serves (deduped), excluding /api/*
    which is gated differently (bearer-or-session → 401, not a redirect)."""
    paths: dict[str, str] = {}
    for route in app.routes:
        path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
        if not path_format or getattr(route, "methods", None) is None:
            continue
        concrete = _concrete(path_format)
        if not concrete.startswith("/api/"):
            paths[concrete] = path_format
    return paths


def test_exempt_paths_are_exactly_health_and_login() -> None:
    # Pin the exemption list: adding to it is a security decision that should
    # require editing this assertion, not slip in unnoticed.
    assert sorted(EXEMPT_PATHS) == ["/healthz", "/login"]


def test_exempt_paths_answer_without_a_session() -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200


@pytest.mark.parametrize("path", sorted(p for p in _browser_paths() if p not in EXEMPT_PATHS))
def test_every_route_redirects_when_signed_out(path) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303, f"{path} did not redirect an unauthenticated GET"
    assert response.headers["location"].startswith("/login"), f"{path} redirected somewhere odd"


def test_api_namespace_is_401_not_a_redirect() -> None:
    """The machine-facing namespace answers a service, not a browser: a 401 it
    can read, never a 303 to an HTML login page. (Endpoints land in todo 5; the
    gate is already in place.)"""
    response = client.get("/api/engagements", follow_redirects=False)
    assert response.status_code == 401


def test_api_rejects_a_bad_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("BENCHRECON_API_TOKEN", "the-real-token")
    bad = client.get("/api/anything", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
