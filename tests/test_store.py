"""The store: engagements, targets, findings, artifacts.

The ``store`` fixture (see conftest) pins the year to 26, so the first engagement
is always ``R26-0001``.
"""

import pytest

from bench_recon.status import Severity, Status
from bench_recon.store import EngagementStore

# -- engagements -------------------------------------------------------------


def test_create_returns_a_ref_and_climbs(store) -> None:
    assert store.create_engagement(safe_title="Head unit", target_class="automotive-ecu").ref == (
        "R26-0001"
    )
    assert store.create_engagement(safe_title="Doorbell", target_class="iot-camera").ref == (
        "R26-0002"
    )


def test_create_starts_in_scoping(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    assert e.status is Status.SCOPING


def test_create_requires_safe_title(store) -> None:
    with pytest.raises(ValueError, match="safe_title"):
        store.create_engagement(safe_title="  ", target_class="iot")


def test_create_requires_target_class(store) -> None:
    with pytest.raises(ValueError, match="target_class"):
        store.create_engagement(safe_title="t", target_class="")


def test_rejected_create_does_not_burn_a_ref(store) -> None:
    with pytest.raises(ValueError):
        store.create_engagement(safe_title="", target_class="iot")
    # The next good create still gets 0001 — the failed one took no number.
    assert store.create_engagement(safe_title="t", target_class="iot").ref == "R26-0001"


def test_sensitive_fields_round_trip(store) -> None:
    e = store.create_engagement(
        safe_title="Blue sedan head unit",
        target_class="automotive-ecu",
        client_ref="ACME Motors",
        scope="CAN bus + head unit only; no airbag ECU",
        authorization="Signed SOW 2026-11-01, J. Client",
    )
    got = store.get_by_ref(e.ref)
    assert got.client_ref == "ACME Motors"
    assert got.scope.startswith("CAN bus")
    assert got.authorization.startswith("Signed SOW")


def test_get_missing_is_none(store) -> None:
    assert store.get_by_ref("R26-0099") is None


def test_get_malformed_raises(store) -> None:
    with pytest.raises(ValueError):
        store.get_by_ref("not-a-ref")


def test_list_is_newest_first(store) -> None:
    store.create_engagement(safe_title="first", target_class="iot")
    store.create_engagement(safe_title="second", target_class="iot")
    refs = [e.ref for e in store.list_engagements()]
    assert refs == ["R26-0002", "R26-0001"]


def test_open_only_excludes_closed(store) -> None:
    a = store.create_engagement(safe_title="open", target_class="iot")
    b = store.create_engagement(safe_title="done", target_class="iot")
    store.set_status(b.ref, Status.CLOSED)
    open_refs = [e.ref for e in store.list_engagements(open_only=True)]
    assert a.ref in open_refs
    assert b.ref not in open_refs


def test_set_status_moves_and_stamps(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    moved = store.set_status(e.ref, "active")
    assert moved.status is Status.ACTIVE


def test_set_status_rejects_unknown(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="unknown status"):
        store.set_status(e.ref, "pwned")


def test_update_engagement_edits_allowed_fields(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    updated = store.update_engagement(e.ref, client_ref="ACME", scope="just the UART")
    assert updated.client_ref == "ACME"
    assert updated.scope == "just the UART"


def test_update_engagement_rejects_unknown_field(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="not editable"):
        store.update_engagement(e.ref, status="active")  # status has its own guarded path


def test_update_engagement_cannot_blank_safe_title(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="safe_title"):
        store.update_engagement(e.ref, safe_title="   ")


def test_set_status_on_missing_raises(store) -> None:
    with pytest.raises(LookupError):
        store.set_status("R26-0099", "active")


# -- targets -----------------------------------------------------------------


def test_add_and_list_targets(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    store.add_target(e.ref, make="Acme", model="Cam 2000", interfaces="UART, SPI flash")
    store.add_target(e.ref, model="ECU rev B")
    targets = store.list_targets(e.ref)
    assert [t.model for t in targets] == ["Cam 2000", "ECU rev B"]
    assert targets[0].interfaces == "UART, SPI flash"


def test_target_requires_make_or_model(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="make or model"):
        store.add_target(e.ref, firmware_version="1.2.3")


def test_update_target(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    t = store.add_target(e.ref, model="Cam")
    updated = store.update_target(e.ref, t.id, firmware_version="2.0", pinout_notes="J4 = UART")
    assert updated.firmware_version == "2.0"
    assert updated.pinout_notes == "J4 = UART"


def test_update_target_rejects_unknown_field(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    t = store.add_target(e.ref, model="Cam")
    with pytest.raises(ValueError, match="not editable"):
        store.update_target(e.ref, t.id, engagement_id=99)


def test_remove_target(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    t = store.add_target(e.ref, model="Cam")
    store.remove_target(e.ref, t.id)
    assert store.list_targets(e.ref) == []


def test_targets_are_scoped_to_their_engagement(store) -> None:
    a = store.create_engagement(safe_title="a", target_class="iot")
    b = store.create_engagement(safe_title="b", target_class="iot")
    t = store.add_target(a.ref, model="Cam")
    # The same id must not be reachable through a different engagement.
    assert store.get_target(b.ref, t.id) is None


# -- findings (append-only) --------------------------------------------------


def test_add_and_list_findings_oldest_first(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    store.add_finding(e.ref, severity="high", title="Open UART root shell")
    store.add_finding(e.ref, severity="low", title="Verbose boot log")
    titles = [f.title for f in store.list_findings(e.ref)]
    assert titles == ["Open UART root shell", "Verbose boot log"]


def test_finding_severity_coerced(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    f = store.add_finding(e.ref, severity="critical", title="Hardcoded key")
    assert f.severity is Severity.CRITICAL


def test_finding_requires_title(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="title"):
        store.add_finding(e.ref, severity="low", title="   ")


def test_findings_are_append_only_by_construction() -> None:
    """There is deliberately no update or delete path for findings — a log you can
    quietly edit isn't a log. This pins that contract."""
    assert not hasattr(EngagementStore, "update_finding")
    assert not hasattr(EngagementStore, "remove_finding")
    assert not hasattr(EngagementStore, "delete_finding")


# -- artifacts (metadata) ----------------------------------------------------


def test_record_and_list_artifacts(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    store.record_artifact(
        e.ref,
        stored_name="a1b2.bin",
        content_type="application/octet-stream",
        size=1024,
        kind="firmware-dump",
        original_name="dump.bin",
    )
    arts = store.list_artifacts(e.ref)
    assert len(arts) == 1
    assert arts[0].kind == "firmware-dump"
    assert arts[0].size == 1024
    assert arts[0].original_name == "dump.bin"


def test_record_artifact_requires_stored_name(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="stored_name"):
        store.record_artifact(e.ref, stored_name="", content_type="x", size=1, kind="other")


def test_record_artifact_rejects_negative_size(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    with pytest.raises(ValueError, match="size"):
        store.record_artifact(e.ref, stored_name="a.bin", content_type="x", size=-1, kind="other")


def test_remove_artifact(store) -> None:
    e = store.create_engagement(safe_title="t", target_class="iot")
    a = store.record_artifact(e.ref, stored_name="a.bin", content_type="x", size=1, kind="other")
    store.remove_artifact(e.ref, a.id)
    assert store.list_artifacts(e.ref) == []


def test_writing_to_missing_engagement_raises(store) -> None:
    with pytest.raises(LookupError):
        store.add_target("R26-0099", model="Cam")
    with pytest.raises(LookupError):
        store.add_finding("R26-0099", severity="low", title="x")
