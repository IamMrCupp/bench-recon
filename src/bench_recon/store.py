"""Reading and writing engagements, targets, findings and artifacts.

This is the layer the web UI and the benchhud API both sit on, which is why
validation lives here rather than in a form. A rule enforced only in the web form
isn't a rule — the API, a migration script and a future import job are all callers
too.

``Engagement`` here is the store's own shape and a **superset** of what benchhud
is allowed to see. The projection in ``bench_recon.projection`` reduces it to the
four ref-safe fields; everything this module exposes that the projection omits —
``client_ref``, ``scope``, ``authorization``, and every target/finding/artifact —
is a thing that must never reach a machine that composites to a video stream.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from bench_recon.refs import allocate_counter, format_ref, parse_ref
from bench_recon.status import Severity, Status, to_severity, to_status

#: Engagement details that can be edited after the fact.
#:
#: ``status`` is deliberately absent — it moves through ``set_status``, the one
#: guarded path. Letting it through here would be a second, unguarded route to a
#: closed engagement.
_EDITABLE = frozenset({"safe_title", "target_class", "client_ref", "scope", "authorization"})

_ENGAGEMENT_COLUMNS = (
    "year, counter, safe_title, target_class, client_ref, scope, authorization,"
    " status, created_at, updated_at"
)

_TARGET_EDITABLE = frozenset(
    {"make", "model", "firmware_version", "interfaces", "pinout_notes", "serial"}
)


@dataclass(frozen=True)
class Engagement:
    """One job, as the store sees it. A superset of the benchhud projection."""

    ref: str
    safe_title: str
    target_class: str
    status: Status
    created_at: str
    updated_at: str
    client_ref: str | None = None
    scope: str | None = None
    authorization: str | None = None


@dataclass(frozen=True)
class Target:
    """A device under test. Every field is sensitive."""

    id: int
    make: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    interfaces: str | None = None
    pinout_notes: str | None = None
    serial: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class Finding:
    """One entry in the append-only findings log."""

    id: int
    severity: Severity
    title: str
    body: str
    created_at: str


@dataclass(frozen=True)
class Artifact:
    """Metadata for one on-disk artifact (firmware dump, capture, photo)."""

    id: int
    stored_name: str
    content_type: str
    size: int
    kind: str
    created_at: str
    original_name: str | None = None


def _now() -> str:
    """UTC, ISO-8601, second precision. SQLite has no datetime type; one sortable
    string format beats letting each caller pick one."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clean(value: str | None) -> str | None:
    """Normalise blank-ish input to None. A form posts '' where a script posts None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _row_to_engagement(row: sqlite3.Row) -> Engagement:
    return Engagement(
        ref=format_ref(row["year"], row["counter"]),
        safe_title=row["safe_title"],
        target_class=row["target_class"],
        status=Status(row["status"]),
        client_ref=row["client_ref"],
        scope=row["scope"],
        authorization=row["authorization"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_target(row: sqlite3.Row) -> Target:
    return Target(
        id=row["id"],
        make=row["make"],
        model=row["model"],
        firmware_version=row["firmware_version"],
        interfaces=row["interfaces"],
        pinout_notes=row["pinout_notes"],
        serial=row["serial"],
        created_at=row["created_at"],
    )


class EngagementStore:
    """Engagement persistence for one recon year."""

    def __init__(self, conn: sqlite3.Connection, year: int | None = None) -> None:
        self.conn = conn
        #: Injectable so tests don't depend on the wall clock, and so a January
        #: rollover can be exercised without waiting for January.
        self.year = year if year is not None else datetime.now(UTC).year % 100

    # -- engagements: writing ----------------------------------------------

    def create_engagement(
        self,
        *,
        safe_title: str,
        target_class: str,
        client_ref: str | None = None,
        scope: str | None = None,
        authorization: str | None = None,
    ) -> Engagement:
        """Open a new engagement and give it a ref."""
        safe_title = (safe_title or "").strip()
        target_class = (target_class or "").strip()

        # Validate before allocating: a rejected create shouldn't burn a ref and
        # leave a hole in the numbering.
        if not safe_title:
            raise ValueError("safe_title is required (a non-sensitive handle for the HUD)")
        if not target_class:
            raise ValueError("target_class is required (e.g. 'automotive-ecu')")

        counter = allocate_counter(self.conn, self.year)
        stamp = _now()

        with self.conn:
            self.conn.execute(
                f"INSERT INTO engagements ({_ENGAGEMENT_COLUMNS}) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.year,
                    counter,
                    safe_title,
                    target_class,
                    _clean(client_ref),
                    _clean(scope),
                    _clean(authorization),
                    Status.SCOPING.value,
                    stamp,
                    stamp,
                ),
            )

        return self.get_by_ref(format_ref(self.year, counter))  # type: ignore[return-value]

    def set_status(self, ref: str, status: Status | str) -> Engagement:
        """Move an engagement through its lifecycle.

        The graph is deliberately permissive: scope gets revisited, an authorized
        job stalls, a closed one reopens. What's enforced is that the status is a
        known one, not the order.
        """
        self._require(ref)
        status = to_status(status)

        year, counter = parse_ref(ref)
        with self.conn:
            self.conn.execute(
                "UPDATE engagements SET status = ?, updated_at = ? WHERE year = ? AND counter = ?",
                (status.value, _now(), year, counter),
            )

        return self.get_by_ref(ref)  # type: ignore[return-value]

    def update_engagement(self, ref: str, **fields: str | None) -> Engagement:
        """Edit engagement details after the fact.

        Only the fields in ``_EDITABLE`` — status changes go through ``set_status``
        so there's exactly one guarded path to a closed engagement. An unknown
        field name raises rather than being ignored, so a typo'd kwarg can't
        silently do nothing.
        """
        current = self._require(ref)

        unknown = set(fields) - _EDITABLE
        if unknown:
            raise ValueError(f"not editable: {', '.join(sorted(unknown))}")
        if not fields:
            return current

        # Re-check the invariants the record was created under, against the
        # post-edit values.
        def after(name: str, existing: str | None) -> str | None:
            return fields.get(name, existing)

        safe_title = (after("safe_title", current.safe_title) or "").strip()
        target_class = (after("target_class", current.target_class) or "").strip()
        if not safe_title:
            raise ValueError("safe_title is required")
        if not target_class:
            raise ValueError("target_class is required")

        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_clean(v) if isinstance(v, str) or v is None else v for v in fields.values()]
        year, counter = parse_ref(ref)

        with self.conn:
            self.conn.execute(
                f"UPDATE engagements SET {assignments}, updated_at = ?"
                " WHERE year = ? AND counter = ?",
                (*values, _now(), year, counter),
            )

        return self.get_by_ref(ref)  # type: ignore[return-value]

    # -- engagements: reading ----------------------------------------------

    def get_by_ref(self, ref: str) -> Engagement | None:
        """Look up one engagement. ``None`` when it doesn't exist.

        A miss is a normal outcome, not an error. A *malformed* ref is different:
        that's a caller bug, and silently returning ``None`` would hide it.
        """
        year, counter = parse_ref(ref)
        row = self.conn.execute(
            f"SELECT {_ENGAGEMENT_COLUMNS} FROM engagements WHERE year = ? AND counter = ?",
            (year, counter),
        ).fetchone()

        return _row_to_engagement(row) if row else None

    def list_engagements(
        self,
        *,
        status: Status | str | None = None,
        open_only: bool = False,
    ) -> list[Engagement]:
        """Engagements, newest first.

        ``open_only`` is built from ``OPEN_STATUSES``, which derives itself from
        the enum, so a newly added status can't quietly drop out of the queue.
        """
        from bench_recon.status import OPEN_STATUSES

        sql = f"SELECT {_ENGAGEMENT_COLUMNS} FROM engagements"
        params: list[object] = []

        if status is not None:
            sql += " WHERE status = ?"
            params.append(to_status(status).value)
        elif open_only:
            marks = ", ".join("?" * len(OPEN_STATUSES))
            sql += f" WHERE status IN ({marks})"
            params.extend(sorted(s.value for s in OPEN_STATUSES))

        sql += " ORDER BY year DESC, counter DESC"
        return [_row_to_engagement(row) for row in self.conn.execute(sql, params)]

    # -- internals ----------------------------------------------------------

    def _require(self, ref: str) -> Engagement:
        """Fetch an engagement that must exist. Reading a missing one is normal
        (``get_by_ref`` returns ``None``); *writing* to one is a caller bug."""
        engagement = self.get_by_ref(ref)
        if engagement is None:
            raise LookupError(f"no such engagement: {ref}")
        return engagement

    def _engagement_id(self, ref: str) -> int:
        """The surrogate id for a ref. Raises ``LookupError`` if it's gone."""
        year, counter = parse_ref(ref)
        row = self.conn.execute(
            "SELECT id FROM engagements WHERE year = ? AND counter = ?",
            (year, counter),
        ).fetchone()
        if row is None:
            raise LookupError(f"no such engagement: {ref}")
        return row["id"]

    # -- targets ------------------------------------------------------------

    def add_target(
        self,
        ref: str,
        *,
        make: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        interfaces: str | None = None,
        pinout_notes: str | None = None,
        serial: str | None = None,
    ) -> Target:
        """Add a device under test to an engagement."""
        engagement_id = self._engagement_id(ref)
        make, model = _clean(make), _clean(model)
        if make is None and model is None:
            raise ValueError("a target needs at least one of make or model")

        stamp = _now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO targets"
                " (engagement_id, make, model, firmware_version, interfaces, pinout_notes,"
                " serial, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    engagement_id,
                    make,
                    model,
                    _clean(firmware_version),
                    _clean(interfaces),
                    _clean(pinout_notes),
                    _clean(serial),
                    stamp,
                ),
            )
        return self.get_target(ref, cur.lastrowid)  # type: ignore[return-value]

    def list_targets(self, ref: str) -> list[Target]:
        """An engagement's targets, oldest first."""
        engagement_id = self._engagement_id(ref)
        rows = self.conn.execute(
            "SELECT id, make, model, firmware_version, interfaces, pinout_notes, serial,"
            " created_at FROM targets WHERE engagement_id = ? ORDER BY id",
            (engagement_id,),
        )
        return [_row_to_target(r) for r in rows]

    def get_target(self, ref: str, target_id: int) -> Target | None:
        """One target by id, scoped to its engagement. ``None`` when absent."""
        engagement_id = self._engagement_id(ref)
        row = self.conn.execute(
            "SELECT id, make, model, firmware_version, interfaces, pinout_notes, serial,"
            " created_at FROM targets WHERE id = ? AND engagement_id = ?",
            (target_id, engagement_id),
        ).fetchone()
        return _row_to_target(row) if row else None

    def update_target(self, ref: str, target_id: int, **fields: str | None) -> Target:
        """Edit a target's fields. Unknown field names raise."""
        current = self.get_target(ref, target_id)
        if current is None:
            raise LookupError(f"no such target: {target_id} on {ref}")

        unknown = set(fields) - _TARGET_EDITABLE
        if unknown:
            raise ValueError(f"not editable: {', '.join(sorted(unknown))}")
        if not fields:
            return current

        make = _clean(fields.get("make", current.make))
        model = _clean(fields.get("model", current.model))
        if make is None and model is None:
            raise ValueError("a target needs at least one of make or model")

        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_clean(v) for v in fields.values()]
        engagement_id = self._engagement_id(ref)
        with self.conn:
            self.conn.execute(
                f"UPDATE targets SET {assignments} WHERE id = ? AND engagement_id = ?",
                (*values, target_id, engagement_id),
            )
        return self.get_target(ref, target_id)  # type: ignore[return-value]

    def remove_target(self, ref: str, target_id: int) -> None:
        """Remove a target. Removing one that isn't there is a no-op, not an error."""
        engagement_id = self._engagement_id(ref)
        with self.conn:
            self.conn.execute(
                "DELETE FROM targets WHERE id = ? AND engagement_id = ?",
                (target_id, engagement_id),
            )

    # -- findings (append-only) --------------------------------------------

    def add_finding(
        self, ref: str, *, severity: Severity | str, title: str, body: str = ""
    ) -> Finding:
        """Append a finding. There is no update or delete — a findings log you can
        quietly edit isn't a log. Corrections are new entries."""
        engagement_id = self._engagement_id(ref)
        title = (title or "").strip()
        if not title:
            raise ValueError("a finding needs a title")
        severity = to_severity(severity)

        stamp = _now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO findings (engagement_id, severity, title, body, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (engagement_id, severity.value, title, (body or "").strip(), stamp),
            )
        return Finding(
            id=cur.lastrowid,
            severity=severity,
            title=title,
            body=(body or "").strip(),
            created_at=stamp,
        )

    def list_findings(self, ref: str) -> list[Finding]:
        """An engagement's findings, oldest first — the log reads as a timeline."""
        engagement_id = self._engagement_id(ref)
        rows = self.conn.execute(
            "SELECT id, severity, title, body, created_at FROM findings"
            " WHERE engagement_id = ? ORDER BY id",
            (engagement_id,),
        )
        return [
            Finding(
                id=r["id"],
                severity=Severity(r["severity"]),
                title=r["title"],
                body=r["body"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- artifacts (metadata; the on-disk blob store lands with the web UI) --

    def record_artifact(
        self,
        ref: str,
        *,
        stored_name: str,
        content_type: str,
        size: int,
        kind: str,
        original_name: str | None = None,
    ) -> Artifact:
        """Record that an artifact was stored for an engagement.

        This writes the index row only; the caller is responsible for the bytes on
        disk (the traversal-proof artifact store lands with the web UI). Kept
        together in one transaction there so a row can't outlive its file.
        """
        engagement_id = self._engagement_id(ref)
        stored_name = (stored_name or "").strip()
        kind = (kind or "").strip() or "other"
        if not stored_name:
            raise ValueError("an artifact needs a stored_name")
        if size < 0:
            raise ValueError("artifact size cannot be negative")

        stamp = _now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO artifacts"
                " (engagement_id, stored_name, original_name, content_type, size, kind, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    engagement_id,
                    stored_name,
                    _clean(original_name),
                    (content_type or "application/octet-stream").strip(),
                    size,
                    kind,
                    stamp,
                ),
            )
        return self.get_artifact(ref, cur.lastrowid)  # type: ignore[return-value]

    def list_artifacts(self, ref: str) -> list[Artifact]:
        """An engagement's artifacts, oldest first."""
        engagement_id = self._engagement_id(ref)
        rows = self.conn.execute(
            "SELECT id, stored_name, original_name, content_type, size, kind, created_at"
            " FROM artifacts WHERE engagement_id = ? ORDER BY id",
            (engagement_id,),
        )
        return [self._row_to_artifact(r) for r in rows]

    def get_artifact(self, ref: str, artifact_id: int) -> Artifact | None:
        """One artifact by id, scoped to its engagement. ``None`` when absent."""
        engagement_id = self._engagement_id(ref)
        row = self.conn.execute(
            "SELECT id, stored_name, original_name, content_type, size, kind, created_at"
            " FROM artifacts WHERE id = ? AND engagement_id = ?",
            (artifact_id, engagement_id),
        ).fetchone()
        return self._row_to_artifact(row) if row else None

    def remove_artifact(self, ref: str, artifact_id: int) -> None:
        """Remove an artifact's index row. A no-op when it's already gone."""
        engagement_id = self._engagement_id(ref)
        with self.conn:
            self.conn.execute(
                "DELETE FROM artifacts WHERE id = ? AND engagement_id = ?",
                (artifact_id, engagement_id),
            )

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=row["id"],
            stored_name=row["stored_name"],
            original_name=row["original_name"],
            content_type=row["content_type"],
            size=row["size"],
            kind=row["kind"],
            created_at=row["created_at"],
        )
