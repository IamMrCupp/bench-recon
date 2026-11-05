"""Connection configuration — the NFS-safety pragmas and foreign keys."""

from bench_recon.db import NFS_ENV_VAR, connect


def test_foreign_keys_are_on(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_rows_are_accessible_by_name(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    row = conn.execute("SELECT 7 AS answer").fetchone()
    assert row["answer"] == 7


def test_local_dev_uses_wal(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(NFS_ENV_VAR, raising=False)
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_nfs_uses_truncate(tmp_path, monkeypatch) -> None:
    """WAL corrupts on NFS; under the flag the journal must be TRUNCATE instead."""
    monkeypatch.setenv(NFS_ENV_VAR, "1")
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "truncate"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL


def test_nfs_flag_zero_means_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(NFS_ENV_VAR, "0")
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
