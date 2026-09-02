"""The concordance: the only source of XPaths in this project.

Every field this project reads out of a filing is located through the Nonprofit Open Data
Collective's IRS E-file Master Concordance File, vendored under ``data/concordance/`` at the
commit pinned in ``data/upstream-pins.toml``. Nothing here writes an XPath by hand. Where the
concordance is wrong or incomplete, the fix is an entry in
``data/overrides/concordance-overrides.toml`` carrying an upstream issue URL, never a literal
in code.

Two facts about the upstream files shape this module, both established against real filings
rather than assumed (see ``data/upstream-pins.toml``):

* **Form 990-PF is a separate file.** ``concordance.csv`` carries the core 990 and every
  schedule, including Schedule I, and contains no 990-PF rows at all. Part XV lives in
  ``F990-PF-FULL.CSV``. A loader that reads only the main file reports zero coverage for the
  primary edge list and looks like a strategy crisis.
* **The ``versions`` column is stale, the XPaths are not.** Version annotations for the
  Part XV and Schedule I subtrees stop at 2016v3.0 / 2018v3.x, while the XPaths flagged
  ``current_version = T`` match 2019-2022 filings exactly - verified at 2020v4.0, 2021v4.2
  and 2022v5.0. So resolution selects current XPaths and does **not** gate on ``versions``;
  a missing annotation is an upstream metadata gap to report, not evidence the field is
  unmapped. Per-version presence is checked instead against ``raw-mappings/``, upstream's own
  per-schema-version XPath inventories, which run through 2022v5.0.
"""

from __future__ import annotations

import csv
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONCORDANCE_DIR = DATA_DIR / "concordance"
PINS_FILE = DATA_DIR / "upstream-pins.toml"

# Logical field names, in upstream's own ``variable_name_new`` vocabulary for 990-PF and
# ``variable_name`` for Schedule I. Using their names rather than inventing ours keeps our
# drift reports legible to the people who maintain the concordance.
PF_PAID_GROUP = (
    "/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContributionPdDurYrGrp"
)
PF_FUTURE_GROUP = (
    "/Return/ReturnData/IRS990PF/SupplementaryInformationGrp/GrantOrContriApprvForFutGrp"
)
SCHED_I_TABLE = "/Return/ReturnData/IRS990ScheduleI/RecipientTable"

PF_PAID_FIELDS: dict[str, str] = {
    "amount": "PF_15_G_PAID_AMT",
    "purpose": "PF_15_G_PAID_PURPOSE",
    "recipient_name_line1": "PF_15_G_PAID_RECIP_NAME_ORG_L1",
    "recipient_name_line2": "PF_15_G_PAID_RECIP_NAME_ORG_L2",
    "recipient_person_name": "PF_15_G_PAID_RECIP_NAME_PERS",
    "address_line1": "PF_15_G_PAID_RECIP_ADDR_L1",
    "address_line2": "PF_15_G_PAID_RECIP_ADDR_L2",
    "city": "PF_15_G_PAID_RECIP_ADDR_CITY",
    "state": "PF_15_G_PAID_RECIP_ADDR_STATE",
    "zip": "PF_15_G_PAID_RECIP_ADDR_ZIP",
    "country": "PF_15_G_PAID_RECIP_ADDR_CNTR",
    "relationship": "PF_15_G_PAID_RELATIONSHIP",
    "foundation_status": "PF_15_G_PAID_RECIP_STAT",
}
PF_FUTURE_FIELDS: dict[str, str] = {
    k: v.replace("PF_15_G_PAID_", "PF_15_G_FUTURE_") for k, v in PF_PAID_FIELDS.items()
}
PF_TOTALS: dict[str, str] = {
    "total_paid": "F9_15_PF_SUINTOGRORCO",  # TotalGrantOrContriPdDurYrAmt
    "total_future": "F9_15_PF_SITGOCAFUT",  # TotalGrantOrContriApprvFutAmt
}

# Schedule I uses the main file's original variable names.
SCHED_I_FIELDS: dict[str, str] = {
    "recipient_name_line1": "SI_02_GRANT_US_ORG_NAME_L1",
    "recipient_name_line2": "SI_02_GRANT_US_ORG_NAME_L2",
    "recipient_ein": "SI_02_GRANT_US_ORG_EIN",
    "address_line1": "SI_02_GRANT_US_ORG_ADDR_L1",
    "address_line2": "SI_02_GRANT_US_ORG_ADDR_L2",
    "city": "SI_02_GRANT_US_ORG_ADDR_CITY",
    "state": "SI_02_GRANT_US_ORG_ADDR_STATE",
    "zip": "SI_02_GRANT_US_ORG_ADDR_ZIP",
    "country": "SI_02_GRANT_US_ORG_ADDR_CNTR",
    "irc_section": "SI_02_GRANT_US_ORG_IRC_SECTION",
    "cash_amount": "SI_02_GRANT_US_ORG_AMT_CASH",
    "noncash_amount": "SI_02_GRANT_US_ORG_AMT_NONCSH",
    "valuation_method": "SI_02_GRANT_US_ORG_MOV",
    "noncash_description": "SI_02_GRANT_US_ORG_DESC_NONCSH",
    "purpose": "SI_02_GRANT_US_ORG_PURPOSE",
}
# Upstream files the Part I organization counts under the Part II prefix; keep their names.
SCHED_I_TOTALS: dict[str, str] = {
    "total_501c3_orgs": "SI_02_GRANT_US_ORG_501C3_TOT",
    "total_other_orgs": "SI_02_GRANT_US_ORG_OTH_TOT",
}


@dataclass(frozen=True)
class Entry:
    """One concordance row we care about."""

    logical: str
    xpath: str
    versions: frozenset[str]
    current: bool
    data_type: str
    form: str


@dataclass
class Concordance:
    """The vendored concordance, indexed for resolution."""

    commit: str
    entries: dict[str, list[Entry]] = field(default_factory=dict)
    _raw_mappings: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def xpaths(self, logical: str) -> list[str]:
        """Current-version XPaths for a logical field, most modern first.

        Deliberately not filtered by the ``versions`` column; see the module docstring. Old
        pre-``Grp`` XPaths are still returned, last, so a pre-2013 filing resolves too.
        """
        rows = self.entries.get(logical, [])
        current = [e for e in rows if e.current]
        # The 990-PF file flags its live rows ``current_version = T``. The main file leaves
        # that column empty for every Schedule I row, so treating "not flagged" as "not
        # current" silently drops the whole public-charity edge list. With no flag to go on,
        # every XPath is a candidate and modernity ordering does the work.
        candidates = current or rows
        return sorted({e.xpath for e in candidates}, key=_modernity, reverse=True)

    def relative(self, logical: str, under: str) -> list[str]:
        """XPaths for ``logical`` relative to the group container ``under``."""
        prefix = under.rstrip("/") + "/"
        return [x[len(prefix) :] for x in self.xpaths(logical) if x.startswith(prefix)]

    def present_in_version(self, xpath: str, return_version: str) -> bool | None:
        """Whether upstream's per-version inventory lists this XPath for this schema version.

        ``None`` means we have no inventory for that version — not "absent". The distinction
        is the whole point: a version we cannot check is reported as unchecked, never as
        unmapped.
        """
        inventory = self._raw_mappings.get(return_version)
        if inventory is None:
            return None
        return xpath in inventory

    @property
    def inventoried_versions(self) -> frozenset[str]:
        return frozenset(self._raw_mappings)


def _modernity(xpath: str) -> tuple[int, int]:
    """Sort key: the modern schema suffixes its leaves (Txt, Amt, Nm, Cd) and its groups (Grp)."""
    leaf = xpath.rsplit("/", 1)[-1]
    modern_leaf = int(leaf.endswith(("Txt", "Amt", "Nm", "Cd", "Ind", "Dt", "Cnt")))
    modern_grp = int("Grp/" in xpath)
    return (modern_leaf, modern_grp)


def _read(path: Path) -> Iterable[dict[str, str]]:
    # The upstream CSVs are UTF-8 with occasional stray bytes in descriptions; a hard error
    # on one bad description would block the whole loader for text we never use.
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        yield from csv.DictReader(handle)


def _entries(path: Path, name_column: str) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = {}
    for row in _read(path):
        logical = (row.get(name_column) or row.get("variable_name") or "").strip()
        xpath = (row.get("xpath") or "").strip()
        if not logical or not xpath:
            continue
        versions = frozenset(v.strip() for v in (row.get("versions") or "").split(";") if v.strip())
        out.setdefault(logical, []).append(
            Entry(
                logical=logical,
                xpath=xpath,
                versions=versions,
                current=(row.get("current_version") or "").strip().upper() == "T",
                data_type=(row.get("data_type_simple") or "").strip(),
                form=(row.get("form") or "").strip(),
            )
        )
    return out


def _raw_mappings(directory: Path) -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for path in sorted(directory.glob("mapping_*.csv")):
        version = path.stem.removeprefix("mapping_")
        out[version] = frozenset(
            (row.get("Xpath") or "").strip() for row in _read(path) if row.get("Xpath")
        )
    return out


@lru_cache(maxsize=1)
def load(directory: Path = CONCORDANCE_DIR) -> Concordance:
    """Load and index the vendored concordance. Cached: this is called per filing."""
    pins = tomllib.loads(PINS_FILE.read_text(encoding="utf-8"))
    commit = pins["concordance"]["commit"]
    entries = _entries(directory / "F990-PF-FULL.CSV", "variable_name_new")
    # The PF file also keys some rows (the Part XV totals) only by the original name.
    for logical, rows in _entries(directory / "F990-PF-FULL.CSV", "variable_name").items():
        entries.setdefault(logical, rows)
    for logical, rows in _entries(directory / "concordance.csv", "variable_name").items():
        entries.setdefault(logical, []).extend(rows)
    return Concordance(
        commit=commit,
        entries=entries,
        _raw_mappings=_raw_mappings(directory / "raw-mappings"),
    )


@dataclass(frozen=True)
class Resolved:
    """Every XPath the extractor will use, resolved once and reused across millions of filings."""

    concordance: Concordance

    @cached_property
    def pf_paid(self) -> dict[str, list[str]]:
        return {k: self.concordance.relative(v, PF_PAID_GROUP) for k, v in PF_PAID_FIELDS.items()}

    @cached_property
    def pf_future(self) -> dict[str, list[str]]:
        return {
            k: self.concordance.relative(v, PF_FUTURE_GROUP) for k, v in PF_FUTURE_FIELDS.items()
        }

    @cached_property
    def pf_totals(self) -> dict[str, list[str]]:
        return {k: self.concordance.xpaths(v) for k, v in PF_TOTALS.items()}

    @cached_property
    def sched_i(self) -> dict[str, list[str]]:
        return {k: self.concordance.relative(v, SCHED_I_TABLE) for k, v in SCHED_I_FIELDS.items()}

    @cached_property
    def sched_i_totals(self) -> dict[str, list[str]]:
        return {k: self.concordance.xpaths(v) for k, v in SCHED_I_TOTALS.items()}

    def unresolved(self) -> list[str]:
        """Logical fields for which the concordance yielded no XPath at all. Should be empty."""
        missing = []
        for label, table in (
            ("pf_paid", self.pf_paid),
            ("pf_future", self.pf_future),
            ("sched_i", self.sched_i),
        ):
            missing += [f"{label}.{k}" for k, v in table.items() if not v]
        return missing


@lru_cache(maxsize=1)
def resolved() -> Resolved:
    return Resolved(load())
