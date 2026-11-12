"""The health probe.

The Kubernetes liveness/readiness probe hits ``/healthz``. If it ever needs a
session (once the auth gate lands) the pod never goes ready, and the deployment
fails in a way that looks unrelated to auth — so this test pins it open.
"""

from fastapi.testclient import TestClient

from bench_recon import __version__
from bench_recon.app import app

client = TestClient(app)


def test_healthz_answers_ok() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_healthz_names_the_app_and_its_version() -> None:
    """bench-setup tells "bench-recon on 8403" from "something on 8403" by this."""
    body = client.get("/healthz").json()
    assert body["app"] == "bench-recon"
    assert body["version"] == __version__


def test_healthz_needs_no_session_or_token() -> None:
    """A fresh client, no cookie, no bearer: the k8s probe's view."""
    response = TestClient(app).get("/healthz", follow_redirects=False)
    assert response.status_code == 200


def test_healthz_says_nothing_else() -> None:
    """The one unauthenticated route must stay boring — never leak a record."""
    assert set(client.get("/healthz").json()) == {"status", "app", "version"}
