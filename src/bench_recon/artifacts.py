"""Engagement artifacts — firmware dumps and captures, stored on disk beside the
database.

This is ``benchhud-intake``'s condition-photo store generalised from "images
only" to "any file": a recon artifact is a flash dump, a logic capture, a pcap, a
screenshot — arbitrary bytes, not a browser image. So the content check here does
not *gate* (anything non-empty under the cap is kept); it *sniffs*, to record an
honest content type and pick a sensible extension, and nothing more is read into
the type than the first few magic bytes.

**Everything here treats its inputs as hostile.** A ref arrives from a URL and a
filename arrives from a browser; both reach the filesystem on a box that also
holds sensitive engagement records. Two independent defences, exactly as intake
settled on:

1. Refs are validated against the known ``R26-0001`` format, and the stored
   filename is *generated* — the uploaded name is kept only as a display hint and
   never touches a path.
2. Every resulting path is resolved and confirmed to sit inside the artifact
   root, so an encoded separator or a symlink that slips past the first check
   still can't escape.

Either alone would probably do. Together they cover the case where the first has
a bug, which is the case worth planning for.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from bench_recon.refs import format_ref, parse_ref

#: Refuse anything larger. A firmware dump is kilobytes to a few tens of
#: megabytes; a logic or RF capture can run larger. 256 MB covers the realistic
#: cases with room to spare while keeping a Pi on an NFS mount from being filled
#: by one upload. Overridable for an outlier via the environment, parsed once.
_DEFAULT_MAX_BYTES = 256 * 1024 * 1024
MAX_BYTES_ENV_VAR = "BENCHRECON_ARTIFACT_MAX_BYTES"


def max_bytes() -> int:
    """The upload ceiling, from the environment or the default.

    A bad value falls back rather than crashing the upload path — the cap exists
    to protect the disk, and a typo in a deployment env var shouldn't be what
    stops an engagement being recorded.
    """
    raw = os.environ.get(MAX_BYTES_ENV_VAR, "")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_MAX_BYTES


#: Magic-byte prefixes we recognise, longest-prefix-first so a more specific
#: signature wins. The point is an honest ``content_type`` in the metadata and a
#: meaningful extension on disk, not validation — an unrecognised file is stored
#: all the same, as ``application/octet-stream`` with a ``.bin`` extension.
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x7fELF", "application/x-elf", ".elf"),
    (b"SQLite format 3\x00", "application/vnd.sqlite3", ".sqlite"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"PK\x03\x04", "application/zip", ".zip"),
    (b"\x1f\x8b", "application/gzip", ".gz"),
    (b"BZh", "application/x-bzip2", ".bz2"),
    (b"\xfd7zXZ\x00", "application/x-xz", ".xz"),
    (b"\xd4\xc3\xb2\xa1", "application/vnd.tcpdump.pcap", ".pcap"),
    (b"\xa1\xb2\xc3\xd4", "application/vnd.tcpdump.pcap", ".pcap"),
    (b"\x0a\x0d\x0d\x0a", "application/x-pcapng", ".pcapng"),
    (b"MZ", "application/x-dosexec", ".bin"),
)

_OCTET = ("application/octet-stream", ".bin")
_TEXT = ("text/plain", ".txt")


class ArtifactError(Exception):
    """Base for anything wrong with an upload."""


class EmptyArtifact(ArtifactError):
    """Nothing was uploaded — an empty file part."""


class ArtifactTooLarge(ArtifactError):
    """Bigger than :func:`max_bytes`."""


@dataclass(frozen=True)
class StoredArtifact:
    """What a successful save produced — the facts the index row needs.

    This is on-disk reality (the generated name, the sniffed type, the byte
    count); ``EngagementStore.record_artifact`` turns it into the metadata row,
    adding the engagement scope, the operator's ``kind`` and the original-name
    hint.
    """

    stored_name: str
    content_type: str
    size: int


def sniff(data: bytes) -> tuple[str, str]:
    """Best-effort ``(content_type, extension)`` from the leading bytes.

    Extension is a hint and content is the authority, same as intake's photos:
    a ``.bin`` that is really an ELF is recorded as an ELF, and a text file named
    ``.pcap`` is recorded as text. Unknown bytes are kept as a generic binary
    rather than refused — the whole point of this store is arbitrary artifacts.
    """
    for prefix, content_type, extension in _MAGIC:
        if data.startswith(prefix):
            return content_type, extension

    # No magic matched. If it decodes as UTF-8 it's almost certainly a log, a
    # note or a hexdump someone saved — record that honestly so it previews as
    # text rather than prompting a download.
    try:
        data[: 8 * 1024].decode("utf-8")
    except UnicodeDecodeError:
        return _OCTET
    return _TEXT


class ArtifactStore:
    """On-disk artifacts for engagements, under one root directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- writing ------------------------------------------------------------

    def save(self, ref: str, filename: str, data: bytes) -> StoredArtifact:
        """Store ``data`` against ``ref``.

        ``filename`` is only ever a hint — the stored name is generated, so a
        crafted upload name can't become a path, collide with another artifact,
        or overwrite anything. The caller records the metadata (and keeps
        ``filename`` as the display hint) once this returns.
        """
        if len(data) > max_bytes():
            # Checked before anything is written: rejecting after the fact would
            # make the cap useless as a disk guard.
            raise ArtifactTooLarge(f"artifact is larger than {max_bytes() // (1024 * 1024)} MB")
        if not data:
            # An empty part means nothing was chosen. A zero-byte artifact is
            # indistinguishable from a mistake and would just be noise in the log.
            raise EmptyArtifact("that file is empty")

        content_type, extension = sniff(data)

        directory = self._engagement_dir(ref)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{secrets.token_hex(8)}{extension}"
        target = self._inside(directory / name)
        target.write_bytes(data)

        return StoredArtifact(stored_name=name, content_type=content_type, size=len(data))

    # -- reading ------------------------------------------------------------

    def artifact_path(self, ref: str, name: str) -> Path:
        """Where a stored artifact lives on disk, with both inputs guarded."""
        return self._inside(self._engagement_dir(ref) / self._safe_name(name))

    def delete(self, ref: str, name: str) -> None:
        """Remove a stored artifact's bytes. A no-op when it's already gone.

        The caller removes the index row in the same breath; this is the file
        half. Order is the caller's call — an orphaned file is recoverable, an
        index row pointing at nothing is just a 404 on download.
        """
        self.artifact_path(ref, name).unlink(missing_ok=True)

    # -- guards -------------------------------------------------------------

    def _engagement_dir(self, ref: str) -> Path:
        """The directory for an engagement, with the ref validated first.

        Parsing the ref rather than sanitising it means only strings matching
        the real ``R26-0004`` shape ever reach a path — a far smaller surface
        than trying to enumerate everything hostile. The directory is named by
        the formatted ref so the on-disk layout reads the way the app does.
        """
        year, counter = parse_ref(ref)
        return self._inside(self.root / format_ref(year, counter))

    @staticmethod
    def _safe_name(name: str) -> str:
        """Require a single, plain path component.

        Deliberately refuses anything with a separator rather than reducing it to
        a basename. Quietly turning ``../../../etc/passwd`` into ``passwd`` would
        be *safe* but dishonest — the caller asked for one thing and got another,
        and a bug that produces the wrong file silently is worse than one that
        raises.
        """
        if not name or name in (".", "..") or "\x00" in name:
            raise ValueError(f"unusable artifact name: {name!r}")
        if "/" in name or "\\" in name or Path(name).name != name:
            raise ValueError(f"artifact name must be a single path component: {name!r}")
        return name

    def _inside(self, path: Path) -> Path:
        """Confirm a path really sits under the artifact root.

        The second of the two defences. Resolution catches what validation might
        miss — encoded separators, symlinks, anything that only becomes obvious
        once the OS has normalised it.
        """
        root = self.root.resolve()
        resolved = (root / path.relative_to(self.root)).resolve() if path.is_absolute() else path
        if not Path(resolved).resolve().is_relative_to(root):
            raise ValueError(f"path escapes the artifact root: {path}")
        return path
