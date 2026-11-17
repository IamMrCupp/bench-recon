"""The footer's Source link.

AGPL section 13 obliges whoever runs a modified copy as a network service to
offer its users the source. The stock app carries the offer so a fork inherits
it, which only works if three things stay true: the link is on every page, it's
there *before* sign-in, and a fork can point it at its own code.

The last test is the privacy one. A footer renders on every page, including the
one that shows a client and a target — so it must hold the link and the licence
name and nothing else.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from bench_recon.app import (
    DEFAULT_SOURCE_URL,
    LICENSE_NAME,
    SOURCE_URL_ENV_VAR,
    app,
    get_artifact_store,
    get_store,
)
from bench_recon.artifacts import ArtifactStore
from bench_recon.db import connect
from bench_recon.schema import initialise
from bench_recon.store import EngagementStore
from conftest import CsrfClient, sign_in

FORK_URL = "https://git.example.invalid/someone/recon-fork"


@pytest.fixture(autouse=True)
def _upstream_default(monkeypatch):
    """Start every test from the stock configuration, whatever the shell has set."""
    monkeypatch.delenv(SOURCE_URL_ENV_VAR, raising=False)


@pytest.fixture
def client(tmp_path):
    """Signed in, against a real temporary database."""
    conn = connect(tmp_path / "bench-recon.db")
    initialise(conn)
    store = EngagementStore(conn, year=26)
    artifact_store = ArtifactStore(tmp_path / "artifacts")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_artifact_store] = lambda: artifact_store
    with CsrfClient(app) as client:
        sign_in(client)
        yield client
    app.dependency_overrides.clear()
    conn.close()


@pytest.fixture
def engagement(client) -> str:
    """An engagement with the sensitive fields filled in; returns its ref."""
    resp = client.post(
        "/engagements",
        data={
            "safe_title": "Blue sedan head unit",
            "target_class": "automotive-ecu",
            "authorization": "SECRET-AUTH-LETTER",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _footer(body: str) -> str:
    match = re.search(r"<footer>(.*?)</footer>", body, re.S)
    assert match, "the base layout has no footer"
    return match.group(1)


def _link(url: str) -> str:
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">Source</a>'


def test_sign_in_page_offers_the_source_without_a_session() -> None:
    response = TestClient(app).get("/login")

    assert response.status_code == 200
    footer = _footer(response.text)
    assert _link(DEFAULT_SOURCE_URL) in footer
    assert LICENSE_NAME in footer


def test_default_points_at_upstream() -> None:
    assert DEFAULT_SOURCE_URL == "https://github.com/IamMrCupp/bench-recon"
    assert LICENSE_NAME == "AGPL-3.0-or-later"


def test_every_page_carries_the_link(client, engagement) -> None:
    for path in ("/login", "/engagements", "/engagements/new", f"/engagements/{engagement}"):
        assert _link(DEFAULT_SOURCE_URL) in _footer(client.get(path).text), path


def test_a_fork_can_point_the_link_at_its_own_source(monkeypatch) -> None:
    monkeypatch.setenv(SOURCE_URL_ENV_VAR, FORK_URL)

    body = TestClient(app).get("/login").text

    assert _link(FORK_URL) in _footer(body)
    assert DEFAULT_SOURCE_URL not in body


@pytest.mark.parametrize("value", ["", "   ", "javascript:alert(1)", "example.invalid/fork"])
def test_a_value_that_is_not_a_web_address_falls_back_to_upstream(monkeypatch, value) -> None:
    """It lands in an ``href``. A typo must not leave a dead or scripted link."""
    monkeypatch.setenv(SOURCE_URL_ENV_VAR, value)

    assert _link(DEFAULT_SOURCE_URL) in _footer(TestClient(app).get("/login").text)


def test_footer_holds_the_link_and_the_licence_and_nothing_else(client, engagement) -> None:
    """On the page that shows the most about an engagement, the footer still
    says only where the code is."""
    body = client.get(f"/engagements/{engagement}").text
    assert "SECRET-AUTH-LETTER" in body  # the page really is the sensitive one
    footer = _footer(body)

    text = " ".join(re.sub(r"<[^>]+>", " ", footer).split())
    assert text == f"Source · {LICENSE_NAME}"
    assert _link(DEFAULT_SOURCE_URL) in footer
