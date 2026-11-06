"""The web UI routes.

Driven through ``TestClient`` against a real (temporary) database and artifact
directory, wired in with dependency overrides so a test's writes persist across
requests the way they do in the running app. The year is pinned to 26, so refs
are predictable (``R26-0001`` …).

The load-bearing test here is ``test_list_view_hides_sensitive_fields``: the
engagement list is the screen most likely to be streamed or read over a
shoulder, and it must never carry a client or a target identity.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bench_recon.app import app, get_artifact_store, get_store
from bench_recon.artifacts import ArtifactStore
from bench_recon.db import connect
from bench_recon.schema import initialise
from bench_recon.store import EngagementStore


@pytest.fixture
def ctx(tmp_path):
    conn = connect(tmp_path / "bench-recon.db")
    initialise(conn)
    store = EngagementStore(conn, year=26)
    artifact_store = ArtifactStore(tmp_path / "artifacts")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store
    with TestClient(app) as client:
        yield client, store
    app.dependency_overrides.clear()
    conn.close()


@pytest.fixture
def client(ctx):
    return ctx[0]


def _create(client, **fields) -> str:
    """Create an engagement through the form and return its ref."""
    payload = {"safe_title": "Blue sedan head unit", "target_class": "automotive-ecu"}
    payload.update(fields)
    resp = client.post("/engagements", data=payload, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


# -- listing + creating -----------------------------------------------------


def test_root_redirects_to_engagements(client) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/engagements"


def test_empty_list(client) -> None:
    body = client.get("/engagements").text
    assert "No open engagements" in body


def test_create_then_see_it_listed(client) -> None:
    ref = _create(client)
    assert ref == "R26-0001"

    listing = client.get("/engagements").text
    assert "R26-0001" in listing
    assert "Blue sedan head unit" in listing


def test_create_requires_safe_title(client) -> None:
    resp = client.post(
        "/engagements",
        data={"safe_title": "", "target_class": "iot-camera", "scope": "keep this scope text"},
    )
    assert resp.status_code == 400
    # The form comes back with what was typed still in it.
    assert "keep this scope text" in resp.text
    assert "required" in resp.text


def test_list_view_hides_sensitive_fields(client) -> None:
    """The four ref-safe fields and nothing else reach the list."""
    _create(
        client,
        client_ref="ACME-SECRET-CLIENT",
        scope="SECRET-SCOPE-TEXT",
        authorization="SECRET-AUTH-LETTER",
    )
    listing = client.get("/engagements").text

    assert "R26-0001" in listing  # ref — safe
    assert "Blue sedan head unit" in listing  # safe_title — safe
    assert "automotive-ecu" in listing  # target_class — safe
    assert "ACME-SECRET-CLIENT" not in listing
    assert "SECRET-SCOPE-TEXT" not in listing
    assert "SECRET-AUTH-LETTER" not in listing


# -- detail: authorization banner, edit, status -----------------------------


def test_detail_warns_when_authorization_is_missing(client) -> None:
    ref = _create(client)
    body = client.get(f"/engagements/{ref}").text
    assert "No authorization recorded" in body


def test_detail_shows_the_authorization_banner(client) -> None:
    ref = _create(client, authorization="Signed authorization on file 2026-11-01")
    body = client.get(f"/engagements/{ref}").text
    assert "Authorization on file" in body
    assert "Signed authorization on file 2026-11-01" in body


def test_edit_engagement(client) -> None:
    ref = _create(client)
    client.post(
        f"/engagements/{ref}",
        data={
            "safe_title": "Grey hatchback head unit",
            "target_class": "automotive-ecu",
            "client_ref": "",
            "scope": "",
            "authorization": "",
        },
        follow_redirects=False,
    )
    assert "Grey hatchback head unit" in client.get(f"/engagements/{ref}").text


def test_status_change(client) -> None:
    ref = _create(client)
    client.post(f"/engagements/{ref}/status", data={"status": "active"}, follow_redirects=False)
    assert "active" in client.get(f"/engagements/{ref}").text


def test_unknown_status_is_rejected(client) -> None:
    ref = _create(client)
    resp = client.post(f"/engagements/{ref}/status", data={"status": "banana"})
    assert resp.status_code == 400


def test_malformed_ref_is_404(client) -> None:
    assert client.get("/engagements/not-a-ref").status_code == 404


# -- targets ----------------------------------------------------------------


def test_add_target(client) -> None:
    ref = _create(client)
    client.post(
        f"/engagements/{ref}/targets",
        data={"make": "Continental", "model": "A2C-884", "interfaces": "UART J4"},
        follow_redirects=False,
    )
    body = client.get(f"/engagements/{ref}").text
    assert "Continental" in body
    assert "UART J4" in body


def test_target_needs_make_or_model(client) -> None:
    ref = _create(client)
    resp = client.post(f"/engagements/{ref}/targets", data={"make": "", "model": ""})
    assert resp.status_code == 400


def test_remove_target(client, ctx) -> None:
    _, store = ctx
    ref = _create(client)
    target = store.add_target(ref, make="Continental")
    client.post(f"/engagements/{ref}/targets/{target.id}/delete", follow_redirects=False)
    assert store.list_targets(ref) == []


# -- findings (append-only) -------------------------------------------------


def test_add_finding(client) -> None:
    ref = _create(client)
    client.post(
        f"/engagements/{ref}/findings",
        data={"severity": "high", "title": "Unauth UART root shell", "body": "no login prompt"},
        follow_redirects=False,
    )
    body = client.get(f"/engagements/{ref}").text
    assert "Unauth UART root shell" in body
    assert "no login prompt" in body


def test_finding_needs_a_title(client) -> None:
    ref = _create(client)
    resp = client.post(f"/engagements/{ref}/findings", data={"severity": "low", "title": ""})
    assert resp.status_code == 400


def test_findings_have_no_delete_route(client, ctx) -> None:
    """The log is append-only: there is no per-finding delete endpoint."""
    _, store = ctx
    ref = _create(client)
    finding = store.add_finding(ref, severity="low", title="note")
    resp = client.post(f"/engagements/{ref}/findings/{finding.id}/delete")
    assert resp.status_code in (404, 405)  # no such route


# -- artifacts --------------------------------------------------------------


def test_upload_download_and_delete_artifact(client, ctx) -> None:
    _, store = ctx
    ref = _create(client)

    up = client.post(
        f"/engagements/{ref}/artifacts",
        data={"kind": "firmware-dump"},
        files={"artifact": ("flash.bin", b"\x7fELFdumped firmware", "application/octet-stream")},
        follow_redirects=False,
    )
    assert up.status_code == 303

    artifacts = store.list_artifacts(ref)
    assert len(artifacts) == 1
    record = artifacts[0]
    assert record.kind == "firmware-dump"
    assert record.content_type == "application/x-elf"  # sniffed, not the posted type
    assert record.original_name == "flash.bin"

    dl = client.get(f"/engagements/{ref}/artifacts/{record.id}/download")
    assert dl.status_code == 200
    assert dl.content == b"\x7fELFdumped firmware"

    client.post(f"/engagements/{ref}/artifacts/{record.id}/delete", follow_redirects=False)
    assert store.list_artifacts(ref) == []
    assert client.get(f"/engagements/{ref}/artifacts/{record.id}/download").status_code == 404


def test_empty_upload_is_reported_not_500(client) -> None:
    ref = _create(client)
    resp = client.post(
        f"/engagements/{ref}/artifacts",
        data={"kind": "capture"},
        files={"artifact": ("nothing.bin", b"", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "empty" in resp.text.lower()


def test_download_missing_artifact_is_404(client) -> None:
    ref = _create(client)
    assert client.get(f"/engagements/{ref}/artifacts/999/download").status_code == 404
