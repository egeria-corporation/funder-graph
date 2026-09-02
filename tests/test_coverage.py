"""The coverage matrix, against the committed real filings.

Seven filings across four schema versions are enough to prove the three reports are built
the way the spec and upstream expect: leaf coverage counted only where a grant group exists,
the XPath inventory in upstream's exact three columns, and the drift report naming what real
filings contain that we deliberately do not read.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from funder_graph.concordance import PF_PAID_GROUP
from funder_graph.pipeline.coverage import (
    Tally,
    consumed_xpaths,
    tally_corpus,
    tally_filing,
    write_unmapped_fields,
    write_version_coverage,
    write_xpath_version_count,
)
from funder_graph.pipeline.extract import build_zip

FIXTURES = Path(__file__).parent / "fixtures" / "filings"


def fixture_members() -> dict[str, bytes]:
    out = {}
    for path in sorted(FIXTURES.glob("*__*.xml")):
        object_id = path.name.split("__", 1)[1].removesuffix(".xml")
        out[object_id] = path.read_bytes()
    return out


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> tuple[list[Path], set[str]]:
    members = fixture_members()
    root = tmp_path_factory.mktemp("corpus")
    # Split across two archives so the parallel path has something to merge.
    ids = sorted(members)
    a = root / "2023_TEOS_XML_01A.zip"
    b = root / "2023_TEOS_XML_02A.zip"
    a.write_bytes(build_zip({i: members[i] for i in ids[:4]}, "2023_TEOS_XML_01A"))
    b.write_bytes(build_zip({i: members[i] for i in ids[4:]}, "2023_TEOS_XML_02A"))
    return [a, b], set(ids)


@pytest.fixture(scope="module")
def tally(corpus) -> Tally:
    archives, wanted = corpus
    t, errors = tally_corpus(archives, wanted, workers=1)
    assert errors == []
    return t


class TestTally:
    def test_every_fixture_version_is_counted(self, tally: Tally) -> None:
        versions = {v for v, _ in tally.versions}
        assert versions == {"2020v4.0", "2021v4.0", "2021v4.2", "2022v5.0"}
        # Eight committed filings: five at 2022v5.0, one each at 2020v4.0, 2021v4.0, 2021v4.2.
        assert tally.filings_seen == 8

    def test_pf_2022v5_counts_filings_and_filings_with_rows(self, tally: Tally) -> None:
        s = tally.versions[("2022v5.0", "990PF")]
        # Broderick, individuals, Chile placeholder, Ghana, hardship grants: all have rows.
        assert s.filings == 5
        assert s.with_rows == 5

    def test_every_pf_filing_with_rows_fully_resolves(self, tally: Tally) -> None:
        # This is the exit criterion in miniature. The all-individuals filing resolves
        # because *either* name slot filling satisfies the name requirement.
        for (_, rtype), s in tally.versions.items():
            if rtype == "990PF":
                assert s.fully_resolved == s.with_rows, (rtype, s)

    def test_schedule_i_resolves_on_the_2021v4_2_filing(self, tally: Tally) -> None:
        s = tally.versions[("2021v4.2", "990")]
        assert s.filings == 1 and s.with_rows == 1 and s.fully_resolved == 1
        assert s.field_hits["recipient_ein"] == 1

    def test_inventory_counts_filings_per_version_for_each_path(self, tally: Tally) -> None:
        amt = tally.inventory[f"{PF_PAID_GROUP}/Amt"]
        # Every 990-PF fixture with paid rows, by version.
        assert amt["2022v5.0"] == 5
        assert amt["2020v4.0"] == 1
        assert amt["2021v4.0"] == 1
        assert "2021v4.2" not in amt  # the 990 filing has no Part XV

    def test_a_corrupt_member_is_counted_not_fatal(self) -> None:
        t, err = tally_filing(b"<not xml", "bad")
        assert err and err.startswith("bad:")
        assert sum(t.parse_errors.values()) == 1
        assert t.filings_seen == 0

    def test_parallel_merge_equals_serial(self, corpus) -> None:
        archives, wanted = corpus
        serial, _ = tally_corpus(archives, wanted, workers=1)
        parallel, _ = tally_corpus(archives, wanted, workers=2)
        assert {
            k: (s.filings, s.with_rows, s.fully_resolved) for k, s in serial.versions.items()
        } == {k: (s.filings, s.with_rows, s.fully_resolved) for k, s in parallel.versions.items()}
        assert (
            serial.inventory[f"{PF_PAID_GROUP}/Amt"] == parallel.inventory[f"{PF_PAID_GROUP}/Amt"]
        )


class TestReports:
    def test_version_coverage_csv_and_headline(self, tally: Tally, tmp_path: Path) -> None:
        out = tmp_path / "version-coverage.csv"
        pct = write_version_coverage(tally, out)
        assert pct == 100.0
        rows = list(csv.DictReader(out.open(encoding="utf-8", newline="")))
        by = {(r["return_version"], r["return_type"]): r for r in rows}
        assert by[("2022v5.0", "990PF")]["filings"] == "5"
        assert by[("2022v5.0", "990PF")]["fully_resolved_pct"] == "100.00"
        assert "amount" in rows[0] and "recipient_ein" in rows[0]

    def test_xpath_version_count_uses_upstreams_three_columns(
        self, tally: Tally, tmp_path: Path
    ) -> None:
        out = tmp_path / "xpath-version-count.csv"
        n = write_xpath_version_count(tally, out)
        assert n == len(tally.inventory)
        rows = list(csv.DictReader(out.open(encoding="utf-8", newline="")))
        assert list(rows[0].keys()) == ["XPATH", "VERSION", "COUNT"]
        amt = next(r for r in rows if r["XPATH"] == f"{PF_PAID_GROUP}/Amt")
        # Versions joined with ";;" exactly as upstream's draft-updates file does.
        assert amt["VERSION"] == "2020v4.0;;2021v4.0;;2022v5.0"
        assert amt["COUNT"] == "7"

    def test_unmapped_fields_names_what_we_deliberately_do_not_read(
        self, tally: Tally, tmp_path: Path
    ) -> None:
        out = tmp_path / "unmapped-fields.csv"
        n = write_unmapped_fields(tally, out)
        assert n > 0
        text = out.read_text(encoding="utf-8")
        # The application contact's phone number is present in a real fixture and must never
        # be consumed (docs/NON-GOALS.md). It belongs in this report, on purpose, forever.
        assert "ApplicationSubmissionInfoGrp/RecipientPhoneNum" in text
        # Things we do read are not "unmapped".
        assert f"{PF_PAID_GROUP}/Amt," not in text
        assert f"{PF_PAID_GROUP}/RecipientUSAddress/CityNm," not in text

    def test_consumed_xpaths_include_leaves_and_their_containers(self) -> None:
        consumed = consumed_xpaths()
        assert f"{PF_PAID_GROUP}/Amt" in consumed
        assert f"{PF_PAID_GROUP}/RecipientUSAddress" in consumed  # walked on the way to CityNm
        assert PF_PAID_GROUP in consumed
        assert (
            "/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/ApplicationSubmissionInfoGrp/RecipientPhoneNum"
            not in consumed
        )
