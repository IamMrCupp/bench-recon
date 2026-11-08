"""Database schema and migration.

**Migrations use ``PRAGMA user_version`` and idempotent DDL, not Alembic.** A
handful of tables, a single writer, one deployment — Alembic would add a
dependency and a migrations tree for a problem this app doesn't have.
``user_version`` is a SQLite built-in that costs nothing and is easy to replace
later if the schema gets complicated.

**The CHECK constraints are deliberate duplication** of the Python enums in
``bench_recon.status`` — but not *hand*-duplicated: they're generated from the
same enums at import, so the two can't drift. The database is the layer that
survives a future script, a manual ``sqlite3`` session, or a bug in the API. For
a file holding engagement records, that's worth the redundancy.
"""

from __future__ import annotations

import sqlite3

from bench_recon.status import Severity, Status

SCHEMA_VERSION = 1


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_STATUSES = tuple(s.value for s in Status)
_SEVERITIES = tuple(s.value for s in Severity)


# An engagement is the top-level record: a job with a scope, an authorization,
# and a client it's for. Only `safe_title`, `target_class` and `status` are
# non-sensitive — those three (plus the ref) are all the benchhud projection ever
# sees. `client_ref`, `scope` and `authorization` never leave this app.
_CREATE_ENGAGEMENTS = f"""
CREATE TABLE IF NOT EXISTS engagements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Refs render as R<YY>-<NNNN> ("R26-0004"). Stored as integers because the
    -- zero-padding is presentation: formatting on the way out means engagement
    -- 10000 widens gracefully instead of becoming a parsing bug.
    year          INTEGER NOT NULL,
    counter       INTEGER NOT NULL,

    -- Operator-set, explicitly NON-sensitive: this is the handle that shows on
    -- the HUD and may be streamed. "Blue sedan head unit", never a client name.
    safe_title    TEXT    NOT NULL,

    -- A category, not an identity: "automotive-ecu", "iot-camera". Also projected.
    target_class  TEXT    NOT NULL,

    -- SENSITIVE. The client this engagement is for. Never projected.
    client_ref    TEXT,

    -- SENSITIVE. What's in and out of bounds, and the authorization/consent that
    -- makes touching it legal. First-class fields because "am I allowed" is the
    -- question this app exists to answer — not a compliance engine, just data.
    scope         TEXT,
    authorization TEXT,

    status        TEXT    NOT NULL,

    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,

    -- Counters restart each January, so uniqueness is the pair. This is also what
    -- makes ref allocation safe under concurrency: the database rejects a
    -- collision rather than the application hoping it read a fresh MAX().
    UNIQUE (year, counter),

    CONSTRAINT known_status CHECK (status IN ({_quoted(_STATUSES)}))
);
"""

_CREATE_ENGAGEMENTS_STATUS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_engagements_status ON engagements (status);"
)

# A monotonic sequence per year, deliberately separate from the engagements
# table. The obvious alternative — MAX(counter)+1 — reuses a ref if the highest
# engagement is ever deleted. Refs get written into reports and authorization
# letters, so a reused one points at the wrong target. A counter that only ever
# climbs can't do that, and it makes allocation atomic as a side effect.
_CREATE_REF_COUNTERS = """
CREATE TABLE IF NOT EXISTS ref_counters (
    year         INTEGER PRIMARY KEY,
    last_counter INTEGER NOT NULL DEFAULT 0
);
"""

# A device under test. All of it is SENSITIVE — make/model/firmware/serial and
# the pinout notes are target identity, and none of it crosses to benchhud.
_CREATE_TARGETS = """
CREATE TABLE IF NOT EXISTS targets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id    INTEGER NOT NULL REFERENCES engagements (id) ON DELETE CASCADE,
    make             TEXT,
    model            TEXT,
    firmware_version TEXT,
    -- Free text: "UART (3V3) J4, SWD TC2050, SPI flash W25Q128".
    interfaces       TEXT,
    pinout_notes     TEXT,
    serial           TEXT,
    created_at       TEXT    NOT NULL,

    -- A target needs *some* identifier; one of make/model must be present. Blank
    -- both and the row is just noise against an engagement.
    CONSTRAINT identified CHECK (make IS NOT NULL OR model IS NOT NULL)
);
"""

_CREATE_TARGETS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_targets_engagement ON targets (engagement_id);"
)

# The findings log. SENSITIVE, and **append-only**: there is no update or delete
# path in the store, by design — a findings log you can quietly edit isn't a log.
# Corrections are new entries.
_CREATE_FINDINGS = f"""
CREATE TABLE IF NOT EXISTS findings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id INTEGER NOT NULL REFERENCES engagements (id) ON DELETE CASCADE,
    severity      TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    body          TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,

    CONSTRAINT known_severity CHECK (severity IN ({_quoted(_SEVERITIES)}))
);
"""

_CREATE_FINDINGS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_findings_engagement ON findings (engagement_id);"
)

# Artifact *metadata*. The blobs (firmware dumps, captures) live on disk beside
# the DB, stored traversal-proof by artifacts.ArtifactStore. This table
# is the index of what's been captured. SENSITIVE: even names and sizes are, so
# only names/sizes/kind/status ever reach benchhud, never contents.
_CREATE_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS artifacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id INTEGER NOT NULL REFERENCES engagements (id) ON DELETE CASCADE,
    -- The generated on-disk filename (never the client's). Unique per engagement.
    stored_name   TEXT    NOT NULL,
    -- The uploaded name, kept only as a display hint.
    original_name TEXT,
    content_type  TEXT    NOT NULL,
    size          INTEGER NOT NULL,
    -- "firmware-dump", "capture", "photo", "other" — free text, operator-set.
    kind          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,

    UNIQUE (engagement_id, stored_name)
);
"""

_CREATE_ARTIFACTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_artifacts_engagement ON artifacts (engagement_id);"
)


def initialise(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to call on every startup.

    Base tables use ``IF NOT EXISTS`` (idempotent create). A database already at
    the current version short-circuits. There's no downgrade path — rolling a
    schema back is a restore-from-backup problem, not a migration one.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return

    with conn:
        conn.execute(_CREATE_ENGAGEMENTS)
        conn.execute(_CREATE_ENGAGEMENTS_STATUS_INDEX)
        conn.execute(_CREATE_REF_COUNTERS)
        conn.execute(_CREATE_TARGETS)
        conn.execute(_CREATE_TARGETS_INDEX)
        conn.execute(_CREATE_FINDINGS)
        conn.execute(_CREATE_FINDINGS_INDEX)
        conn.execute(_CREATE_ARTIFACTS)
        conn.execute(_CREATE_ARTIFACTS_INDEX)

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
