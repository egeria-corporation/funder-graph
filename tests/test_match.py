"""The matcher, rule by rule, against a synthetic BMF small enough to reason about.

Every tier rule in the build spec's section 7 has a case here, and so do the two failure modes
the spec singles out: ambiguity resolving to the top candidate, and chapter organizations
matched without geography. Confidence is asserted exactly only where the spec fixes it (the
tier B ladder, the chapter cap); elsewhere the assertion is the band.

The synthetic rows go through ``bmf_record`` so the derived columns are the real ones. The
49-row real fixture is exercised in ``test_bmf.py``.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from funder_graph.pipeline.resolve import (
    BmfMissing,
    grant_files,
    pending_recipients,
    resolve,
    tier_distribution,
)
from funder_graph.pipeline.write import SCHEMA
from funder_graph.resolve.bmf import bmf_record, ensure_bmf_schema, insert_bmf_records
from funder_graph.resolve.match import (
    AMBIGUITY_MARGIN,
    JW_STRONG,
    TIER_B_CEIL,
    TIER_B_FLOOR,
    TIER_C_CEIL,
    TIER_C_FLOOR,
    TIER_D_CEIL,
    TIER_D_FLOOR,
    Candidate,
    Recipient,
    block,
    load_aliases,
    load_corrections,
    resolve_all,
    resolve_one,
    valid_ein,
)
from funder_graph.resolve.normalize import normalize_name
from funder_graph.resolve.phonetic import phonetic_key

FEEDING = "100000001"
BGC_SAC = "100000002"
BGC_FRESNO = "100000003"
HARVARD = "100000004"
FLINT_OF = "100000005"
FLINT_FOR = "100000006"
FBC_DALLAS = "100000007"
FBC_HOUSTON = "100000008"

_BMF = [
    (FEEDING, "FEEDING AMERICA", "CHICAGO", "IL", "60601-3389", ""),
    (BGC_SAC, "BOYS AND GIRLS CLUB OF SACRAMENTO", "SACRAMENTO", "CA", "95814", ""),
    (BGC_FRESNO, "BOYS AND GIRLS CLUB OF FRESNO", "FRESNO", "CA", "93721", ""),
    (
        HARVARD,
        "PRESIDENT AND FELLOWS OF HARVARD COLLEGE",
        "CAMBRIDGE",
        "MA",
        "02138",
        "HARVARD UNIVERSITY",
    ),
    (FLINT_OF, "COMMUNITY FOUNDATION OF GREATER FLINT", "FLINT", "MI", "48502", ""),
    (FLINT_FOR, "COMMUNITY FOUNDATION FOR GREATER FLINT", "FLINT", "MI", "48502", ""),
    (FBC_DALLAS, "FIRST BAPTIST CHURCH", "DALLAS", "TX", "75201", ""),
    (FBC_HOUSTON, "FIRST BAPTIST CHURCH", "HOUSTON", "TX", "77002", ""),
]


def _row(ein, name, city, state, zip_, sort_name):
    # grantcheck's parse_bmf yields lowercase keys; so does this.
    return {
        "ein": ein,
        "name": name,
        "sort_name": sort_name,
        "city": city,
        "state": state,
        "zip": zip_,
        "subsection": "03",
        "ntee_cd": "P30",
        "group_exemption": "0000",
    }


def load_synthetic(conn: duckdb.DuckDBPyConnection) -> None:
    ensure_bmf_schema(conn)
    insert_bmf_records(conn, [bmf_record(_row(*r), "2026-08") for r in _BMF])


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    load_synthetic(c)
    yield c
    c.close()


def one(conn, recipient: Recipient):
    return resolve_all(conn, [recipient])[0]


def rcpt(name: str, **fields) -> Recipient:
    """A recipient tuple the way the pipeline builds it: the name through ``normalize_name``.

    The BMF side is normalized the same way, and the canonical suffixes (FOUNDATION -> FDN,
    UNIVERSITY -> UNIV) only line up when both sides are.
    """
    return Recipient(normalize_name(name), **fields)


class TestReportedEin:
    def test_reported_and_in_bmf_is_tier_a_verified(self, conn) -> None:
        r = one(conn, rcpt("ANYTHING AT ALL", ein_reported="10-0000001"))
        assert (r.tier, r.source, r.confidence, r.ein) == ("A", "reported_verified", 1.0, FEEDING)
        assert r.bmf_name == "FEEDING AMERICA" and r.ntee_code == "P30"

    def test_reported_but_absent_is_tier_a_unverified_and_flagged(self, conn) -> None:
        r = one(conn, rcpt("SOME MERGED ORG", ein_reported="999999999"))
        assert (r.tier, r.source, r.confidence, r.ein) == (
            "A",
            "reported_unverified",
            0.95,
            "999999999",
        )
        assert r.method == "reported_ein_not_in_bmf"

    def test_revoked_is_noted(self) -> None:
        r = resolve_one(rcpt("X", ein_reported="999999999"), [], revoked=True)
        assert r.method == "reported_ein_revoked" and r.tier == "A"

    def test_structurally_invalid_reported_ein_falls_through_to_name_matching(self, conn) -> None:
        r = one(conn, rcpt("FEEDING AMERICA", state="IL", zip5="60601", ein_reported="12-345"))
        assert r.tier == "B" and r.ein == FEEDING

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("36-3673599", "363673599"),
            ("363673599", "363673599"),
            ("000000000", None),
            ("1234", None),
            (None, None),
        ],
    )
    def test_valid_ein(self, raw, expected) -> None:
        assert valid_ein(raw) == expected


class TestTierB:
    def test_name_zip5_state_at_the_ceiling_when_raw_name_and_city_agree(self, conn) -> None:
        r = one(
            conn,
            rcpt(
                "FEEDING AMERICA",
                name_raw="Feeding America",
                city="Chicago",
                state="IL",
                zip5="60601",
            ),
        )
        assert (r.tier, r.source, r.confidence) == ("B", "bmf_deterministic", TIER_B_CEIL)
        assert r.method == "name_zip5_state_exact"

    def test_name_zip5_state_at_the_floor_without_corroboration(self, conn) -> None:
        r = one(
            conn,
            rcpt("FEEDING AMERICA", name_raw="Feeding America Inc", state="IL", zip5="60601"),
        )
        assert (r.tier, r.confidence) == ("B", TIER_B_FLOOR)

    def test_exact_beats_a_near_duplicate_in_the_same_zip(self, conn) -> None:
        r = one(
            conn,
            rcpt(
                "COMMUNITY FOUNDATION OF GREATER FLINT",
                name_raw="Community Foundation of Greater Flint",
                city="Flint",
                state="MI",
                zip5="48502",
            ),
        )
        assert r.tier == "B" and r.ein == FLINT_OF


class TestTierC:
    def test_exact_name_and_state_without_zip(self, conn) -> None:
        r = one(conn, rcpt("FEEDING AMERICA", state="IL"))
        assert (r.tier, r.source, r.method) == ("C", "bmf_strong", "name_state_exact")
        assert TIER_C_FLOOR <= r.confidence <= TIER_C_CEIL

    def test_fuzzy_name_with_zip5(self, conn) -> None:
        r = one(conn, rcpt("FEEDING AMERCA", state="IL", zip5="60601"))
        assert (r.tier, r.method, r.ein) == ("C", "name_jw_zip5", FEEDING)
        assert TIER_C_FLOOR <= r.confidence <= TIER_C_CEIL

    def test_sort_name_resolves_the_university_case(self, conn) -> None:
        r = one(
            conn,
            rcpt(
                "HARVARD UNIVERSITY",
                name_raw="Harvard University",
                city="Cambridge",
                state="MA",
                zip5="02138",
            ),
        )
        assert r.tier == "B" and r.ein == HARVARD
        assert r.bmf_name == "PRESIDENT AND FELLOWS OF HARVARD COLLEGE"

    def test_zip_disagreement_lowers_confidence_but_does_not_veto(self, conn) -> None:
        agree = one(conn, rcpt("FEEDING AMERICA", state="IL"))
        conflict = one(conn, rcpt("FEEDING AMERICA", state="IL", zip5="99999"))
        assert conflict.tier == "C" and conflict.ein == FEEDING
        assert conflict.confidence < agree.confidence


class TestTierD:
    def test_fuzzy_in_state_single_candidate_is_a_guess_with_a_number(self, conn) -> None:
        r = one(conn, rcpt("FEEDING AMERCA", state="IL"))
        assert (r.tier, r.source, r.method) == ("D", "bmf_probable", "name_jw_state")
        assert TIER_D_FLOOR <= r.confidence <= TIER_D_CEIL

    def test_fuzzy_with_a_second_candidate_is_unresolved(self, conn) -> None:
        r = one(conn, rcpt("COMMUNITY FOUNDATION GREATER FLINT", state="MI"))
        assert r.tier == "U" and r.ein is None
        assert r.method in ("probable_not_unique", "ambiguous_2_candidates")

    def test_exact_name_with_no_state_anywhere_is_at_most_d(self) -> None:
        c = Candidate(
            FEEDING,
            "FEEDING AMERICA",
            None,
            "FEEDING AMERICA",
            None,
            "CHICAGO",
            None,
            "60601",
            "03",
            "P30",
            1.0,
        )
        r = resolve_one(rcpt("FEEDING AMERICA"), [c])
        assert r.tier == "D" and r.method == "name_exact_no_state"


class TestUnresolved:
    def test_two_exact_matches_in_one_state_is_unknown_not_a_coin_flip(self, conn) -> None:
        r = one(conn, rcpt("FIRST BAPTIST CHURCH", state="TX"))
        assert (r.tier, r.source, r.ein, r.confidence) == ("U", "unresolved", None, None)
        assert r.method == "ambiguous_2_candidates"

    def test_city_breaks_the_tie(self, conn) -> None:
        r = one(conn, rcpt("FIRST BAPTIST CHURCH", city="Dallas", state="TX"))
        assert r.tier == "C" and r.ein == FBC_DALLAS

    def test_no_candidate(self, conn) -> None:
        r = one(conn, rcpt("ZZYZX SOCIETY OF NOWHERE", state="NV"))
        assert r.tier == "U" and r.method == "no_candidates"

    @pytest.mark.parametrize("kind", ["individual", "government"])
    def test_individuals_and_governments_are_never_matched(self, conn, kind) -> None:
        r = one(conn, rcpt("FEEDING AMERICA", state="IL", zip5="60601", recipient_type=kind))
        assert r.tier == "U" and r.method == f"recipient_type_{kind}"

    def test_out_of_state_exact_name_is_not_a_match(self, conn) -> None:
        r = one(conn, rcpt("FEEDING AMERICA", state="CA"))
        assert r.tier == "U"

    def test_margin_is_the_documented_constant(self) -> None:
        assert AMBIGUITY_MARGIN == 0.03 and JW_STRONG == 0.94


class TestChapterOrganizations:
    def test_name_and_state_only_is_capped_at_mid_c(self, conn) -> None:
        r = one(
            conn,
            rcpt(
                "BOYS AND GIRLS CLUB OF SACRAMENTO",
                name_raw="Boys and Girls Club of Sacramento",
                state="CA",
            ),
        )
        assert (r.tier, r.confidence) == ("C", 0.80)
        assert r.method.endswith("+chapter_capped")

    def test_city_agreement_lifts_the_cap(self, conn) -> None:
        r = one(
            conn,
            rcpt(
                "BOYS AND GIRLS CLUB OF SACRAMENTO",
                name_raw="Boys and Girls Club of Sacramento",
                city="Sacramento",
                state="CA",
            ),
        )
        assert r.tier == "C" and r.confidence > 0.80 and "capped" not in r.method

    def test_zip_agreement_allows_tier_b(self, conn) -> None:
        r = one(conn, rcpt("BOYS AND GIRLS CLUB OF SACRAMENTO", state="CA", zip5="95814"))
        assert r.tier == "B" and r.ein == BGC_SAC


class TestBlocking:
    def test_every_block_key_finds_feeding_america(self, conn) -> None:
        recipients = [
            rcpt("FEEDING AMERICA"),  # exact name
            rcpt("FEEDING AMERCA", state="IL"),  # state + first token
            rcpt("FEEDING AMERCA", zip5="60601"),  # zip5 + first token
            rcpt("FEEDNG AMERICA", state="IL"),  # state + phonetic
            rcpt("HARVARD UNIVERSITY"),  # sort name
        ]
        by_idx, _ = block(conn, recipients)
        assert [c.ein for c in by_idx[0]] == [FEEDING]
        assert FEEDING in [c.ein for c in by_idx[1]]
        assert FEEDING in [c.ein for c in by_idx[2]]
        assert FEEDING in [c.ein for c in by_idx[3]]
        assert [c.ein for c in by_idx[4]] == [HARVARD]

    def test_reported_ein_lookup_rides_along(self, conn) -> None:
        _, reported = block(conn, [rcpt("X", ein_reported="100000004"), rcpt("Y")])
        assert reported[0].ein == HARVARD and 1 not in reported

    def test_phonetic_key(self) -> None:
        assert phonetic_key("FEEDING AMERICA") == phonetic_key("FEEDNG AMERICA")
        assert phonetic_key("") is None

    def test_empty_input(self, conn) -> None:
        assert block(conn, []) == ({}, {})


class TestOverrides:
    def test_alias_reaches_the_canonical_row(self, conn, tmp_path: Path) -> None:
        path = tmp_path / "name-aliases.csv"
        path.write_text(
            "alias_normalized,canonical_normalized,source,note\n"
            "HARVARD,PRESIDENT AND FELLOWS OF HARVARD COLLEGE,BMF sort name for EIN 100000004,\n",
            encoding="utf-8",
        )
        aliases = load_aliases(path)
        r = one(conn, rcpt("HARVARD", state="MA", zip5="02138", alias=aliases["HARVARD"]))
        assert r.tier == "B" and r.ein == HARVARD

    def test_alias_without_a_source_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "name-aliases.csv"
        path.write_text(
            "alias_normalized,canonical_normalized,source,note\nA,B,,\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="source"):
            load_aliases(path)

    def test_correction_outranks_the_automated_result(self, conn, tmp_path: Path) -> None:
        path = tmp_path / "ein-corrections.csv"
        path.write_text(
            "recipient_name_normalized,state,zip5,ein,source,note\n"
            "FIRST BAPTIST CHURCH,TX,,100000007,verified by hand against the filing's stated address,\n",
            encoding="utf-8",
        )
        corrections = load_corrections(path)
        r = resolve_all(conn, [rcpt("FIRST BAPTIST CHURCH", state="TX")], corrections=corrections)[
            0
        ]
        assert (r.tier, r.source, r.ein, r.confidence) == (
            "A",
            "manual_correction",
            FBC_DALLAS,
            1.0,
        )

    def test_missing_override_files_are_empty(self, tmp_path: Path) -> None:
        assert load_aliases(tmp_path / "none.csv") == {}
        assert load_corrections(tmp_path / "none.csv") == {}


def _grants_file(out_dir: Path, rows: list[dict]) -> Path:
    part = out_dir / "grants" / "filing_year=2023"
    part.mkdir(parents=True)
    path = part / "part-00000.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path, compression="zstd")
    return path


def _grant(i: int, **fields) -> dict:
    base = {
        "grant_id": f"g{i:04}",
        "funder_ein": "300000001",
        "filing_year": 2023,
        "tax_year": 2022,
        "amount_usd": 1000 - i,
        "recipient_type": "organization",
        "recipient_ein_source": "unresolved",
        "match_tier": "U",
    }
    return {**base, **fields}


class TestStage:
    def test_end_to_end_rewrites_in_place_preserving_order_and_count(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        state = duckdb.connect(str(work / "state.duckdb"))
        load_synthetic(state)
        state.close()
        out = tmp_path / "dataset"
        rows = [
            _grant(
                0,
                recipient_name_raw="Feeding America",
                recipient_name_normalized="FEEDING AMERICA",
                recipient_city="Chicago",
                recipient_state="IL",
                recipient_zip5="60601",
            ),
            _grant(
                1,
                recipient_name_raw="Feeding America",
                recipient_name_normalized="FEEDING AMERICA",
                recipient_city="Chicago",
                recipient_state="IL",
                recipient_zip5="60601",
            ),
            _grant(
                2,
                recipient_name_raw="First Baptist Church",
                recipient_name_normalized="FIRST BAPTIST CHURCH",
                recipient_state="TX",
            ),
            _grant(
                3,
                recipient_name_raw="Someone",
                recipient_name_normalized="SOMEONE",
                recipient_state="TX",
                recipient_ein_reported="100000008",
            ),
            _grant(
                4,
                recipient_name_raw="Nobody Known",
                recipient_name_normalized="NOBODY KNOWN",
                recipient_state="WY",
            ),
        ]
        path = _grants_file(out, rows)

        result = resolve(out, work, [2023])

        assert result.bmf_vintage == "2026-08"
        assert result.tuples_pending == 4  # rows 0 and 1 are one tuple
        assert result.tier_counts == {"A": 1, "B": 1, "U": 2}
        assert (result.files_rewritten, result.rows_rewritten) == (1, 5)

        table = pq.read_table(path)
        assert table.schema.names == SCHEMA.names
        got = table.to_pylist()
        assert [g["grant_id"] for g in got] == [f"g{i:04}" for i in range(5)]
        assert got[0]["recipient_ein_resolved"] == FEEDING and got[0]["match_tier"] == "B"
        assert (
            got[1]["recipient_ein_resolved"] == FEEDING
            and got[1]["recipient_bmf_name"] == "FEEDING AMERICA"
        )
        assert got[2]["match_tier"] == "U" and got[2]["match_method"] == "ambiguous_2_candidates"
        assert got[3]["match_tier"] == "A" and got[3]["recipient_ein_resolved"] == FBC_HOUSTON
        assert got[4]["match_tier"] == "U" and got[4]["recipient_ein_resolved"] is None

        # Second run: nothing pending, nothing changes.
        again = resolve(out, work, [2023])
        assert again.tuples_pending == 0 and again.rows_rewritten == 5
        assert pq.read_table(path).to_pylist() == got

        # Re-resolving the unresolved gives U rows another chance and leaves A/B alone.
        third = resolve(out, work, [2023], re_resolve_unresolved=True)
        assert third.tuples_pending == 2 and third.tier_counts == {"U": 2}
        state = duckdb.connect(str(work / "state.duckdb"), read_only=True)
        assert tier_distribution(state) == {"A": 1, "B": 1, "U": 2}
        state.close()

    def test_without_a_bmf_the_stage_refuses(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        (tmp_path / "dataset").mkdir()
        with pytest.raises(BmfMissing):
            resolve(tmp_path / "dataset", work, None)

    def test_grant_files_filters_years_and_skips_individuals(self, tmp_path: Path) -> None:
        out = tmp_path / "dataset"
        for year in (2022, 2023):
            (out / "grants" / f"filing_year={year}").mkdir(parents=True)
            (out / "grants" / f"filing_year={year}" / "part-00000.parquet").touch()
        (out / "grants_individuals" / "filing_year=2023").mkdir(parents=True)
        (out / "grants_individuals" / "filing_year=2023" / "part-00000.parquet").touch()
        assert [p.parent.name for p in grant_files(out, [2023])] == ["filing_year=2023"]
        assert len(grant_files(out, None)) == 2
        assert grant_files(tmp_path / "missing", None) == []

    def test_pending_is_empty_without_files(self, conn) -> None:
        conn.execute(
            "CREATE TABLE resolutions (name_normalized VARCHAR, name_raw VARCHAR, city VARCHAR, state VARCHAR, zip5 VARCHAR, ein_reported VARCHAR, recipient_type VARCHAR, tier VARCHAR)"
        )
        assert pending_recipients(conn, []) == []
