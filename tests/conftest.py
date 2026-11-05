"""Shared test fixtures for the store layer.

A fresh on-disk SQLite database per test (``tmp_path``), initialised to the
current schema, with the store's year pinned to 26 so refs are predictable
(``R26-0001`` …). On-disk rather than ``:memory:`` because the production concern
— NFS pragmas, a connection per request — is about a real file; a memory database
would quietly hide a file-path bug.
"""

import pytest

from bench_recon.db import connect
from bench_recon.schema import initialise
from bench_recon.store import EngagementStore


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "bench-recon.db"
    connection = connect(path)
    initialise(connection)
    yield connection
    connection.close()


@pytest.fixture
def store(conn):
    return EngagementStore(conn, year=26)
