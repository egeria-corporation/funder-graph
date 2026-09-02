"""Grant extraction against real filings.

Every fixture under ``tests/fixtures/filings/`` is a genuine IRS e-file document from the
2023 bulk posting, trimmed to its return header and grant subtree with nothing anonymized
(they are public filings; see CONTRIBUTING). Mocked-shape tests would not catch schema
drift, and schema drift is the only failure mode that matters here.

The numbers asserted below were read off the filings by hand and cross-checked against each
filer's own reported totals before the code existed. If a test here starts failing after a
concordance re-vendor, the concordance moved — not the filing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from funder_graph import concordance
from funder_graph.cli import main
from funder_graph.extract import FilingError, _amount, extract

FIXTURES = Path(__file__).parent / "fixtures" / "filings"

BRODERICK = "2022v5.0__202333349349100703.xml"  # 4 paid grants to alumni associations
INDIVIDUALS = "2022v5.0__202303289349100000.xml"  # 7 orgs typed into the person slot
INDIVIDUALS_REAL = "2022v5.0__202313339349100601.xml"  # 8 hardship grants to named people
FOREIGN = "2022v5.0__202303319349100105.xml"  # one grant, Chilean recipient
OLDER = "2020v4.0__202303329349100200.xml"  # 9 grants, earlier schema version
FUTURE = "2021v4.0__202303319349100400.xml"  # 3 paid + 13 approved-for-future
SCHED_I = "2021v4.2__202303319349300220.xml"  # a public charity, Schedule I Part II


def load(name: str):
    object_id = name.split("__", 1)[1].removesuffix(".xml")
    return extract((FIXTURES / name).read_bytes(), object_id)


def test_every_logical_field_resolves_to_a_concordance_xpath() -> None:
    # The coverage gate at its smallest: if this fails, the extractor would be reading
    # nothing for that field and reporting nothing, which is the failure this project
    # exists to prevent. It also pins the Schedule I fallback in Concordance.xpaths().
    assert concordance.resolved().unresolved() == []


class TestForm990PF:
    def test_broderick_rows_reconcile_to_the_filers_own_total(self) -> None:
        # Milestone 1's exit criterion, exactly as the build spec states it: real grant rows
        # from one real filing, through the concordance, matching the filing.
        result = load(BRODERICK)
        assert result.filing.return_type == "990PF"
        assert result.filing.return_version == "2022v5.0"
        assert result.filing.funder_ein == "846725611"
        assert result.filing.funder_name == "BRODERICK CHARITABLE FOUNDATION TRUST"
        assert str(result.filing.tax_period_end) == "2022-12-31"

        paid = result.rows_of("paid")
        assert len(paid) == 4
        assert result.rows_of("approved_future") == []
        assert result.parsed_total("paid") == 50_000
        assert result.reported_total_paid == 50_000
        assert result.errors == []

    def test_broderick_first_row_field_by_field(self) -> None:
        row = load(BRODERICK).rows[0]
        assert row.group == "pf_paid"
        assert row.ordinal == 0
        assert row.amount_usd == 33_333
        assert row.recipient_name_raw == "IOWA STATE UNIVERSITY ALUMNI ASSOCIATION"
        assert row.address_line1 == "429 ALUMNI LANE"
        assert row.city == "AMES"
        assert row.state == "IA"
        assert row.zip_raw == "50011"
        assert row.country == "US"
        assert row.relationship == "NONE"
        assert row.foundation_status == "PC"
        assert row.purpose == "UNRESTRICTED GIFT"
        assert row.recipient_type == "organization"
        assert row.errors == []

    def test_organization_names_in_the_person_slot_are_reclassified(self) -> None:
        # This fixture was chosen because every row populates RecipientPersonNm and none
        # populates RecipientBusinessName. It looked like seven scholarships. It is seven
        # grants to organizations - THE TREVOR PROJECT, ACTIVE MINDS INC, YWCA OF GREATER
        # AUSTIN - that the filer typed into the wrong slot. A rule that trusts the slot
        # would drop all seven from the default edge view as payments to natural persons.
        result = load(INDIVIDUALS)
        assert len(result.rows) == 7
        assert all(r.recipient_person_name and r.recipient_name_raw is None for r in result.rows)
        types = {r.recipient_person_name: r.recipient_type for r in result.rows}
        assert types["THE TREVOR PROJECT"] == "organization"
        assert types["ACTIVE MINDS INC"] == "organization"
        assert types["YWCA OF GREATER AUSTIN"] == "organization"
        assert types["THE TRAVIS MILLS FOUNDATION"] == "organization"
        # BLACK MEN HEAL is a real nonprofit that no name-only rule can recognise. Tagging it
        # "individual" is the honest limit of the rule, and it is why individuals are tagged
        # and excluded from the default view rather than deleted: a wrong tag is recoverable.
        assert types["BLACK MEN HEAL"] == "individual"
        assert sum(t == "organization" for t in types.values()) >= 5

    def test_genuine_individual_recipients_are_tagged_and_reconcile(self) -> None:
        # Eight hardship grants to named people, reconciling to the filer's stated total.
        # These rows are what recipient_type = "individual" exists for: they are real grants
        # that must never appear in the published edge list with a person's name on them.
        result = load(INDIVIDUALS_REAL)
        # Seven, not eight: the filing has eight RecipientPersonNm elements, but one lives in
        # ApplicationSubmissionInfoGrp - the person applications are addressed to, which is
        # never a grant row and never published (docs/NON-GOALS.md). Counting person-name
        # elements across the whole document would have smuggled a contact into the graph.
        assert len(result.rows) == 7
        assert result.parsed_total("paid") == 263_775 == result.reported_total_paid
        types = {r.recipient_person_name: r.recipient_type for r in result.rows}
        # CARRIE BOHRER is the eighth RecipientPersonNm in this document: the person grant
        # applications are addressed to, with a home address and phone number, in
        # ApplicationSubmissionInfoGrp. She is not a grantee and must never become a row.
        # An earlier draft of this test asserted she was one - which is the exact leak
        # docs/NON-GOALS.md forbids, and the reason this assertion is phrased as an absence.
        assert "CARRIE BOHRER" not in types
        assert types["CHRISTOPHER HIPP"] == "individual"
        assert types["KATHLEEN HEISTER"] == "individual"
        # COMFORT CASES is a foster-care charity the filer typed into the person slot. Two
        # capitalized words with no organizational token look exactly like a person, and
        # a name-only rule cannot know better. Recorded here so the limit stays visible.
        assert types["COMFORT CASES"] == "individual"
        assert sum(t == "individual" for t in types.values()) >= 6

    def test_foreign_address_sets_country_and_has_no_us_state(self) -> None:
        result = load(FOREIGN)
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.country == "CI"
        assert row.city == "SANTIAGO DE CHILE"
        assert row.state is None
        assert row.zip_raw is None
        assert row.amount_usd == 9_758_900

    def test_an_aggregate_placeholder_row_is_flagged_not_published_as_an_individual(self) -> None:
        # The same filing: RecipientPersonNm is "VARIOUS ORGANIZATIONS" and the address is
        # "SEE ATTACHED SCHEDULE". The itemized grants live in an attachment. A naive rule
        # tags this $9.76M row as a scholarship to a person and drops it from the default
        # view; the empty-group detector never fires because the group is not empty. Both
        # failures are silent, which is why this filing is a fixture.
        result = load(FOREIGN)
        row = result.rows[0]
        assert row.recipient_person_name == "VARIOUS ORGANIZATIONS"
        assert row.is_aggregate_placeholder
        assert row.recipient_type == "unknown"
        assert any("aggregate placeholder" in e for e in row.errors)
        # This filer also omitted TotalGrantOrContriPdDurYrAmt entirely, so there is no
        # stated total for the filing-level detector to contradict. It must stay silent:
        # reporting a reconciliation failure against a number that does not exist would be
        # its own kind of false alarm. The row-level flag above is what carries this case.
        assert result.reported_total_paid is None
        assert not any(e.startswith("pf-missing-detail") for e in result.errors)

    def test_a_placeholder_row_beside_a_stated_total_is_missing_detail(self) -> None:
        # The same filing with the total the filer should have supplied. Now the structured
        # group is non-empty AND accounts for nothing itemized, and the filing-level detector
        # has a number to compare against. This is the shape the empty-group check misses.
        xml = (FIXTURES / FOREIGN).read_text(encoding="utf-8")
        injected = xml.replace(
            "<SupplementaryInformationGrp>",
            "<SupplementaryInformationGrp>\n"
            "      <TotalGrantOrContriPdDurYrAmt>9758900</TotalGrantOrContriPdDurYrAmt>",
            1,
        )
        assert injected != xml
        result = extract(injected.encode("utf-8"), "synthetic")
        assert result.reported_total_paid == 9_758_900
        assert len(result.rows) == 1 and result.rows[0].is_aggregate_placeholder
        flagged = [e for e in result.errors if e.startswith("pf-missing-detail")]
        assert flagged and "only aggregate placeholder rows" in flagged[0]

    def test_an_organization_name_in_the_person_slot_is_not_an_individual(self) -> None:
        # Constructed from the Broderick fixture: move a business name into RecipientPersonNm.
        xml = (FIXTURES / BRODERICK).read_text(encoding="utf-8")
        mutated = xml.replace(
            "<RecipientBusinessName>\n            <BusinessNameLine1Txt>IOWA STATE UNIVERSITY ALUMNI ASSOCIATION</BusinessNameLine1Txt>\n          </RecipientBusinessName>",
            "<RecipientPersonNm>IOWA STATE UNIVERSITY ALUMNI ASSOCIATION</RecipientPersonNm>",
            1,
        )
        assert mutated != xml, "fixture text changed; update the mutation"
        row = extract(mutated.encode("utf-8"), "synthetic").rows[0]
        assert row.recipient_person_name == "IOWA STATE UNIVERSITY ALUMNI ASSOCIATION"
        assert row.recipient_type == "organization"
        assert not row.is_aggregate_placeholder

    def test_earlier_schema_version_resolves_through_the_same_xpaths(self) -> None:
        # The concordance's `versions` column stops at 2016v3.0. This 2020v4.0 filing is
        # the proof that the xpaths, not the annotations, are what matter.
        result = load(OLDER)
        assert result.filing.return_version == "2020v4.0"
        assert len(result.rows_of("paid")) == 9
        assert all(r.amount_usd is not None for r in result.rows)
        assert result.errors == []

    def test_approved_future_is_a_separate_table_that_reconciles_separately(self) -> None:
        # The single most common analytical error with 990-PF data is summing these. The
        # extractor keeps them apart from birth and each reconciles to its own stated total.
        result = load(FUTURE)
        assert len(result.rows_of("paid")) == 3
        assert len(result.rows_of("approved_future")) == 13
        assert result.parsed_total("paid") == 10_000 == result.reported_total_paid
        assert result.parsed_total("approved_future") == 29_979 == result.reported_total_future
        assert {r.group for r in result.rows_of("approved_future")} == {"pf_future"}

    def test_a_stated_total_with_no_structured_rows_is_flagged_not_zeroed(self) -> None:
        # Part XV filed as an attachment: the filer reports a total but the structured group
        # is empty. That must surface as a loud error, never as "this foundation gave nothing".
        xml = (FIXTURES / BRODERICK).read_text(encoding="utf-8")
        start = xml.index("<GrantOrContributionPdDurYrGrp>")
        end = xml.rindex("</GrantOrContributionPdDurYrGrp>") + len(
            "</GrantOrContributionPdDurYrGrp>"
        )
        stripped = xml[:start] + xml[end:]
        result = extract(stripped.encode("utf-8"), "synthetic")
        assert result.rows == []
        assert result.reported_total_paid == 50_000
        assert any(e.startswith("pf-missing-detail") for e in result.errors)


class TestForm990ScheduleI:
    def test_recipient_table_rows_with_reported_eins(self) -> None:
        result = load(SCHED_I)
        assert result.filing.return_type == "990"
        assert result.filing.return_version == "2021v4.2"
        assert len(result.rows) == 3
        assert {r.group for r in result.rows} == {"sched_i"}
        assert {r.amount_type for r in result.rows} == {"paid"}

        first = result.rows[0]
        assert first.recipient_name_raw == "ANDALUSIA LLC 2112 INC"
        assert first.city == "CHICAGO"
        assert first.state == "IL"
        assert first.country == "US"
        assert first.recipient_ein_reported is not None
        assert len(first.recipient_ein_reported) == 9
        assert first.recipient_ein_reported.startswith("8327")
        assert first.amount_usd is not None and first.amount_usd > 0

    def test_every_schedule_i_row_carries_a_nine_digit_or_null_ein(self) -> None:
        for row in load(SCHED_I).rows:
            assert row.recipient_ein_reported is None or len(row.recipient_ein_reported) == 9


class TestAmounts:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("33333", 33_333),
            ("$1,234", 1_234),
            ("1234.00", 1_234),
            ("(500)", -500),
            (None, None),
        ],
    )
    def test_parses_what_filers_actually_write(self, raw, expected) -> None:
        errors: list[str] = []
        assert _amount(raw, errors, "x") == expected
        assert errors == []

    def test_garbage_is_an_error_never_a_zero(self) -> None:
        errors: list[str] = []
        assert _amount("N/A", errors, "row[3]") is None
        assert errors == ["row[3]: amount 'N/A' is not numeric"]


class TestGuards:
    def test_non_return_document_is_rejected(self) -> None:
        with pytest.raises(FilingError):
            extract(b"<html><body>Not a filing</body></html>", "x")

    def test_unsupported_return_type_is_an_error_not_an_empty_success(self) -> None:
        xml = (
            (FIXTURES / BRODERICK)
            .read_text(encoding="utf-8")
            .replace("<ReturnTypeCd>990PF</ReturnTypeCd>", "<ReturnTypeCd>990T</ReturnTypeCd>")
        )
        result = extract(xml.encode("utf-8"), "x")
        assert result.rows == []
        assert any("unsupported return type" in e for e in result.errors)


class TestCli:
    def test_parse_filing_json_is_the_milestone_demo(self) -> None:
        runner = CliRunner()
        out = runner.invoke(main, ["parse-filing", str(FIXTURES / BRODERICK), "--json"])
        assert out.exit_code == 0, out.output
        import json

        payload = json.loads(out.output)
        # The version prefix on the fixture filename must not leak into provenance.
        assert payload["filing"]["object_id"] == "202333349349100703"
        assert payload["parsed_total_paid"] == 50_000 == payload["reported_total_paid"]
        assert len(payload["rows"]) == 4
        assert payload["rows"][0]["recipient_type"] == "organization"
        assert payload["concordance_version"] == concordance.load().commit
        assert payload["errors"] == []

    def test_concordance_check_passes(self) -> None:
        out = CliRunner().invoke(main, ["concordance-check"])
        assert out.exit_code == 0, out.output
