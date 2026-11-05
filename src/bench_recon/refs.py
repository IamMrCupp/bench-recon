"""Engagement references — ``R26-0004``.

An ``R``, two digits of year, a dash, then a counter that restarts each January.
The ``R`` prefix is deliberate: it distinguishes a recon engagement ref from a
benchhud-intake *repair* ticket ref (``26-0007``) at a glance, since both can end
up written on tape or spoken at the same bench.

Padding is **presentation only**. Year and counter are stored as integers, so the
four-digit width is a formatting choice rather than a limit — engagement 10000
simply renders wider.
"""

from __future__ import annotations

import re
import sqlite3

_COUNTER_WIDTH = 4

# R, two-digit year, dash, at least one digit. Deliberately not anchored to
# exactly four digits: parsing must keep working past 9999.
_REF_PATTERN = re.compile(r"^R(\d{2})-(\d+)$")


def format_ref(year: int, counter: int) -> str:
    """Render a ref for a label, a report, or a URL."""
    return f"R{year:02d}-{counter:0{_COUNTER_WIDTH}d}"


def parse_ref(ref: str) -> tuple[int, int]:
    """Parse ``R26-0004`` into ``(26, 4)``.

    Surrounding whitespace is tolerated because refs get typed by hand and pasted
    out of messages. Anything else is an error rather than a guess — silently
    accepting a malformed ref means looking up the wrong engagement.
    """
    match = _REF_PATTERN.match(ref.strip())
    if match is None:
        raise ValueError(f"malformed engagement ref: {ref!r}")

    year, counter = int(match.group(1)), int(match.group(2))
    if counter < 1:
        raise ValueError(f"engagement refs are 1-based, got: {ref!r}")

    return year, counter


def allocate_counter(conn: sqlite3.Connection, year: int) -> int:
    """Claim the next counter for ``year``, atomically.

    Backed by ``ref_counters``, which only ever climbs. Two properties matter:

    - **No reuse.** Deleting an engagement doesn't free its number. A ref that's
      been written into a report or an authorization letter must never point at a
      different target later.
    - **No race.** The increment is a single UPDATE, so there's no read-then-write
      gap for a concurrent caller to slip into. The deployment is single-writer
      today, but "unlikely" and "impossible" are different things when the failure
      mode is a misfiled finding.
    """
    with conn:
        conn.execute("INSERT OR IGNORE INTO ref_counters (year) VALUES (?)", (year,))
        row = conn.execute(
            "UPDATE ref_counters SET last_counter = last_counter + 1"
            " WHERE year = ? RETURNING last_counter",
            (year,),
        ).fetchone()

    return row["last_counter"]
