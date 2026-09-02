"""The Business Master File loader, against 49 real BMF rows.

The fixture is a slice of the real ``eo1.csv`` carried over from grantcheck with its
provenance sidecar (``bmf-sample.csv.source.json``): the named verification organizations,
group-exemption subordinates, private foundations, and rows whose quoted fields contain
commas. Parsing is grantcheck's; what is tested here is what this loader adds - the
normalized names, ZIP5, the blocking keys, and idempotent reloads.

Skips, rather than fails, when the ``grantcheck`` dependency is not installed in the
environment running the tests.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("grantcheck")

from funder_graph.resolve.bmf import bmf_count, load_bmf

FIXTURE = Path(__file__).parent / "fixtures" / "bmf" / "bmf-sample.csv"
FEEDING_AMERICA = "363673599"


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def loaded(conn):
    result = load_bmf(conn, FIXTURE.read_text(encoding="utf-8"), vintage="2026-08")
    return conn, result


class TestLoad:
    def test_every_fixture_row_loads_and_nothing_is_quarantined(self, loaded) -> None:
        conn, result = loaded
        assert result.rows == 49
        # The real file carries EIN 000019818 twice (PALMER SECOND BAPTIST CHURCH, MA); one
        # row of the pair is kept, and the count says so rather than hiding it.
        assert result.organizations == 48
        assert result.quarantined == 0
        assert result.vintage == "2026-08"
        assert bmf_count(conn) == 48

    def test_feeding_america_with_the_derived_columns(self, loaded) -> None:
        conn, _ = loaded
        row = conn.execute(
            "SELECT name, city, state, zip, zip5, subsection, ntee_cd, name_normalized, "
            "first_token FROM bmf WHERE ein = ?",
            [FEEDING_AMERICA],
        ).fetchone()
        assert row is not None
        name, city, state, zip_, zip5, subsection, _ntee, normalized, token = row
        assert name == "FEEDING AMERICA"
        assert (city, state) == ("CHICAGO", "IL")
        assert zip_ == "60601-3389" and zip5 == "60601"
        assert subsection == "03"
        assert normalized == "FEEDING AMERICA"
        assert token == "FEEDING"

    def test_reloading_a_vintage_replaces_it(self, loaded) -> None:
        conn, _ = loaded
        again = load_bmf(conn, FIXTURE.read_text(encoding="utf-8"), vintage="2026-08")
        assert again.organizations == 48
        assert bmf_count(conn) == 48  # not 96

    def test_a_second_vintage_coexists(self, loaded) -> None:
        conn, _ = loaded
        load_bmf(conn, FIXTURE.read_text(encoding="utf-8"), vintage="2026-09")
        (n,) = conn.execute("SELECT COUNT(DISTINCT vintage) FROM bmf").fetchone()
        # INSERT OR REPLACE on the EIN key: the newer vintage's row wins for each EIN.
        assert n == 1
        (v,) = conn.execute("SELECT DISTINCT vintage FROM bmf").fetchone()
        assert v == "2026-09"


class TestBlocking:
    def test_exact_normalized_name_block(self, loaded) -> None:
        conn, _ = loaded
        rows = conn.execute(
            "SELECT ein FROM bmf WHERE name_normalized = ?", ["FEEDING AMERICA"]
        ).fetchall()
        assert rows == [(FEEDING_AMERICA,)]

    def test_state_and_first_token_block(self, loaded) -> None:
        conn, _ = loaded
        rows = conn.execute(
            "SELECT ein FROM bmf WHERE state = ? AND first_token = ?", ["IL", "FEEDING"]
        ).fetchall()
        assert (FEEDING_AMERICA,) in rows

    def test_zip5_and_first_token_block(self, loaded) -> None:
        conn, _ = loaded
        rows = conn.execute(
            "SELECT ein FROM bmf WHERE zip5 = ? AND first_token = ?", ["60601", "FEEDING"]
        ).fetchall()
        assert rows == [(FEEDING_AMERICA,)]

    def test_sort_name_is_normalized_when_present(self, loaded) -> None:
        conn, _ = loaded
        (n_with_sort,) = conn.execute(
            "SELECT COUNT(*) FROM bmf WHERE sort_name IS NOT NULL AND sort_name_normalized IS NULL"
        ).fetchone()
        assert n_with_sort == 0

    def test_group_exemption_subordinates_are_visible(self, loaded) -> None:
        # The fixture deliberately includes subordinates (non-zero GROUP); the matcher will
        # need to know they are chapters of something, not independent organizations.
        conn, _ = loaded
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM bmf WHERE group_exemption IS NOT NULL AND group_exemption <> '0000'"
        ).fetchone()
        assert n > 0
