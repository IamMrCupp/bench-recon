"""Schema creation, versioning, and the database-level guards."""

import sqlite3

import pytest

from bench_recon.db import connect
from bench_recon.schema import SCHEMA_VERSION, initialise
from bench_recon.status import Severity, Status


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_initialise_creates_every_table(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    initialise(conn)
    assert {"engagements", "targets", "findings", "artifacts", "ref_counters"} <= _tables(conn)


def test_initialise_sets_the_version(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    initialise(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_initialise_is_idempotent(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    initialise(conn)
    initialise(conn)  # must not raise or duplicate
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_status_check_rejects_unknown_status(conn) -> None:
    conn.execute(
        "INSERT INTO engagements (year, counter, safe_title, target_class, status,"
        " created_at, updated_at) VALUES (26, 1, 't', 'iot', 'active', 'x', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO engagements (year, counter, safe_title, target_class, status,"
            " created_at, updated_at) VALUES (26, 2, 't', 'iot', 'pwned', 'x', 'x')"
        )


def test_status_check_matches_the_enum(conn) -> None:
    """The DB accepts exactly the enum's values — no drift between them."""
    for i, s in enumerate(Status, start=1):
        conn.execute(
            "INSERT INTO engagements (year, counter, safe_title, target_class, status,"
            " created_at, updated_at) VALUES (26, ?, 't', 'iot', ?, 'x', 'x')",
            (i, s.value),
        )


def test_severity_check_matches_the_enum(conn) -> None:
    conn.execute(
        "INSERT INTO engagements (id, year, counter, safe_title, target_class, status,"
        " created_at, updated_at) VALUES (1, 26, 1, 't', 'iot', 'active', 'x', 'x')"
    )
    for s in Severity:
        conn.execute(
            "INSERT INTO findings (engagement_id, severity, title, created_at)"
            " VALUES (1, ?, 'f', 'x')",
            (s.value,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO findings (engagement_id, severity, title, created_at)"
            " VALUES (1, 'spicy', 'f', 'x')"
        )


def test_target_requires_make_or_model(conn) -> None:
    conn.execute(
        "INSERT INTO engagements (id, year, counter, safe_title, target_class, status,"
        " created_at, updated_at) VALUES (1, 26, 1, 't', 'iot', 'active', 'x', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO targets (engagement_id, created_at) VALUES (1, 'x')")


def test_unique_year_counter(conn) -> None:
    conn.execute(
        "INSERT INTO engagements (year, counter, safe_title, target_class, status,"
        " created_at, updated_at) VALUES (26, 1, 't', 'iot', 'active', 'x', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO engagements (year, counter, safe_title, target_class, status,"
            " created_at, updated_at) VALUES (26, 1, 'u', 'iot', 'active', 'x', 'x')"
        )


def test_foreign_keys_cascade_on_engagement_delete(conn) -> None:
    conn.execute(
        "INSERT INTO engagements (id, year, counter, safe_title, target_class, status,"
        " created_at, updated_at) VALUES (1, 26, 1, 't', 'iot', 'active', 'x', 'x')"
    )
    conn.execute("INSERT INTO targets (engagement_id, model, created_at) VALUES (1, 'm', 'x')")
    conn.execute(
        "INSERT INTO findings (engagement_id, severity, title, created_at)"
        " VALUES (1, 'low', 'f', 'x')"
    )
    conn.execute("DELETE FROM engagements WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
