"""The string canon: one normalization, shared by matching and by the published column.

These are the build spec's rules, verbatim, in the order it gives them. They are deliberately
conservative: the goal is to make the same organization written two ways collide, not to
make different organizations collide. Every rule here is exercised on real names from the
committed filings, and any change must move precision and recall on the labeled set.

``recipient_name_raw`` is never touched. Typos, abbreviations and DBA names are preserved
there on purpose; this is the matching form, published alongside so a reader can see what
was compared.
"""

from __future__ import annotations

import re
from datetime import date

# Legal-form and common-word canonical forms, from the spec. Applied token by token, so
# "FOUNDATION" inside "FOUNDATION FOR X" still becomes "FDN" - that is intended: the point is
# that the same organization written both ways lands on the same string.
_CANON: dict[str, str] = {
    "INCORPORATED": "INC",
    "CORPORATION": "CORP",
    "FOUNDATION": "FDN",
    "UNIVERSITY": "UNIV",
    "ASSOCIATION": "ASSN",
    "SOCIETY": "SOC",
    "INTERNATIONAL": "INTL",
    "SAINT": "ST",
}

# Chapter organizations: hundreds of distinct EINs with near-identical names, where only
# geography separates them. The matcher caps these at tier C unless ZIP5 or city agrees.
_CHAPTER = re.compile(
    r"\b(BOYS AND GIRLS CLUB|UNITED WAY|HABITAT FOR HUMANITY|YMCA|YWCA|GOODWILL|"
    r"BIG BROTHERS BIG SISTERS|SALVATION ARMY|AMERICAN RED CROSS|RED CROSS|ROTARY|KIWANIS)\b"
)

_ALIAS_SPLIT = re.compile(r"\b(?:D/?B/?A|A/?K/?A|F/?K/?A)\b\.?", re.I)
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_name(raw: str | None) -> str:
    """The spec's normalization, in the spec's order. Empty input normalizes to empty."""
    if not raw:
        return ""
    s = raw.upper()
    s = s.replace("&", " AND ")
    # Apostrophes are deleted, not spaced: "CHILDREN'S" must become CHILDRENS, the form
    # filers write when they drop the punctuation, not "CHILDREN S". Every other mark
    # (hyphen, slash, period, comma) becomes a space so "FEEDING-AMERICA" splits.
    s = s.replace("'", "").replace("\u2019", "").replace("\u2018", "")
    s = _NON_ALNUM.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    tokens = s.split(" ") if s else []
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    tokens = [_CANON.get(t, t) for t in tokens]
    # Drop a trailing INC (after canonicalization, so "INCORPORATED" is caught too).
    while tokens and tokens[-1] == "INC":
        tokens.pop()
    return " ".join(tokens)


def split_aliases(raw: str | None) -> list[str]:
    """``"X DBA Y"`` -> ``["X", "Y"]``; both forms are match candidates. Order preserved."""
    if not raw:
        return []
    parts = [p.strip(" ,;-") for p in _ALIAS_SPLIT.split(raw)]
    return [p for p in parts if p]


def is_chapter_organization(normalized: str) -> bool:
    """True for names the spec lists as needing geographic corroboration to match."""
    return bool(_CHAPTER.search(normalized))


def zip5(raw: str | None) -> str | None:
    """First five digits of a ZIP, or None. ``"94022-1234"`` and ``"940221234"`` both -> 94022."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits[:5] if len(digits) >= 5 else None


def tax_year(tax_period_end: date | None) -> int | None:
    """The calendar year of the period end, per the spec."""
    return tax_period_end.year if tax_period_end else None
