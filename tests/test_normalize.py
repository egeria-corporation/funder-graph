"""The name canon, against real names.

Every input here is a name that appears in a committed filing or in the build spec's list of
hard cases. The point of each assertion is a collision that should happen or one that should
not, and both kinds are stated.
"""

from __future__ import annotations

from datetime import date

import pytest

from funder_graph.resolve.normalize import (
    is_chapter_organization,
    normalize_name,
    split_aliases,
    tax_year,
    zip5,
)


class TestNormalizeName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # From the committed filings.
            ("BOYS & GIRLS CLUBS OF AMERICA", "BOYS AND GIRLS CLUBS OF AMERICA"),
            ("THE TREVOR PROJECT", "TREVOR PROJECT"),
            ("ACTIVE MINDS INC", "ACTIVE MINDS"),
            ("HEADSTRONG PROJECT INC", "HEADSTRONG PROJECT"),
            ("IOWA STATE UNIVERSITY ALUMNI ASSOCIATION", "IOWA STATE UNIV ALUMNI ASSN"),
            (
                "KWAME NKRUMAH UNIVERSITY OF SCIENCE & TECHNOLOGY",
                "KWAME NKRUMAH UNIV OF SCIENCE AND TECHNOLOGY",
            ),
            ("SEQUOIA CLIMATE FOUNDATION", "SEQUOIA CLIMATE FDN"),
            # The spec's hard cases.
            ("The David and Lucile Packard Foundation", "DAVID AND LUCILE PACKARD FDN"),
            (
                "PRESIDENT AND FELLOWS OF HARVARD COLLEGE",
                "PRESIDENT AND FELLOWS OF HARVARD COLLEGE",
            ),
            ("St. Jude Children's Research Hospital, Inc.", "ST JUDE CHILDRENS RESEARCH HOSPITAL"),
            (
                "Saint Jude Children's Research Hospital Incorporated",
                "ST JUDE CHILDRENS RESEARCH HOSPITAL",
            ),
            ("Amnesty International USA", "AMNESTY INTL USA"),
            ("Humane Society of the United States", "HUMANE SOC OF THE UNITED STATES"),
            ("ACME CORPORATION", "ACME CORP"),
            # Whitespace and punctuation noise.
            ("  Feeding   America  ", "FEEDING AMERICA"),
            ("FEEDING-AMERICA", "FEEDING AMERICA"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_canonical_forms(self, raw, expected) -> None:
        assert normalize_name(raw) == expected

    def test_two_spellings_of_one_organization_collide(self) -> None:
        # The purpose of the whole function, in one assertion.
        a = normalize_name("The St. Jude Children's Research Hospital, Inc.")
        b = normalize_name("SAINT JUDE CHILDRENS RESEARCH HOSPITAL INCORPORATED")
        assert a == b == "ST JUDE CHILDRENS RESEARCH HOSPITAL"

    def test_different_organizations_do_not_collide(self) -> None:
        # Normalization must not be so aggressive that it merges distinct entities.
        assert normalize_name("UNITED WAY OF METRO CHICAGO") != normalize_name(
            "UNITED WAY OF GREATER ATLANTA"
        )
        assert normalize_name("BOYS AND GIRLS CLUB OF BOSTON") != normalize_name(
            "BOYS AND GIRLS CLUB OF DENVER"
        )

    def test_only_a_leading_the_is_dropped(self) -> None:
        assert (
            normalize_name("HUMANE SOCIETY OF THE UNITED STATES")
            == "HUMANE SOC OF THE UNITED STATES"
        )

    def test_only_a_trailing_inc_is_dropped(self) -> None:
        # INC in the middle is part of a name ("INC MAGAZINE FUND") and stays.
        assert normalize_name("INC MAGAZINE FUND INC") == "INC MAGAZINE FUND"


class TestAliases:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("COMMUNITY PARTNERS DBA FOOD BANK OF X", ["COMMUNITY PARTNERS", "FOOD BANK OF X"]),
            ("Community Partners d/b/a Food Bank", ["Community Partners", "Food Bank"]),
            (
                "OLD NAME INC AKA NEW NAME FKA OLDER NAME",
                ["OLD NAME INC", "NEW NAME", "OLDER NAME"],
            ),
            ("NO ALIAS HERE", ["NO ALIAS HERE"]),
            ("", []),
            (None, []),
        ],
    )
    def test_split(self, raw, expected) -> None:
        assert split_aliases(raw) == expected

    def test_a_word_containing_dba_letters_is_not_split(self) -> None:
        assert split_aliases("ADBACH FOUNDATION") == ["ADBACH FOUNDATION"]


class TestChapterOrganizations:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("YWCA OF GREATER AUSTIN", True),
            ("UNITED WAY OF METRO CHICAGO", True),
            ("BOYS AND GIRLS CLUB OF BOSTON", True),
            ("HABITAT FOR HUMANITY OF GREATER DENVER", True),
            ("AMERICAN RED CROSS", True),
            ("FEEDING AMERICA", False),
            ("SEQUOIA CLIMATE FDN", False),
        ],
    )
    def test_detection(self, name, expected) -> None:
        assert is_chapter_organization(name) is expected


class TestSmallHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("94022-1234", "94022"),
            ("940221234", "94022"),
            ("50011", "50011"),
            ("9402", None),
            (None, None),
            ("", None),
        ],
    )
    def test_zip5(self, raw, expected) -> None:
        assert zip5(raw) == expected

    def test_tax_year_is_the_calendar_year_of_the_period_end(self) -> None:
        assert tax_year(date(2022, 12, 31)) == 2022
        assert tax_year(date(2023, 6, 30)) == 2023
        assert tax_year(None) is None
