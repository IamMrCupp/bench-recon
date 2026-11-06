"""The on-disk artifact store.

Two things matter here and both are security properties: a crafted ref or name
can never write or read outside the artifact root, and the size cap actually
bounds what reaches the disk. The content sniff is a convenience (honest
metadata), not a gate — any non-empty file under the cap is kept.
"""

from __future__ import annotations

import pytest

from bench_recon.artifacts import (
    ArtifactStore,
    ArtifactTooLarge,
    EmptyArtifact,
    max_bytes,
    sniff,
)


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts")


# -- sniffing ---------------------------------------------------------------


def test_sniff_recognises_elf() -> None:
    content_type, extension = sniff(b"\x7fELF\x02\x01\x01\x00rest of a firmware image")
    assert content_type == "application/x-elf"
    assert extension == ".elf"


def test_sniff_recognises_png() -> None:
    assert sniff(b"\x89PNG\r\n\x1a\n...") == ("image/png", ".png")


def test_sniff_calls_utf8_text_text() -> None:
    assert sniff(b"boot log line 1\nboot log line 2\n") == ("text/plain", ".txt")


def test_sniff_falls_back_to_octet_stream() -> None:
    # High bytes that aren't valid UTF-8 and match no signature.
    assert sniff(b"\x00\x01\x92\xfe\xff\xab\xcd") == ("application/octet-stream", ".bin")


# -- saving -----------------------------------------------------------------


def test_save_writes_the_bytes_and_reports_metadata(store) -> None:
    stored = store.save("R26-0001", "dump.bin", b"\x7fELFpayload")

    assert stored.stored_name.endswith(".elf")  # sniffed, not from the hint name
    assert stored.content_type == "application/x-elf"
    assert stored.size == len(b"\x7fELFpayload")
    assert store.artifact_path("R26-0001", stored.stored_name).read_bytes() == b"\x7fELFpayload"


def test_save_generates_unique_names(store) -> None:
    a = store.save("R26-0001", "x", b"first capture")
    b = store.save("R26-0001", "x", b"second capture")
    assert a.stored_name != b.stored_name


def test_save_rejects_empty(store) -> None:
    with pytest.raises(EmptyArtifact):
        store.save("R26-0001", "empty", b"")


def test_save_rejects_oversize(store, monkeypatch) -> None:
    monkeypatch.setenv("BENCHRECON_ARTIFACT_MAX_BYTES", "8")
    assert max_bytes() == 8
    with pytest.raises(ArtifactTooLarge):
        store.save("R26-0001", "big", b"this is more than eight bytes")


def test_save_rejects_a_malformed_ref(store) -> None:
    # A bad ref never reaches a path — parse_ref raises first.
    with pytest.raises(ValueError, match="malformed"):
        store.save("not-a-ref", "x", b"data")


# -- path guards ------------------------------------------------------------


@pytest.mark.parametrize("name", ["../escape", "a/b", "..", ".", "", "x\x00y", "\\windows"])
def test_artifact_path_refuses_traversal(store, name) -> None:
    with pytest.raises(ValueError):
        store.artifact_path("R26-0001", name)


def test_artifact_path_stays_under_root(store) -> None:
    stored = store.save("R26-0001", "x", b"data")
    path = store.artifact_path("R26-0001", stored.stored_name)
    assert path.resolve().is_relative_to(store.root.resolve())


# -- deletion ---------------------------------------------------------------


def test_delete_removes_the_file(store) -> None:
    stored = store.save("R26-0001", "x", b"data")
    path = store.artifact_path("R26-0001", stored.stored_name)
    assert path.is_file()

    store.delete("R26-0001", stored.stored_name)
    assert not path.exists()


def test_delete_is_a_noop_when_absent(store) -> None:
    store.delete("R26-0001", "deadbeefdeadbeef.bin")  # never created — must not raise
