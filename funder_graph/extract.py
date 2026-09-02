"""Turn one filing's XML into grant rows.

This is the stage the project lives or dies on, and it is deliberately dumb: it walks the
repeating-group containers the concordance names and reads the leaves the concordance names,
and that is all. It knows nothing about schema versions. Version drift is the concordance's
problem to carry and ``build map``'s problem to measure; the extractor just asks for XPaths.

Three rules from the build spec are enforced here rather than downstream, because here is
where a silent failure would be born:

* **A missing grant must never look like a zero grant.** An amount that will not parse is a
  recorded error on the row, never ``0``. A filing whose grant group cannot be located at all
  is a recorded error on the filing, never an empty list.
* **Individuals are tagged at the source.** ``RecipientPersonNm`` populated means
  ``recipient_type = "individual"`` before any downstream code sees the row.
* **``approved_future`` is a different table.** Rows from the approved-for-future group carry
  ``amount_type = "approved_future"`` from birth and are never mixed with ``paid``.

Namespaces are stripped uniformly before any XPath is evaluated — the corpus uses
``http://www.irs.gov/efile`` throughout and the concordance writes namespace-free paths.
That is documented in ``docs/research/data-sources.md`` as the spec permits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from lxml import etree

from funder_graph.concordance import (
    PF_FUTURE_GROUP,
    PF_PAID_GROUP,
    SCHED_I_TABLE,
    Resolved,
    resolved,
)

EFILE_NS = "http://www.irs.gov/efile"

AmountType = Literal["paid", "approved_future"]
RecipientType = Literal["organization", "individual", "government", "unknown"]

# Header fields: the same on every form, so plain paths are acceptable here. These are the
# one place the spec lists explicit paths (data-sources.md §4c) rather than concordance targets.
_HEADER = {
    "ein": "ReturnHeader/Filer/EIN",
    "name1": "ReturnHeader/Filer/BusinessName/BusinessNameLine1Txt",
    "name2": "ReturnHeader/Filer/BusinessName/BusinessNameLine2Txt",
    "state": "ReturnHeader/Filer/USAddress/StateAbbreviationCd",
    "tax_period_end": "ReturnHeader/TaxPeriodEndDt",
    "return_type": "ReturnHeader/ReturnTypeCd",
}

_GOVERNMENT = re.compile(
    r"\b(CITY|COUNTY|TOWN|TOWNSHIP|VILLAGE|BOROUGH|PARISH|STATE) OF\b|"
    r"\b(DEPARTMENT|DEPT|BUREAU|OFFICE) OF\b|\bSCHOOL DISTRICT\b|\bPUBLIC SCHOOLS?\b|"
    r"\bUNITED STATES\b|\bU\.?S\.? (ARMY|NAVY|AIR FORCE|MARINE|COAST GUARD|TREASURY)\b",
    re.I,
)

# Tokens that make a "person name" not a person. Filers put organization names in
# RecipientPersonNm routinely; the spec's own rule is "populated *with no organizational
# tokens*", and this is the token list that clause needs.
_ORG_TOKENS = re.compile(
    r"\b(INC|INCORPORATED|LLC|LTD|CORP|CORPORATION|CO|COMPANY|FOUNDATION|FDN|FUND|TRUST|"
    r"ASSOCIATION|ASSN|SOCIETY|SOC|INSTITUTE|UNIVERSITY|UNIV|COLLEGE|SCHOOL|CHURCH|"
    r"MINISTRY|MINISTRIES|HOSPITAL|CLINIC|CENTER|CENTRE|COUNCIL|COMMITTEE|CLUB|LEAGUE|"
    r"ORGANIZATION|ORGANIZATIONS|CHARITY|CHARITIES|MUSEUM|LIBRARY|THEATER|THEATRE|"
    r"ORCHESTRA|ALLIANCE|COALITION|NETWORK|PARTNERSHIP|PROJECT|PROGRAM|SERVICES|"
    r"DEPARTMENT|AGENCY|AUTHORITY|DISTRICT|CITY|COUNTY|STATE|"
    # The chapter organizations the build spec names as the highest-risk matches. A filer
    # who writes "YWCA OF GREATER AUSTIN" in the person slot did not fund a person.
    r"YMCA|YWCA|UNITED WAY|GOODWILL|HABITAT|ROTARY|KIWANIS|LIONS|ELKS|SCOUTS|"
    r"SALVATION ARMY|RED CROSS|BOYS|GIRLS)\b",
    re.I,
)

# A row that stands in for an attached list rather than naming one recipient. These are
# counted into the missing-detail report, never published as an edge to "VARIOUS".
_PLACEHOLDER = re.compile(
    r"\b(VARIOUS|MISCELLANEOUS|MISC|MULTIPLE|NUMEROUS|SEVERAL|N/?A|NONE)\b|"
    r"\bSEE (ATTACHED|ATTACHMENT|SCHEDULE|STATEMENT|LIST)\b|\bATTACHED (SCHEDULE|LIST)\b",
    re.I,
)


@dataclass(frozen=True)
class Filing:
    object_id: str
    return_version: str
    return_type: str
    funder_ein: str
    funder_name: str
    funder_state: str | None
    tax_period_end: date | None


@dataclass
class GrantRow:
    """One grant line, exactly as filed, with only the classification the source supports."""

    filing: Filing
    group: str  # "pf_paid" | "pf_future" | "sched_i"
    ordinal: int  # 0-based position within its group in this filing; part of grant_id
    amount_type: AmountType
    amount_usd: int | None
    noncash_amount_usd: int | None
    purpose: str | None
    recipient_name_raw: str | None
    recipient_person_name: str | None
    recipient_ein_reported: str | None
    address_line1: str | None
    city: str | None
    state: str | None
    zip_raw: str | None
    country: str | None  # ISO-2; "US" when a USAddress was present
    relationship: str | None
    foundation_status: str | None
    irc_section: str | None
    errors: list[str] = field(default_factory=list)

    @property
    def is_aggregate_placeholder(self) -> bool:
        """The row stands in for a list the filer attached instead of itemizing.

        Real example, a 2022 990-PF: ``RecipientPersonNm = "VARIOUS ORGANIZATIONS"``,
        address "SEE ATTACHED SCHEDULE", amount $9,758,900. It is one structured row, so the
        empty-group detector does not fire, and the person-name slot is populated, so a naive
        rule tags a $9.76M edge as a scholarship to a natural person and drops it from the
        default view. Neither is acceptable; this row is a known limitation to be counted.
        """
        name = self.recipient_name_raw or self.recipient_person_name or ""
        return bool(_PLACEHOLDER.search(name)) or bool(
            self.address_line1 and _PLACEHOLDER.search(self.address_line1)
        )

    @property
    def recipient_type(self) -> RecipientType:
        if self.is_aggregate_placeholder:
            return "unknown"
        person = self.recipient_person_name or ""
        # A populated person-name slot means "individual" only when it does not carry
        # organizational tokens. Filers put organization names in that slot routinely.
        if person and not self.recipient_name_raw and not _ORG_TOKENS.search(person):
            return "individual"
        name = self.recipient_name_raw or person
        if name and _GOVERNMENT.search(name):
            return "government"
        if name:
            return "organization"
        return "unknown"


@dataclass
class Extraction:
    filing: Filing
    rows: list[GrantRow]
    # The filer's own totals, for reconciliation. None when the element is absent.
    reported_total_paid: int | None
    reported_total_future: int | None
    reported_501c3_org_count: int | None
    reported_other_org_count: int | None
    errors: list[str] = field(default_factory=list)

    def rows_of(self, amount_type: AmountType) -> list[GrantRow]:
        return [r for r in self.rows if r.amount_type == amount_type]

    def parsed_total(self, amount_type: AmountType) -> int:
        return sum(r.amount_usd or 0 for r in self.rows_of(amount_type))


class FilingError(ValueError):
    """The document is not a parseable IRS e-file return."""


def parse_xml(data: bytes) -> etree._Element:
    """Parse and strip the e-file namespace uniformly. Never resolves external entities."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.fromstring(data, parser)
    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.startswith("{"):
            element.tag = element.tag.split("}", 1)[1]
    if root.tag != "Return":
        raise FilingError(f"root element is <{root.tag}>, expected <Return>")
    return root


def _text(node: etree._Element, paths: list[str]) -> str | None:
    """First non-empty text among candidate relative XPaths, most modern first."""
    for path in paths:
        hit = node.find(path)
        if hit is not None and hit.text and hit.text.strip():
            return hit.text.strip()
    return None


def _amount(raw: str | None, errors: list[str], label: str) -> int | None:
    """Integer USD, or None with a recorded error. Never coerces garbage to zero."""
    if raw is None:
        return None
    cleaned = raw.replace("$", "").replace(",", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = round(float(cleaned))
    except ValueError:
        errors.append(f"{label}: amount {raw!r} is not numeric")
        return None
    return -value if negative else value


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _header(root: etree._Element, object_id: str) -> Filing:
    ein = _text(root, [_HEADER["ein"]]) or ""
    digits = re.sub(r"\D", "", ein)
    name = " ".join(
        filter(None, (_text(root, [_HEADER["name1"]]), _text(root, [_HEADER["name2"]])))
    )
    return Filing(
        object_id=object_id,
        return_version=root.get("returnVersion", ""),
        return_type=_text(root, [_HEADER["return_type"]]) or "",
        funder_ein=digits.zfill(9) if 8 <= len(digits) <= 9 else digits,
        funder_name=name,
        funder_state=_text(root, [_HEADER["state"]]),
        tax_period_end=_date(_text(root, [_HEADER["tax_period_end"]])),
    )


def _country(node: etree._Element, paths: dict[str, list[str]]) -> str | None:
    """ "US" if the row used a US address container, else the stated country, else None."""
    if node.find("RecipientUSAddress") is not None or node.find("USAddress") is not None:
        return "US"
    return _text(node, paths["country"])


def _pf_rows(
    root: etree._Element,
    filing: Filing,
    group_xpath: str,
    fields: dict[str, list[str]],
    amount_type: AmountType,
    label: str,
) -> list[GrantRow]:
    container = group_xpath.removeprefix("/Return/")
    rows: list[GrantRow] = []
    for ordinal, node in enumerate(root.findall(container)):
        errors: list[str] = []
        name = " ".join(
            filter(
                None,
                (
                    _text(node, fields["recipient_name_line1"]),
                    _text(node, fields["recipient_name_line2"]),
                ),
            )
        )
        rows.append(
            GrantRow(
                filing=filing,
                group=label,
                ordinal=ordinal,
                amount_type=amount_type,
                amount_usd=_amount(_text(node, fields["amount"]), errors, f"{label}[{ordinal}]"),
                noncash_amount_usd=None,
                purpose=_text(node, fields["purpose"]),
                recipient_name_raw=name or None,
                recipient_person_name=_text(node, fields["recipient_person_name"]),
                recipient_ein_reported=None,
                address_line1=_text(node, fields["address_line1"]),
                city=_text(node, fields["city"]),
                state=_text(node, fields["state"]),
                zip_raw=_text(node, fields["zip"]),
                country=_country(node, fields),
                relationship=_text(node, fields["relationship"]),
                foundation_status=_text(node, fields["foundation_status"]),
                irc_section=None,
                errors=errors,
            )
        )
    return rows


def _sched_i_rows(
    root: etree._Element, filing: Filing, fields: dict[str, list[str]]
) -> list[GrantRow]:
    rows: list[GrantRow] = []
    for ordinal, node in enumerate(root.findall(SCHED_I_TABLE.removeprefix("/Return/"))):
        errors: list[str] = []
        name = " ".join(
            filter(
                None,
                (
                    _text(node, fields["recipient_name_line1"]),
                    _text(node, fields["recipient_name_line2"]),
                ),
            )
        )
        ein_raw = _text(node, fields["recipient_ein"])
        ein = re.sub(r"\D", "", ein_raw) if ein_raw else None
        if ein is not None and len(ein) != 9:
            errors.append(f"sched_i[{ordinal}]: reported EIN {ein_raw!r} is not 9 digits")
            ein = None
        rows.append(
            GrantRow(
                filing=filing,
                group="sched_i",
                ordinal=ordinal,
                amount_type="paid",
                amount_usd=_amount(
                    _text(node, fields["cash_amount"]), errors, f"sched_i[{ordinal}]"
                ),
                noncash_amount_usd=_amount(
                    _text(node, fields["noncash_amount"]), errors, f"sched_i[{ordinal}] noncash"
                ),
                purpose=_text(node, fields["purpose"]),
                recipient_name_raw=name or None,
                recipient_person_name=None,
                recipient_ein_reported=ein,
                address_line1=_text(node, fields["address_line1"]),
                city=_text(node, fields["city"]),
                state=_text(node, fields["state"]),
                zip_raw=_text(node, fields["zip"]),
                country=_country(node, fields),
                relationship=None,
                foundation_status=None,
                irc_section=_text(node, fields["irc_section"]),
                errors=errors,
            )
        )
    return rows


def _int_or_none(
    root: etree._Element, paths: list[str], errors: list[str], label: str
) -> int | None:
    return _amount(_text(root, [p.removeprefix("/Return/") for p in paths]), errors, label)


def extract(data: bytes, object_id: str, xpaths: Resolved | None = None) -> Extraction:
    """Extract every grant row from one filing.

    ``object_id`` is the IRS identifier for this document and is provenance for every row;
    it is the ZIP member's basename with ``_public.xml`` removed.
    """
    xp = xpaths or resolved()
    root = parse_xml(data)
    filing = _header(root, object_id)
    errors: list[str] = []
    rows: list[GrantRow] = []

    if filing.return_type == "990PF":
        rows += _pf_rows(root, filing, PF_PAID_GROUP, xp.pf_paid, "paid", "pf_paid")
        rows += _pf_rows(
            root, filing, PF_FUTURE_GROUP, xp.pf_future, "approved_future", "pf_future"
        )
        total_paid = _int_or_none(
            root, xp.pf_totals["total_paid"], errors, "TotalGrantOrContriPdDurYrAmt"
        )
        total_future = _int_or_none(
            root, xp.pf_totals["total_future"], errors, "TotalGrantOrContriApprvFutAmt"
        )
        # The detector for attachment-reported Part XV. Two shapes, both real: a stated
        # total with no structured rows at all, and a stated total whose only structured
        # rows are aggregate placeholders ("VARIOUS ORGANIZATIONS, SEE ATTACHED SCHEDULE").
        # Either way the itemized grants exist only in an attachment we cannot parse, and
        # that has to be counted and published, never mistaken for "gave nothing".
        for row in rows:
            if row.is_aggregate_placeholder:
                row.errors.append(
                    f"{row.group}[{row.ordinal}]: aggregate placeholder, not an itemized recipient"
                )
        itemized = [r for r in rows if not r.is_aggregate_placeholder]
        if not itemized and (total_paid or 0) > 0:
            errors.append(
                f"pf-missing-detail: reported total paid {total_paid} but "
                f"{'no structured Part XV rows' if not rows else 'only aggregate placeholder rows'}"
            )
        return Extraction(filing, rows, total_paid, total_future, None, None, errors)

    if filing.return_type == "990":
        rows += _sched_i_rows(root, filing, xp.sched_i)
        c3 = _int_or_none(root, xp.sched_i_totals["total_501c3_orgs"], errors, "Total501c3OrgCnt")
        other = _int_or_none(
            root, xp.sched_i_totals["total_other_orgs"], errors, "TotalOtherOrgCnt"
        )
        return Extraction(filing, rows, None, None, c3, other, errors)

    errors.append(
        f"unsupported return type {filing.return_type!r}; only 990 and 990PF carry grant edges"
    )
    return Extraction(filing, rows, None, None, None, None, errors)
