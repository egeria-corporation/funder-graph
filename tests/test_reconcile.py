"""Reconciliation, against the real filings.

The build spec calls these reports the highest-value QA in the pipeline and makes the 990-PF
number a gate. Every status the reports can emit is exercised here on a committed filing or
a one-line mutation of one, so the meaning of each is pinned.
"""

from __future__ import annotations

from pathlib import Path

from funder_graph.extract import extract
from funder_graph.pipeline.reconcile import (
    missing_detail,
    pf_total,
    sched_i_count,
    write_reports,
)

FIXTURES = Path(__file__).parent / "fixtures" / "filings"

BRODERICK = "2022v5.0__202333349349100703.xml"  # 4 rows, stated 50,000
HARDSHIP = "2022v5.0__202313339349100601.xml"  # 7 rows, stated 263,775
FUTURE = "2021v4.0__202303319349100400.xml"  # 3 paid stated 10,000; 13 future stated 29,979
CHILE = "2022v5.0__202303319349100105.xml"  # 1 placeholder row, NO stated total
SCHED_I = "2021v4.2__202303319349300220.xml"  # 3 rows, stated 3 + 0


def load(name: str):
    return extract((FIXTURES / name).read_bytes(), name.split("__", 1)[1].removesuffix(".xml"))


class TestPfTotal:
    def test_a_clean_filing_is_within_tolerance_at_delta_zero(self) -> None:
        r = pf_total(load(BRODERICK))
        assert r.status == "within_tolerance"
        assert r.parsed_paid == r.reported_paid == 50_000
        assert r.delta == 0 and r.delta_pct == 0.0

    def test_future_rows_are_not_counted_against_the_paid_total(self) -> None:
        # The most common analytical error with 990-PF data, guarded at the report level.
        r = pf_total(load(FUTURE))
        assert r.parsed_paid == 10_000 == r.reported_paid
        assert r.status == "within_tolerance"

    def test_no_stated_total_is_its_own_status_not_a_failure(self) -> None:
        # The Chilean filing has a row but omits TotalGrantOrContriPdDurYrAmt entirely.
        r = pf_total(load(CHILE))
        assert r.status == "no_total"
        assert r.reported_paid is None and r.delta is None and r.delta_pct is None
        assert r.parsed_paid == 9_758_900

    def test_a_two_percent_disagreement_is_over_tolerance(self) -> None:
        xml = (
            (FIXTURES / BRODERICK)
            .read_text(encoding="utf-8")
            .replace(
                "<TotalGrantOrContriPdDurYrAmt>50000<", "<TotalGrantOrContriPdDurYrAmt>49000<", 1
            )
        )
        r = pf_total(extract(xml.encode("utf-8"), "synthetic"))
        assert r.status == "over_tolerance"
        assert r.delta == 1_000
        assert round(r.delta_pct, 2) == 2.04

    def test_a_filing_with_nothing_at_all_is_no_rows(self) -> None:
        xml = (FIXTURES / BRODERICK).read_text(encoding="utf-8")
        start = xml.index("<GrantOrContributionPdDurYrGrp>")
        end = xml.rindex("</GrantOrContributionPdDurYrGrp>") + len(
            "</GrantOrContributionPdDurYrGrp>"
        )
        stripped = (xml[:start] + xml[end:]).replace(
            "<TotalGrantOrContriPdDurYrAmt>50000</TotalGrantOrContriPdDurYrAmt>", "", 1
        )
        r = pf_total(extract(stripped.encode("utf-8"), "synthetic"))
        assert r.status == "no_rows"


class TestScheduleICount:
    def test_exact_match_against_the_filers_own_counts(self) -> None:
        r = sched_i_count(load(SCHED_I))
        assert r.status == "exact"
        assert r.parsed_rows == 3 and r.reported_501c3 == 3 and r.reported_other == 0
        assert r.reported_total == 3 and r.delta == 0

    def test_a_dropped_row_is_a_mismatch(self) -> None:
        xml = (FIXTURES / SCHED_I).read_text(encoding="utf-8")
        start = xml.index("<RecipientTable>")
        end = xml.index("</RecipientTable>") + len("</RecipientTable>")
        r = sched_i_count(extract((xml[:start] + xml[end:]).encode("utf-8"), "synthetic"))
        assert r.status == "mismatch" and r.parsed_rows == 2 and r.delta == -1


class TestMissingDetail:
    def test_an_itemized_filing_is_not_missing_detail(self) -> None:
        assert missing_detail(load(BRODERICK)) is None
        assert missing_detail(load(HARDSHIP)) is None

    def test_a_placeholder_only_filing_with_no_total_is_reported(self) -> None:
        m = missing_detail(load(CHILE))
        assert m is not None
        assert m.structured_rows == 1 and m.placeholder_rows == 1
        assert m.reported_paid is None
        assert m.reason == "only aggregate placeholder rows, no stated total"

    def test_a_stated_total_with_no_rows_is_reported(self) -> None:
        xml = (FIXTURES / BRODERICK).read_text(encoding="utf-8")
        start = xml.index("<GrantOrContributionPdDurYrGrp>")
        end = xml.rindex("</GrantOrContributionPdDurYrGrp>") + len(
            "</GrantOrContributionPdDurYrGrp>"
        )
        m = missing_detail(extract((xml[:start] + xml[end:]).encode("utf-8"), "synthetic"))
        assert m is not None
        assert m.reported_paid == 50_000 and m.structured_rows == 0
        assert m.reason == "no structured rows"

    def test_a_990_is_never_missing_detail(self) -> None:
        assert missing_detail(load(SCHED_I)) is None


class TestWriteReports:
    def test_all_three_reports_and_the_summary(self, tmp_path: Path) -> None:
        extractions = [load(p.name) for p in sorted(FIXTURES.glob("*__*.xml"))]
        s = write_reports(extractions, tmp_path)

        assert s.pf_filings == 7 and s.sched_i_filings == 1
        assert s.pf_no_total == 1  # Chile
        assert s.pf_with_total == 6
        assert s.pf_within_tolerance == 6
        assert s.pf_within_share == 100.0
        assert s.pf_missing_detail == 1
        assert s.sched_i_exact_share == 100.0

        for name, n in s.rows.items():
            path = tmp_path / name
            assert path.exists(), name
            header = path.read_text(encoding="utf-8").splitlines()[0]
            assert header.startswith("object_id,"), name
        assert s.rows["pf-total-reconciliation.csv"] == 7
        assert s.rows["schedule-i-count-reconciliation.csv"] == 1
        assert s.rows["pf-missing-detail.csv"] == 1

    def test_the_worst_disagreements_sort_first(self, tmp_path: Path) -> None:
        good = load(BRODERICK)
        xml = (
            (FIXTURES / HARDSHIP)
            .read_text(encoding="utf-8")
            .replace(
                "<TotalGrantOrContriPdDurYrAmt>263775<", "<TotalGrantOrContriPdDurYrAmt>200000<", 1
            )
        )
        bad = extract(xml.encode("utf-8"), "bad")
        write_reports([good, bad], tmp_path)
        lines = (tmp_path / "pf-total-reconciliation.csv").read_text(encoding="utf-8").splitlines()
        assert lines[1].startswith("bad,")  # over_tolerance sorts before within_tolerance
