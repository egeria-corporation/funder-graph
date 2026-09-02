"""Stage 3: measure what the concordance covers, before parsing anything for keeps.

"This is the stage the whole project lives or dies on." Three reports come out of one pass
over the corpus, and the first of them is the milestone's exit criterion:

* ``version-coverage.csv`` — every ``returnVersion`` in the corpus, how many grant-bearing
  filings carry it, how many of those have a non-empty grant group, and for each required
  logical field, how many of *those* resolved it. The headline number is the share of
  filings-with-rows for which every required field resolved. If it is below ~95% by volume,
  the build prompt says stop and ask; the number is printed before anything else is built.
* ``xpath-version-count.csv`` — for every element path under the grant subtrees, the schema
  versions it appears in and the count of filings carrying it. This is the 990-PF
  counterpart to the Nonprofit Open Data Collective's ``draft-updates/XPATH-VERSION-COUNT.CSV``,
  emitted in their exact three columns so it can be merged upstream rather than argued about.
  It is built from filings, as theirs is, not from schemas.
* ``unmapped-fields.csv`` — paths present in real filings under those subtrees that this
  pipeline does not consume, with counts. Drift the concordance has not caught up to, and
  raw material for upstream issues. Some entries are deliberate: the application-contact
  fields must never be consumed (``docs/NON-GOALS.md``), and they appear here on purpose.

Why leaf coverage is measured only on filings whose grant group is non-empty: a 990-PF with
zero Part XV rows cannot tell us whether ``RecipientUSAddress/CityNm`` resolves, and counting
it as a miss would report a mapping failure for what is simply a foundation that made no
grants that year. Conflating those two is the exact silent failure this stage exists to
prevent.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from funder_graph.concordance import (
    PF_FUTURE_FIELDS,
    PF_FUTURE_GROUP,
    PF_PAID_FIELDS,
    PF_PAID_GROUP,
    SCHED_I_FIELDS,
    SCHED_I_TABLE,
    resolved,
)
from funder_graph.extract import parse_xml
from funder_graph.pipeline.extract import iter_filings

# The fields a row cannot be published without. Optional leaves (address line 2, relationship,
# foundation status, non-cash) are inventoried but do not count against coverage.
REQUIRED_PF = ("amount", "purpose", "recipient_name_line1", "recipient_person_name", "city")
REQUIRED_SCHED_I = ("cash_amount", "purpose", "recipient_name_line1", "recipient_ein", "city")

# Subtrees whose element paths are inventoried and diffed. Root-relative, as the concordance
# writes them.
SUBTREES = (
    PF_PAID_GROUP,
    PF_FUTURE_GROUP,
    SCHED_I_TABLE,
    "/Return/ReturnData/IRS990PF/SupplementaryInformationGrp",
)


@dataclass
class VersionStats:
    """Everything counted for one (returnVersion, return_type)."""

    filings: int = 0
    with_rows: int = 0
    field_hits: Counter = field(default_factory=Counter)
    fully_resolved: int = 0


@dataclass
class Tally:
    """Counters from one archive, mergeable across workers."""

    versions: dict[tuple[str, str], VersionStats] = field(default_factory=dict)
    # (xpath) -> Counter(version -> filings carrying it)
    inventory: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    parse_errors: Counter = field(default_factory=Counter)
    filings_seen: int = 0

    def merge(self, other: Tally) -> None:
        for key, s in other.versions.items():
            mine = self.versions.setdefault(key, VersionStats())
            mine.filings += s.filings
            mine.with_rows += s.with_rows
            mine.field_hits.update(s.field_hits)
            mine.fully_resolved += s.fully_resolved
        for xpath, counter in other.inventory.items():
            self.inventory[xpath].update(counter)
        self.parse_errors.update(other.parse_errors)
        self.filings_seen += other.filings_seen


def _paths_under(root: etree._Element, subtree: str) -> set[str]:
    """Every distinct element path under ``subtree`` present in this document, root-relative."""
    rel = subtree.removeprefix("/Return/")
    found: set[str] = set()
    for container in root.findall(rel):
        stack = [(container, subtree)]
        while stack:
            node, path = stack.pop()
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                child_path = f"{path}/{child.tag}"
                found.add(child_path)
                stack.append((child, child_path))
    return found


def _resolves(node: etree._Element, rel_paths: list[str]) -> bool:
    return any(
        (hit := node.find(p)) is not None and hit.text and hit.text.strip() for p in rel_paths
    )


def tally_filing(data: bytes, object_id: str) -> tuple[Tally, str | None]:
    """Count one filing. Returns the tally and an error string if it could not be parsed."""
    t = Tally()
    try:
        root = parse_xml(data)
    except Exception as error:  # noqa: BLE001 - a corrupt member must be counted, not fatal
        t.parse_errors[type(error).__name__] += 1
        return t, f"{object_id}: {error}"

    version = root.get("returnVersion", "?")
    return_type = (root.findtext("ReturnHeader/ReturnTypeCd") or "?").strip()
    t.filings_seen = 1
    if return_type not in ("990PF", "990"):
        return t, None

    xp = resolved()
    stats = t.versions.setdefault((version, return_type), VersionStats())
    stats.filings += 1

    if return_type == "990PF":
        groups = root.findall(PF_PAID_GROUP.removeprefix("/Return/")) + root.findall(
            PF_FUTURE_GROUP.removeprefix("/Return/")
        )
        fields = {**{k: xp.pf_paid[k] for k in REQUIRED_PF}}
        future_fields = {k: xp.pf_future[k] for k in REQUIRED_PF}
        required = REQUIRED_PF
    else:
        groups = root.findall(SCHED_I_TABLE.removeprefix("/Return/"))
        fields = {k: xp.sched_i[k] for k in REQUIRED_SCHED_I}
        future_fields = {}
        required = REQUIRED_SCHED_I

    if groups:
        stats.with_rows += 1
        hits: set[str] = set()
        for g in groups:
            table = future_fields if g.tag == "GrantOrContriApprvForFutGrp" else fields
            for name, rel_paths in table.items():
                if _resolves(g, rel_paths):
                    hits.add(name)
        # A name is required only in the sense that *one of* the two name slots must fill:
        # an all-individuals filing legitimately has no business names, and vice versa.
        if "recipient_name_line1" in hits or "recipient_person_name" in hits:
            hits.update({"recipient_name_line1", "recipient_person_name"})
        for name in hits:
            stats.field_hits[name] += 1
        if all(name in hits for name in required):
            stats.fully_resolved += 1

    for subtree in SUBTREES:
        for path in _paths_under(root, subtree):
            t.inventory[path][version] += 1
    return t, None


def tally_archive(zip_path: Path, wanted: set[str]) -> tuple[Tally, list[str]]:
    """One worker's share: every wanted filing in one archive."""
    total = Tally()
    errors: list[str] = []
    for object_id, data in iter_filings(zip_path, only=wanted):
        t, err = tally_filing(data, object_id)
        total.merge(t)
        if err:
            errors.append(err)
    return total, errors


def tally_corpus(
    archives: Iterable[Path], wanted: set[str], *, workers: int | None = None
) -> tuple[Tally, list[str]]:
    """Parallel by archive, one process each, merged in the parent. No shared DuckDB."""
    archives = list(archives)
    total = Tally()
    errors: list[str] = []
    if workers == 1 or len(archives) <= 1:
        for z in archives:
            t, e = tally_archive(z, wanted)
            total.merge(t)
            errors += e
        return total, errors
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for t, e in pool.map(tally_archive, archives, [wanted] * len(archives)):
            total.merge(t)
            errors += e
    return total, errors


# ---------------------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------------------


def consumed_xpaths() -> set[str]:
    """Every absolute XPath the extractor can read, from the concordance."""
    xp = resolved()
    out: set[str] = set()
    for group, table in (
        (PF_PAID_GROUP, xp.pf_paid),
        (PF_FUTURE_GROUP, xp.pf_future),
        (SCHED_I_TABLE, xp.sched_i),
    ):
        for rel_paths in table.values():
            out.update(f"{group}/{p}" for p in rel_paths)
    for paths in (*xp.pf_totals.values(), *xp.sched_i_totals.values()):
        out.update(paths)
    # Container paths themselves are "consumed" by walking them.
    out.update({PF_PAID_GROUP, PF_FUTURE_GROUP, SCHED_I_TABLE})
    # Intermediate containers on the way to a consumed leaf are consumed too.
    for leaf in list(out):
        parts = leaf.split("/")
        for i in range(2, len(parts)):
            out.add("/".join(parts[:i]))
    return out


def write_version_coverage(t: Tally, path: Path) -> float:
    """``version-coverage.csv``. Returns the headline: share of filings-with-rows fully resolved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    names = sorted(
        {n for s in t.versions.values() for n in s.field_hits}
        | set(REQUIRED_PF)
        | set(REQUIRED_SCHED_I)
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(
            [
                "return_version",
                "return_type",
                "filings",
                "with_grant_rows",
                "fully_resolved",
                "fully_resolved_pct",
                *names,
            ]
        )
        for (version, rtype), s in sorted(t.versions.items()):
            pct = (100.0 * s.fully_resolved / s.with_rows) if s.with_rows else ""
            w.writerow(
                [
                    version,
                    rtype,
                    s.filings,
                    s.with_rows,
                    s.fully_resolved,
                    f"{pct:.2f}" if pct != "" else "",
                    *(s.field_hits.get(n, 0) for n in names),
                ]
            )
    with_rows = sum(s.with_rows for s in t.versions.values())
    full = sum(s.fully_resolved for s in t.versions.values())
    return (100.0 * full / with_rows) if with_rows else 0.0


def write_xpath_version_count(t: Tally, path: Path) -> int:
    """The 990-PF/Schedule I ``XPATH-VERSION-COUNT`` counterpart, in upstream's three columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["XPATH", "VERSION", "COUNT"])
        for xpath in sorted(t.inventory):
            versions = t.inventory[xpath]
            # Upstream joins versions with ";;" and counts filings across all of them.
            w.writerow([xpath, ";;".join(sorted(versions)), sum(versions.values())])
    return len(t.inventory)


def write_unmapped_fields(t: Tally, path: Path) -> int:
    """Paths present in filings that the pipeline does not read, with counts, most common first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    consumed = consumed_xpaths()
    rows = []
    for xpath, versions in t.inventory.items():
        if xpath in consumed:
            continue
        rows.append((sum(versions.values()), xpath, ";;".join(sorted(versions))))
    rows.sort(reverse=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["filings", "xpath", "versions"])
        w.writerows(rows)
    return len(rows)
