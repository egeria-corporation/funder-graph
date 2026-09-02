"""The index / reconcile / stream stage, against a synthetic IRS posting.

The ZIP is built in the IRS layout from the committed real filings, and the index CSV uses
the real 2023 header verbatim. Every rule here is one the spec states and one the real data
made concrete: an unknown header is a loud error, amended returns are deduplicated and
recorded rather than dropped, and reconciliation reports both directions without crashing.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from funder_graph.pipeline.extract import (
    IndexHeaderError,
    build_zip,
    iter_filings,
    load_index,
    normalize_header,
    reconcile,
    register_zip,
    wanted_object_ids,
)

FIXTURES = Path(__file__).parent / "fixtures" / "filings"
REAL_HEADER = (
    "RETURN_ID,FILING_TYPE,EIN,TAX_PERIOD,SUB_DATE,TAXPAYER_NAME,RETURN_TYPE,DLN,OBJECT_ID"
)

# Real OBJECT_IDs from the committed fixtures. The synthetic index below invents the
# rest of each row; only the shape matters here, and the shape is the real one.
BRODERICK = "202333349349100703"  # 990PF
ARTS = "202303319349300220"  # 990
FUTURE = "202303319349100400"  # 990PF
GHANA = "202303319349100605"  # 990PF


def fixture_bytes(object_id: str) -> bytes:
    (path,) = FIXTURES.glob(f"*__{object_id}.xml")
    return path.read_bytes()


def write_index(path: Path, rows: list[str], header: str = REAL_HEADER) -> Path:
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


class TestHeaders:
    def test_the_real_2023_header_normalises(self) -> None:
        assert normalize_header(REAL_HEADER.split(",")) == [
            "return_id",
            "filing_type",
            "ein",
            "tax_period",
            "sub_date",
            "taxpayer_name",
            "return_type",
            "dln",
            "object_id",
        ]

    def test_known_variants_and_case_are_tolerated(self) -> None:
        cols = normalize_header(
            ["ein", " Tax_Prd ", "SUBMISSION_DATE", "Taxpayer Name", "return type", "Object ID"]
        )
        assert cols == [
            "ein",
            "tax_period",
            "sub_date",
            "taxpayer_name",
            "return_type",
            "object_id",
        ]

    def test_an_unknown_header_is_a_loud_error_that_names_it(self) -> None:
        with pytest.raises(IndexHeaderError, match=r"FILING_DT"):
            normalize_header(REAL_HEADER.replace("SUB_DATE", "FILING_DT").split(","))

    def test_a_missing_required_column_is_a_loud_error(self) -> None:
        with pytest.raises(IndexHeaderError, match="object_id"):
            normalize_header(["EIN", "TAX_PERIOD", "SUB_DATE", "TAXPAYER_NAME", "RETURN_TYPE"])


class TestLoadIndex:
    def test_filters_to_grant_bearing_forms_and_counts_the_rest(self, conn, tmp_path: Path) -> None:
        idx = write_index(
            tmp_path / "index_2023.csv",
            [
                f"1,EFILE,846725611,202212,2023,BRODERICK,990PF,93491,{BRODERICK}",
                f"2,EFILE,363177592,202209,2023,ILLINOIS ARTS,990,93493,{ARTS}",
                "3,EFILE,111111111,202212,2023,SMALL ORG,990EZ,93492,202300000000000001",
                "4,EFILE,222222222,202212,2023,UBIT FILER,990T,93494,202300000000000002",
            ],
        )
        summary = load_index(conn, idx, 2023)
        assert summary.rows_read == 4
        assert summary.grant_bearing == 2
        assert summary.kept == 2
        assert summary.superseded == 0
        assert summary.by_return_type == {"990": 1, "990EZ": 1, "990PF": 1, "990T": 1}
        assert wanted_object_ids(conn, 2023) == {BRODERICK, ARTS}

    def test_amended_returns_keep_the_latest_and_record_the_loser(
        self, conn, tmp_path: Path
    ) -> None:
        # Same EIN, same TAX_PERIOD, same RETURN_TYPE, filed twice. The later SUB_DATE wins;
        # the earlier one is kept in superseded_filings pointing at its replacement.
        idx = write_index(
            tmp_path / "index_2023.csv",
            [
                f"1,EFILE,846725611,202212,2023-03-01,BRODERICK,990PF,93491,{BRODERICK}",
                f"2,EFILE,846725611,202212,2023-09-15,BRODERICK AMENDED,990PF,93495,{FUTURE}",
            ],
        )
        summary = load_index(conn, idx, 2023)
        assert summary.kept == 1 and summary.superseded == 1
        assert wanted_object_ids(conn, 2023) == {FUTURE}
        (row,) = conn.execute("SELECT object_id, superseded_by FROM superseded_filings").fetchall()
        assert row == (BRODERICK, FUTURE)

    def test_dedup_is_deterministic_when_sub_date_ties(self, conn, tmp_path: Path) -> None:
        # The 2023 index carries SUB_DATE as just "2023", so ties are the normal case.
        idx = write_index(
            tmp_path / "index_2023.csv",
            [
                f"1,EFILE,846725611,202212,2023,BRODERICK,990PF,93491,{FUTURE}",
                f"2,EFILE,846725611,202212,2023,BRODERICK,990PF,93495,{BRODERICK}",
            ],
        )
        load_index(conn, idx, 2023)
        # Highest OBJECT_ID wins on a tie, every time.
        assert wanted_object_ids(conn, 2023) == {max(BRODERICK, FUTURE)}

    def test_reloading_a_year_replaces_it(self, conn, tmp_path: Path) -> None:
        idx = write_index(
            tmp_path / "i.csv", [f"1,EFILE,846725611,202212,2023,X,990PF,1,{BRODERICK}"]
        )
        load_index(conn, idx, 2023)
        idx = write_index(tmp_path / "i.csv", [f"1,EFILE,363177592,202209,2023,Y,990,2,{ARTS}"])
        load_index(conn, idx, 2023)
        assert wanted_object_ids(conn, 2023) == {ARTS}


class TestZipsAndReconciliation:
    def test_registers_only_xml_members(self, conn, tmp_path: Path) -> None:
        archive = tmp_path / "2023_TEOS_XML_12A.zip"
        archive.write_bytes(
            build_zip({BRODERICK: b"<Return/>", ARTS: b"<Return/>"}, "2023_TEOS_XML_12A")
        )
        assert register_zip(conn, archive) == 2

    def test_reconciles_both_directions_and_ignores_superseded(self, conn, tmp_path: Path) -> None:
        idx = write_index(
            tmp_path / "index_2023.csv",
            [
                f"1,EFILE,846725611,202212,2023-01-01,B,990PF,1,{BRODERICK}",  # superseded by FUTURE
                f"2,EFILE,846725611,202212,2023-06-01,B,990PF,2,{FUTURE}",  # kept, in zip
                f"3,EFILE,363177592,202209,2023,A,990,3,{ARTS}",  # kept, NOT in zip
            ],
        )
        load_index(conn, idx, 2023)
        archive = tmp_path / "2023_TEOS_XML_01A.zip"
        archive.write_bytes(
            build_zip(
                {
                    FUTURE: b"<Return/>",
                    BRODERICK: b"<Return/>",  # superseded: present in zip, must NOT be zip_only
                    GHANA: b"<Return/>",  # in zip, absent from index entirely
                },
                "2023_TEOS_XML_01A",
            )
        )
        register_zip(conn, archive)

        r = reconcile(conn, 2023)
        assert r.matched == 1  # FUTURE
        assert r.index_only == 1  # ARTS
        assert r.zip_only == 1  # GHANA, not BRODERICK

        out = tmp_path / "reports" / "index-reconciliation-2023.csv"
        r.write_csv(conn, out)
        text = out.read_text(encoding="utf-8")
        assert f"index_only,{ARTS}" in text
        assert f"zip_only,{GHANA}" in text
        assert BRODERICK not in text


class TestStreaming:
    def test_streams_only_wanted_members_without_extracting(self, conn, tmp_path: Path) -> None:
        idx = write_index(
            tmp_path / "index_2023.csv",
            [
                f"1,EFILE,846725611,202212,2023,B,990PF,1,{BRODERICK}",
                f"2,EFILE,363177592,202209,2023,A,990,2,{ARTS}",
                "3,EFILE,111111111,202212,2023,EZ,990EZ,3,202300000000000001",
            ],
        )
        load_index(conn, idx, 2023)
        archive = tmp_path / "z.zip"
        archive.write_bytes(
            build_zip(
                {
                    BRODERICK: fixture_bytes(BRODERICK),
                    ARTS: fixture_bytes(ARTS),
                    "202300000000000001": b"<Return>never opened</Return>",
                },
                "z",
            )
        )
        wanted = wanted_object_ids(conn, 2023)
        got = dict(iter_filings(archive, only=wanted))
        assert set(got) == {BRODERICK, ARTS}
        assert b"BRODERICK CHARITABLE FOUNDATION TRUST" in got[BRODERICK]
        # Nothing was written to disk beyond the archive itself.
        assert sorted(p.name for p in tmp_path.iterdir()) == ["index_2023.csv", "z.zip"]
