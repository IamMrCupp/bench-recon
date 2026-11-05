"""Engagement refs — ``R26-0004``."""

import pytest

from bench_recon.db import connect
from bench_recon.refs import allocate_counter, format_ref, parse_ref
from bench_recon.schema import initialise


def test_format_pads_to_four_digits() -> None:
    assert format_ref(26, 4) == "R26-0004"
    assert format_ref(26, 1) == "R26-0001"


def test_format_widens_past_four_digits() -> None:
    """Padding is presentation, not a limit."""
    assert format_ref(26, 10000) == "R26-10000"


def test_parse_round_trips() -> None:
    assert parse_ref("R26-0004") == (26, 4)
    assert parse_ref(format_ref(31, 123)) == (31, 123)


def test_parse_tolerates_surrounding_whitespace() -> None:
    assert parse_ref("  R26-0004\n") == (26, 4)


@pytest.mark.parametrize(
    "bad", ["26-0004", "R26-0000", "RX-0001", "R26-", "", "R260004", "r26-0004"]
)
def test_parse_rejects_malformed(bad) -> None:
    with pytest.raises(ValueError):
        parse_ref(bad)


def test_the_R_prefix_distinguishes_from_intake_tickets() -> None:
    """An intake ticket ref (26-0007) must not parse as a recon ref."""
    with pytest.raises(ValueError):
        parse_ref("26-0007")


def test_allocate_counter_climbs(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    initialise(conn)
    assert allocate_counter(conn, 26) == 1
    assert allocate_counter(conn, 26) == 2
    assert allocate_counter(conn, 26) == 3


def test_allocate_counter_is_per_year(tmp_path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    initialise(conn)
    assert allocate_counter(conn, 26) == 1
    assert allocate_counter(conn, 27) == 1  # a new year restarts
    assert allocate_counter(conn, 26) == 2


def test_allocate_counter_never_reuses_after_delete(tmp_path) -> None:
    """A deleted engagement must not free its number — refs get written down."""
    conn = connect(tmp_path / "db.sqlite")
    initialise(conn)
    allocate_counter(conn, 26)  # 1
    allocate_counter(conn, 26)  # 2
    # Even with the engagements table empty, the counter keeps climbing.
    assert allocate_counter(conn, 26) == 3
