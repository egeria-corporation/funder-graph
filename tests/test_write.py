"""The published row, against the README and against DuckDB.

The README's schema table is the public contract. The first test parses that table out of the
README and asserts it equals the writer's column list, so a change to either without the
other fails here. The rest prove the properties consumers depend on: a stable ``grant_id``,
the sort order that makes ``WHERE funder_ein = ?`` cheap, and individuals kept out of the
default edge view.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from funder_graph.extract import extract
from funder_graph.pipeline.write import COLUMNS, SCHEMA, grant_id, to_record, write_shard

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "filings"
BRODERICK = "2022v5.0__202333349349100703.xml"
HARDSHIP = "2022v5.0__202313339349100601.xml"
SCHED_I = "2021v4.2__202303319349300220.xml"

STAMP = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def load(name: str):
    return extract((FIXTURES / name).read_bytes(), name.split("__", 1)[1].removesuffix(".xml"))


def records(name: str, filing_year: int = 2023) -> list[dict]:
    return [
        to_record(
            r,
            filing_year=filing_year,
            filing_submission_date=None,
            concordance_version="d8266da934cd1cb5d1d9cedc5398ba107b0c7bbe",
            dataset_version="2026.09.0",
            ingested_at=STAMP,
        )
        for r in load(name).rows
    ]


def readme_schema_columns() -> list[str]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## Published schema", 1)[1].split("Companion tables", 1)[0]
    return re.findall(r"^\| `([a-z_0-9]+)` \|", section, re.M)


class TestContract:
    def test_columns_equal_the_readme_schema_table_exactly(self) -> None:
        # If this fails, one of the two changed without the other. Update both, and note it
        # in CHANGELOG.md: consumers pin this schema.
        assert readme_schema_columns() == COLUMNS

    def test_schema_field_order_matches_columns(self) -> None:
        assert [f.name for f in SCHEMA] == COLUMNS


class TestGrantId:
    def test_stable_across_re_extraction(self) -> None:
        a = [r["grant_id"] for r in records(BRODERICK)]
        b = [r["grant_id"] for r in records(BRODERICK)]
        assert a == b

    def test_unique_within_a_filing_and_well_formed(self) -> None:
        ids = [r["grant_id"] for r in records(BRODERICK)]
        assert len(set(ids)) == len(ids) == 4
        assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)

    def test_is_a_function_of_object_group_and_ordinal_only(self) -> None:
        assert grant_id("202333349349100703", "pf_paid", 0) == records(BRODERICK)[0]["grant_id"]
        assert grant_id("202333349349100703", "pf_paid", 0) != grant_id(
            "202333349349100703", "pf_paid", 1
        )
        assert grant_id("202333349349100703", "pf_paid", 0) != grant_id(
            "202333349349100703", "pf_future", 0
        )


class TestToRecord:
    def test_broderick_first_row_in_the_published_schema(self) -> None:
        r = records(BRODERICK)[0]
        assert set(r) == set(COLUMNS)
        assert r["funder_ein"] == "846725611"
        assert r["funder_form_type"] == "990PF"
        assert r["tax_year"] == 2022 and r["tax_period_end"] == date(2022, 12, 31)
        assert r["filing_year"] == 2023
        assert r["amount_usd"] == 33_333 and r["amount_type"] == "paid"
        assert r["recipient_name_raw"] == "IOWA STATE UNIVERSITY ALUMNI ASSOCIATION"
        assert r["recipient_name_normalized"] == "IOWA STATE UNIV ALUMNI ASSN"
        assert r["recipient_zip"] == "50011" and r["recipient_zip5"] == "50011"
        assert r["recipient_country"] == "US"
        assert r["recipient_type"] == "organization"
        # Unresolved until milestone 4, and honest about it.
        assert r["recipient_ein_resolved"] is None
        assert r["recipient_ein_source"] == "unresolved"
        assert r["match_confidence"] is None and r["match_tier"] == "U"
        assert r["concordance_version"].startswith("d8266da9")
        assert r["dataset_version"] == "2026.09.0"

    def test_individual_rows_use_the_person_name_as_raw_name(self) -> None:
        rows = records(HARDSHIP)
        assert all(r["recipient_type"] == "individual" for r in rows)
        assert rows[0]["recipient_name_raw"] == "COMFORT CASES"

    def test_schedule_i_carries_the_reported_ein(self) -> None:
        r = records(SCHED_I)[0]
        assert r["funder_form_type"] == "990"
        assert r["recipient_ein_reported"] and len(r["recipient_ein_reported"]) == 9


class TestWriteShard:
    def test_individuals_go_to_their_own_table(self, tmp_path: Path) -> None:
        rows = records(BRODERICK) + records(HARDSHIP)
        counts = write_shard(rows, tmp_path, 2023, 0)
        assert counts == {"grants": 4, "grants_individuals": 7}
        assert (tmp_path / "grants" / "filing_year=2023" / "part-0000.parquet").exists()
        assert (tmp_path / "grants_individuals" / "filing_year=2023" / "part-0000.parquet").exists()

    def test_sorted_by_funder_then_year_then_amount_desc(self, tmp_path: Path) -> None:
        rows = records(SCHED_I) + records(BRODERICK)  # deliberately out of order
        write_shard(rows, tmp_path, 2023, 0)
        got = duckdb.sql(
            f"SELECT funder_ein, amount_usd FROM read_parquet('{(tmp_path / 'grants' / 'filing_year=2023' / 'part-0000.parquet').as_posix()}')"
        ).fetchall()
        eins = [e for e, _ in got]
        assert eins == sorted(eins)
        brod = [a for e, a in got if e == "846725611"]
        assert brod == sorted(brod, reverse=True) == [33_333, 5_556, 5_556, 5_555]

    def test_duckdb_reads_the_hive_layout_and_prunes_by_funder(self, tmp_path: Path) -> None:
        # The README quickstart, against what we actually write.
        write_shard(records(BRODERICK) + records(SCHED_I), tmp_path, 2023, 0)
        write_shard(records(HARDSHIP, filing_year=2024), tmp_path, 2024, 0)
        glob = (tmp_path / "grants" / "*" / "*.parquet").as_posix()
        rows = duckdb.sql(
            f"SELECT filing_year, recipient_name_raw, amount_usd FROM read_parquet('{glob}', hive_partitioning = 1) "
            "WHERE funder_ein = '846725611' ORDER BY amount_usd DESC"
        ).fetchall()
        assert [r[0] for r in rows] == [2023] * 4
        assert rows[0][1] == "IOWA STATE UNIVERSITY ALUMNI ASSOCIATION" and rows[0][2] == 33_333

    def test_empty_tables_write_no_file(self, tmp_path: Path) -> None:
        counts = write_shard(records(BRODERICK), tmp_path, 2023, 3)
        assert counts == {"grants": 4, "grants_individuals": 0}
        assert not (tmp_path / "grants_individuals").exists()

    def test_types_match_the_declared_schema(self, tmp_path: Path) -> None:
        write_shard(records(BRODERICK), tmp_path, 2023, 0)
        import pyarrow.parquet as pq

        schema = pq.read_schema(tmp_path / "grants" / "filing_year=2023" / "part-0000.parquet")
        assert schema.field("amount_usd").type == SCHEMA.field("amount_usd").type
        assert schema.field("tax_period_end").type == SCHEMA.field("tax_period_end").type
        assert schema.field("match_confidence").type == SCHEMA.field("match_confidence").type
        assert "filing_year" not in schema.names  # it is the partition key, read from the path


@pytest.mark.parametrize("name", [BRODERICK, HARDSHIP, SCHED_I])
def test_every_record_has_every_column_and_no_extras(name: str) -> None:
    for r in records(name):
        assert list(r) == COLUMNS
