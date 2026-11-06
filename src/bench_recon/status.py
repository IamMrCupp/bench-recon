"""Where an engagement is, and how severe a finding is.

Two small vocabularies, defined once here and imported by the schema so the
database CHECK constraints and the Python coercion can't drift apart (the
mistake benchhud-intake had to guard with a duplication test — here there's a
single source of truth instead).

The engagement lifecycle is deliberately short. An engagement is a job: you
scope it, you get authorization in writing, you do the work, you write it up,
you close it. The authorization step is a *state*, not just a field, because
"am I allowed to touch this yet" is the question this whole app exists to keep
answerable.
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """Where an engagement currently sits."""

    #: Defining what's in and out of scope; not yet cleared to touch anything.
    SCOPING = "scoping"

    #: Authorization is in hand (see the engagement's ``authorization`` field);
    #: cleared to begin, not yet started.
    AUTHORIZED = "authorized"

    #: Testing in progress.
    ACTIVE = "active"

    #: Testing done, writing it up.
    REPORTING = "reporting"

    #: Delivered and done. The only terminal state.
    CLOSED = "closed"


class Severity(StrEnum):
    """How bad a finding is. CVSS-flavoured, without pretending to be CVSS."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Every status that isn't finished.
#:
#: **Derived, never hand-listed.** The benchhud projection and the web queue both
#: serve "open" from this set; a hand-maintained list is one forgotten line away
#: from hiding an engagement, which is indistinguishable from losing it.
OPEN_STATUSES: frozenset[Status] = frozenset(Status) - {Status.CLOSED}


def is_open(status: Status) -> bool:
    """True while there's still work to do on this engagement."""
    return status in OPEN_STATUSES


def to_status(value: Status | str) -> Status:
    """Coerce to a ``Status``, raising a readable error for anything unknown.

    Callers hand this strings — a web form posts them, the API takes them as
    query params — so coercion belongs at the boundary rather than in each caller.
    """
    try:
        return Status(value)
    except ValueError:
        known = ", ".join(s.value for s in Status)
        raise ValueError(f"unknown status {value!r}; expected one of: {known}") from None


def to_severity(value: Severity | str) -> Severity:
    """Coerce to a ``Severity``, raising a readable error for anything unknown."""
    try:
        return Severity(value)
    except ValueError:
        known = ", ".join(s.value for s in Severity)
        raise ValueError(f"unknown severity {value!r}; expected one of: {known}") from None
