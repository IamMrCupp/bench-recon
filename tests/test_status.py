"""The status/severity vocabulary and its coercion helpers."""

import pytest

from bench_recon.status import (
    OPEN_STATUSES,
    Severity,
    Status,
    is_open,
    to_severity,
    to_status,
)


def test_closed_is_the_only_terminal_status() -> None:
    assert frozenset(Status) - {Status.CLOSED} == OPEN_STATUSES
    assert not is_open(Status.CLOSED)
    assert all(is_open(s) for s in OPEN_STATUSES)


def test_to_status_coerces_strings() -> None:
    assert to_status("active") is Status.ACTIVE
    assert to_status(Status.SCOPING) is Status.SCOPING


def test_to_status_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown status"):
        to_status("pwned")


def test_to_severity_coerces_strings() -> None:
    assert to_severity("critical") is Severity.CRITICAL
    assert to_severity(Severity.LOW) is Severity.LOW


def test_to_severity_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown severity"):
        to_severity("spicy")


def test_open_statuses_is_derived_not_hand_listed() -> None:
    """Adding a status must not require touching the open set by hand."""
    assert Status.AUTHORIZED in OPEN_STATUSES
    assert Status.REPORTING in OPEN_STATUSES
