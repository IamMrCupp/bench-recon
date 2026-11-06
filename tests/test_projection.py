"""The benchhud projection — the single place sensitive recon data is filtered.

Unit-level here: the exact shape and that the engagement-level sensitive fields
don't survive ``project_engagement``. The end-to-end guarantee (sensitive target,
finding and credential values never reaching the API payload) is the integration
leak test in ``tests/test_api.py``, which scans the live JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from bench_recon.projection import (
    ARTIFACT_KEYS,
    ENGAGEMENT_KEYS,
    project_artifact,
    project_engagement,
)
from bench_recon.status import Status
from bench_recon.store import Artifact, Engagement

# An engagement with every sensitive field set to a distinctive, greppable value.
FULL = Engagement(
    ref="R26-0007",
    safe_title="Blue sedan head unit",
    target_class="automotive-ecu",
    status=Status.ACTIVE,
    created_at="2026-11-04T10:00:00+00:00",
    updated_at="2026-11-04T11:30:00+00:00",
    client_ref="ACME-CLIENT-SECRET",
    scope="in-scope-SECRET and out-of-scope-SECRET",
    authorization="signed-authorization-SECRET dated 2026-11-01",
)


def project() -> dict:
    return project_engagement(FULL)


# -- shape ------------------------------------------------------------------


def test_exactly_the_contract_keys() -> None:
    assert set(project()) == set(ENGAGEMENT_KEYS)


def test_the_contract_keys_are_the_expected_four() -> None:
    assert set(ENGAGEMENT_KEYS) == {"ref", "title", "target_class", "status"}


def test_ref_title_class_status_pass_through() -> None:
    result = project()
    assert result["ref"] == "R26-0007"
    assert result["title"] == "Blue sedan head unit"
    assert result["target_class"] == "automotive-ecu"
    assert result["status"] == "active"


def test_status_is_coerced_to_its_string_value() -> None:
    # Whether it arrives as the enum or a bare string, the payload is a str.
    from dataclasses import replace

    assert project_engagement(replace(FULL, status="reporting"))["status"] == "reporting"
    assert isinstance(project()["status"], str)


# -- the sensitivity guarantee (engagement level) ---------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "ACME-CLIENT-SECRET",
        "in-scope-SECRET",
        "out-of-scope-SECRET",
        "signed-authorization-SECRET",
        "2026-11-04T10:00:00",  # created_at
        "2026-11-04T11:30:00",  # updated_at
    ],
)
def test_no_sensitive_engagement_field_survives(secret) -> None:
    """Scan the whole serialized projection — not specific keys — so a field added
    to ``Engagement`` later cannot quietly leak into benchhud."""
    blob = json.dumps(project())
    assert secret not in blob, f"leaked into the benchhud projection: {secret!r}"


# -- artifact projection ----------------------------------------------------


def test_artifact_projection_is_metadata_only() -> None:
    artifact = Artifact(
        id=3,
        stored_name="a1b2c3d4e5f6a7b8.elf",  # the generated on-disk name — must not cross
        original_name="headunit-dump.bin",
        content_type="application/x-elf",
        size=4_194_304,
        kind="firmware-dump",
        created_at="2026-11-04T12:00:00+00:00",
    )
    result = project_artifact(artifact)

    assert set(result) == set(ARTIFACT_KEYS)
    assert result == {
        "name": "headunit-dump.bin",  # the display name crosses
        "kind": "firmware-dump",
        "size": 4_194_304,
        "status": "stored",
    }
    # The on-disk path and the content type stay behind the boundary.
    blob = json.dumps(result)
    assert "a1b2c3d4e5f6a7b8.elf" not in blob
    assert "application/x-elf" not in blob


def test_artifact_name_falls_back_to_stored_name() -> None:
    artifact = Artifact(
        id=1,
        stored_name="deadbeef.bin",
        original_name=None,
        content_type="application/octet-stream",
        size=10,
        kind="capture",
        created_at="t",
    )
    assert project_artifact(artifact)["name"] == "deadbeef.bin"


# -- benchhud shape round-trip ----------------------------------------------


@dataclass(frozen=True)
class _BenchhudEngagement:
    """A local stand-in for benchhud.recon.source.Engagement, same fields.

    Constructing it from the projection proves the JSON is exactly what benchhud
    will deserialize into. If the real dataclass changes, this is where the
    mismatch should surface first.
    """

    ref: str
    title: str = ""
    target_class: str = ""
    status: str = ""


def test_projection_constructs_a_benchhud_engagement() -> None:
    engagement = _BenchhudEngagement(**project())
    assert engagement.ref == "R26-0007"
    assert engagement.title == "Blue sedan head unit"
    assert engagement.status == "active"
