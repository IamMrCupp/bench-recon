"""The read-only benchhud API: ``/api/engagements`` and artifact metadata.

Two concerns. The gate — bearer token or session, 401 otherwise — and the
**leak scan**: with a fully populated engagement (sensitive client, scope,
authorization, target identity, findings, a credential in a finding body), the
whole JSON the API returns is scanned for every sensitive value, and the test
fails if any crosses. This is the end-to-end counterpart to the unit leak test in
``test_projection.py``: it proves the *handlers* only ever emit the projection.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from bench_recon.app import app, get_store
from bench_recon.db import connect
from bench_recon.schema import initialise
from bench_recon.store import EngagementStore
from conftest import sign_in

TOKEN = {"Authorization": "Bearer test-api-token"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHRECON_API_TOKEN", "test-api-token")
    conn = connect(tmp_path / "bench-recon.db")
    initialise(conn)
    store = EngagementStore(conn, year=26)
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), store
    app.dependency_overrides.clear()
    conn.close()


def _seed_sensitive(store: EngagementStore) -> str:
    """One engagement with a distinctive SECRET in every sensitive field."""
    engagement = store.create_engagement(
        safe_title="Blue sedan head unit",
        target_class="automotive-ecu",
        client_ref="ACME-CLIENT-SECRET",
        scope="in-SECRET-scope",
        authorization="AUTH-LETTER-SECRET",
    )
    ref = engagement.ref
    store.add_target(
        ref,
        make="MAKE-SECRET",
        model="MODEL-SECRET",
        firmware_version="FW-SECRET",
        interfaces="UART-SECRET J4",
        pinout_notes="PINOUT-SECRET",
        serial="SERIAL-SECRET",
    )
    store.add_finding(
        ref,
        severity="critical",
        title="FINDING-TITLE-SECRET",
        body="root password is hunter2-SECRET",
    )
    store.record_artifact(
        ref,
        stored_name="abcdef0123456789.elf",
        content_type="application/x-elf",
        size=4096,
        kind="firmware-dump",
        original_name="headunit-dump.bin",
    )
    return ref


SECRETS = [
    "ACME-CLIENT-SECRET",
    "in-SECRET-scope",
    "AUTH-LETTER-SECRET",
    "MAKE-SECRET",
    "MODEL-SECRET",
    "FW-SECRET",
    "UART-SECRET",
    "PINOUT-SECRET",
    "SERIAL-SECRET",
    "FINDING-TITLE-SECRET",
    "hunter2-SECRET",
    "abcdef0123456789.elf",  # the on-disk artifact path must not cross either
]


# -- the gate ---------------------------------------------------------------


def test_api_requires_auth(api) -> None:
    client, store = api
    _seed_sensitive(store)
    assert client.get("/api/engagements").status_code == 401


def test_api_accepts_a_bearer_token(api) -> None:
    client, store = api
    _seed_sensitive(store)
    assert client.get("/api/engagements", headers=TOKEN).status_code == 200


def test_api_rejects_a_wrong_bearer_token(api) -> None:
    client, store = api
    _seed_sensitive(store)
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/api/engagements", headers=bad).status_code == 401


def test_api_accepts_a_browser_session(api) -> None:
    client, store = api
    _seed_sensitive(store)
    sign_in(client)  # a logged-in browser reaches the API without a token
    assert client.get("/api/engagements").status_code == 200


# -- the leak scan ----------------------------------------------------------


def test_list_endpoint_leaks_nothing_sensitive(api) -> None:
    client, store = api
    _seed_sensitive(store)
    blob = json.dumps(client.get("/api/engagements", headers=TOKEN).json())
    for secret in SECRETS:
        assert secret not in blob, f"leaked via /api/engagements: {secret!r}"


def test_detail_endpoint_leaks_nothing_sensitive(api) -> None:
    client, store = api
    ref = _seed_sensitive(store)
    blob = json.dumps(client.get(f"/api/engagements/{ref}", headers=TOKEN).json())
    for secret in SECRETS:
        assert secret not in blob, f"leaked via /api/engagements/{{ref}}: {secret!r}"


def test_artifacts_endpoint_leaks_nothing_sensitive(api) -> None:
    client, store = api
    ref = _seed_sensitive(store)
    resp = client.get(f"/api/engagements/{ref}/artifacts", headers=TOKEN)
    blob = json.dumps(resp.json())
    for secret in SECRETS:
        assert secret not in blob, f"leaked via /artifacts: {secret!r}"
    # The metadata that IS meant to cross is present.
    assert "headunit-dump.bin" in blob
    assert "firmware-dump" in blob


# -- the ref-safe shape that DOES cross -------------------------------------


def test_list_returns_the_ref_safe_fields(api) -> None:
    client, store = api
    ref = _seed_sensitive(store)
    payload = client.get("/api/engagements", headers=TOKEN).json()
    assert payload == [
        {
            "ref": ref,
            "title": "Blue sedan head unit",
            "target_class": "automotive-ecu",
            "status": "scoping",
        }
    ]


def test_list_filters_by_status(api) -> None:
    client, store = api
    ref = _seed_sensitive(store)
    store.set_status(ref, "closed")
    # Default (open) now excludes it...
    assert client.get("/api/engagements", headers=TOKEN).json() == []
    # ...but an explicit filter finds it.
    found = client.get("/api/engagements?status=closed", headers=TOKEN).json()
    assert [e["ref"] for e in found] == [ref]


def test_unknown_status_filter_is_400(api) -> None:
    client, _ = api
    assert client.get("/api/engagements?status=banana", headers=TOKEN).status_code == 400


def test_detail_unknown_ref_is_404(api) -> None:
    client, _ = api
    assert client.get("/api/engagements/R26-9999", headers=TOKEN).status_code == 404


def test_artifacts_metadata_shape(api) -> None:
    client, store = api
    ref = _seed_sensitive(store)
    artifacts = client.get(f"/api/engagements/{ref}/artifacts", headers=TOKEN).json()
    assert artifacts == [
        {"name": "headunit-dump.bin", "kind": "firmware-dump", "size": 4096, "status": "stored"}
    ]
