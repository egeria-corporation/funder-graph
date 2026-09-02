"""The normalize stage end to end, against a synthetic posting built from real filings.

Proves the wiring rather than the parts: an archive goes in, Parquet shards and report rows
come out, and a second archive merges into the same reports. Also pins the honesty rule for
``filing_submission_date``: a bare year in the index is stored as null, never as a made-up
January 1st.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from funder_graph.pipeline.extract import build_zip
from funder_graph.pipeline.normalize import normalize_archive, normalize_year, submission_date

FIXTURES = Path(__file__).parent / "fixtures" / "filings"
STAMP = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
COMMIT = "d8266da934cd1cb5d1d9cedc5398ba107b0c7bbe"

BRODERICK = "202333349349100703"
HARDSHIP = "202313339349100601"
CHILE = "202303319349100105"
SCHED_I = "202303319349300220"


def members() -> dict[str, bytes]:
    out = {}
    for path in sorted(FIXTURES.glob("*__*.xml")):
        out[path.name.split("__", 1)[1].removesuffix(".xml")] = path.read_bytes()
    return out


@pytest.fixture
def posting(tmp_path: Path) -> tuple[list[Path], set[str], dict[str, str | None]]:
    m = members()
    ids = sorted(m)
    a, b = tmp_path / "2023_TEOS_XML_01A.zip", tmp_path / "2023_TEOS_XML_02A.zip"
    a.write_bytes(build_zip({i: m[i] for i in ids[:4]}, "2023_TEOS_XML_01A"))
    b.write_bytes(build_zip({i: m[i] for i in ids[4:]}, "2023_TEOS_XML_02A"))
    # The 2023 index says "2023" for every filing; give one a real date to prove both paths.
    sub_dates: dict[str, str | None] = dict.fromkeys(ids, "2023")
    sub_dates[BRODERICK] = "2023-09-15"
    return [a, b], set(ids), sub_dates


class TestSubmissionDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2023-09-15", date(2023, 9, 15)),
            ("2023", None),  # the whole 2023 index looks like this
            ("", None),
            (None, None),
            ("09/15/2023", None),  # not ISO; not guessed
            ("2023-13-40", None),  # ISO-shaped nonsense; not guessed
        ],
    )
    def test_only_a_real_iso_date_becomes_a_date(self, raw, expected) -> None:
        assert submission_date(raw) == expected


class TestNormalizeArchive:
    def test_one_archive_produces_shards_and_report_rows(self, posting, tmp_path: Path) -> None:
        archives, wanted, sub_dates = posting
        out = tmp_path / "parquet"
        r = normalize_archive(
            archives[0], wanted, sub_dates, 2023, out, 0, COMMIT, "2026.09.0", STAMP
        )

        assert r.archive == "2023_TEOS_XML_01A.zip"
        assert r.filings == 4
        assert r.rows == sum(r.counts.values())
        assert r.counts["grants"] > 0
        assert (out / "grants" / "filing_year=2023" / "part-0000.parquet").exists()
        assert len(r.pf_rows) + len(r.si_rows) == 4  # every filing yields exactly one report row

    def test_errors_are_collected_not_raised(self, tmp_path: Path) -> None:
        z = tmp_path / "z.zip"
        z.write_bytes(build_zip({"000000000000000001": b"<not xml"}, "z"))
        r = normalize_archive(
            z, {"000000000000000001"}, {}, 2023, tmp_path / "p", 0, COMMIT, "v", STAMP
        )
        assert r.filings == 0 and r.rows == 0
        assert r.errors and "000000000000000001" in r.errors[0]


class TestNormalizeYear:
    def test_two_archives_merge_into_one_set_of_reports(self, posting, tmp_path: Path) -> None:
        archives, wanted, sub_dates = posting
        out, reports = tmp_path / "parquet", tmp_path / "reports"
        y = normalize_year(
            archives,
            wanted,
            sub_dates,
            2023,
            out,
            reports,
            concordance_version=COMMIT,
            dataset_version="2026.09.0",
            workers=1,
            ingested_at=STAMP,
        )

        assert y.filing_year == 2023
        assert y.filings == 8
        assert len(y.archives) == 2
        counts = y.table_counts()
        assert counts["grants"] + counts["grants_individuals"] == y.rows
        # 7 hardship grants + 15 West High scholarships (2 paid, 13 approved-future) + the
        # two names no token can catch, BLACK MEN HEAL and Kappa Alpha Theta. Both are real
        # organizations; both are the recorded limit of a name-only rule, and both will be
        # re-examined against the Business Master File by the matcher in milestone 4.
        assert counts["grants_individuals"] == 24
        assert (reports / "pf-total-reconciliation.csv").exists()
        assert (reports / "schedule-i-count-reconciliation.csv").exists()
        assert (reports / "pf-missing-detail.csv").exists()

        s = y.summary
        assert s.pf_filings == 7 and s.sched_i_filings == 1
        assert s.pf_within_share == 100.0
        assert s.pf_no_total == 1 and s.pf_missing_detail == 1  # Chile, both times
        assert s.sched_i_exact_share == 100.0

    def test_submission_date_is_null_for_a_bare_year_and_real_for_a_date(
        self, posting, tmp_path: Path
    ) -> None:
        archives, wanted, sub_dates = posting
        out = tmp_path / "parquet"
        normalize_year(
            archives,
            wanted,
            sub_dates,
            2023,
            out,
            tmp_path / "reports",
            concordance_version=COMMIT,
            dataset_version="2026.09.0",
            workers=1,
            ingested_at=STAMP,
        )
        glob = (out / "grants" / "*" / "*.parquet").as_posix()
        rows = duckdb.sql(
            f"SELECT object_id, filing_submission_date FROM read_parquet('{glob}', hive_partitioning = 1) "
            f"WHERE object_id IN ('{BRODERICK}', '{SCHED_I}') GROUP BY ALL ORDER BY object_id"
        ).fetchall()
        by = dict(rows)
        assert by[BRODERICK] == date(2023, 9, 15)
        assert by[SCHED_I] is None  # "2023" is a year, not a date

    def test_every_row_carries_provenance(self, posting, tmp_path: Path) -> None:
        archives, wanted, sub_dates = posting
        out = tmp_path / "parquet"
        normalize_year(
            archives,
            wanted,
            sub_dates,
            2023,
            out,
            tmp_path / "reports",
            concordance_version=COMMIT,
            dataset_version="2026.09.0",
            workers=1,
            ingested_at=STAMP,
        )
        glob = (out / "*" / "*" / "*.parquet").as_posix()
        (n_null,) = duckdb.sql(
            f"SELECT COUNT(*) FROM read_parquet('{glob}', hive_partitioning = 1) "
            "WHERE object_id IS NULL OR tax_period_end IS NULL OR return_version IS NULL "
            "OR concordance_version IS NULL OR dataset_version IS NULL OR grant_id IS NULL"
        ).fetchone()
        assert n_null == 0
