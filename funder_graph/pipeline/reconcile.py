"""The reconciliation reports: the highest-value quality control in the pipeline.

Every 990-PF states its own total of grants paid; every Schedule I states its own count of
recipient organizations. Our parsed rows should reproduce both. When they do not, that is a
parsing bug with a built-in detector, and the build spec makes the numbers a gate: parsed
990-PF totals must reconcile within 1% for at least 95% of filings before milestone 3 is done.

Three reports, all per filing so a reader can go from a bad number to one document:

* ``pf-total-reconciliation.csv`` — parsed ``paid`` sum vs. the filer's stated total, delta
  and percentage, with a status that keeps "the filer stated no total" distinct from "the
  total disagrees". Conflating those would report reconciliation failures for filers who
  simply left the box empty, and the Chilean filing in the fixtures does exactly that.
* ``schedule-i-count-reconciliation.csv`` — parsed Part II rows vs. the filer's stated
  ``Total501c3OrgCnt + TotalOtherOrgCnt``.
* ``pf-missing-detail.csv`` — filings whose itemized grants live in an attachment: a stated
  total with no structured rows, or with only aggregate placeholder rows. A known, real
  limitation, measured and published rather than hidden.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from funder_graph.extract import Extraction

TOLERANCE_PCT = 1.0


@dataclass(frozen=True)
class PfTotalRow:
    object_id: str
    funder_ein: str
    return_version: str
    parsed_paid: int
    reported_paid: int | None
    delta: int | None
    delta_pct: float | None
    status: str  # within_tolerance | over_tolerance | no_total | no_rows


@dataclass(frozen=True)
class SchedICountRow:
    object_id: str
    funder_ein: str
    return_version: str
    parsed_rows: int
    reported_501c3: int | None
    reported_other: int | None
    reported_total: int | None
    delta: int | None
    status: str  # exact | mismatch | no_count


@dataclass(frozen=True)
class MissingDetailRow:
    object_id: str
    funder_ein: str
    funder_name: str
    return_version: str
    reported_paid: int | None
    structured_rows: int
    placeholder_rows: int
    reason: str


def pf_total(e: Extraction) -> PfTotalRow:
    parsed = e.parsed_total("paid")
    reported = e.reported_total_paid
    if not e.rows_of("paid") and reported is None:
        status, delta, pct = "no_rows", None, None
    elif reported is None:
        status, delta, pct = "no_total", None, None
    else:
        delta = parsed - reported
        pct = (100.0 * abs(delta) / reported) if reported else (0.0 if delta == 0 else float("inf"))
        status = "within_tolerance" if pct <= TOLERANCE_PCT else "over_tolerance"
    return PfTotalRow(
        e.filing.object_id,
        e.filing.funder_ein,
        e.filing.return_version,
        parsed,
        reported,
        delta,
        pct,
        status,
    )


def sched_i_count(e: Extraction) -> SchedICountRow:
    parsed = len(e.rows)
    c3, other = e.reported_501c3_org_count, e.reported_other_org_count
    if c3 is None and other is None:
        total, delta, status = None, None, "no_count"
    else:
        total = (c3 or 0) + (other or 0)
        delta = parsed - total
        status = "exact" if delta == 0 else "mismatch"
    return SchedICountRow(
        e.filing.object_id,
        e.filing.funder_ein,
        e.filing.return_version,
        parsed,
        c3,
        other,
        total,
        delta,
        status,
    )


def missing_detail(e: Extraction) -> MissingDetailRow | None:
    """A filing whose Part XV detail is somewhere we cannot parse, or None if it is itemized."""
    if e.filing.return_type != "990PF":
        return None
    structured = len(e.rows)
    placeholders = sum(1 for r in e.rows if r.is_aggregate_placeholder)
    itemized = structured - placeholders
    reported = e.reported_total_paid
    if itemized > 0:
        return None
    if reported is not None and reported > 0:
        reason = "no structured rows" if structured == 0 else "only aggregate placeholder rows"
    elif placeholders > 0:
        reason = "only aggregate placeholder rows, no stated total"
    else:
        return None
    return MissingDetailRow(
        e.filing.object_id,
        e.filing.funder_ein,
        e.filing.funder_name,
        e.filing.return_version,
        reported,
        structured,
        placeholders,
        reason,
    )


@dataclass
class Summary:
    pf_filings: int = 0
    pf_with_total: int = 0
    pf_within_tolerance: int = 0
    pf_no_total: int = 0
    pf_missing_detail: int = 0
    sched_i_filings: int = 0
    sched_i_with_count: int = 0
    sched_i_exact: int = 0
    rows: dict[str, int] = field(default_factory=dict)

    @property
    def pf_within_share(self) -> float | None:
        """The milestone gate: share of 990-PF filings with a stated total that reconcile."""
        return (
            (100.0 * self.pf_within_tolerance / self.pf_with_total) if self.pf_with_total else None
        )

    @property
    def sched_i_exact_share(self) -> float | None:
        return (
            (100.0 * self.sched_i_exact / self.sched_i_with_count)
            if self.sched_i_with_count
            else None
        )


def _write(path: Path, rows: Iterable[object], columns: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(columns)
        for r in rows:
            w.writerow([getattr(r, c) for c in columns])
            n += 1
    return n


def write_reports(extractions: Iterable[Extraction], reports_dir: Path) -> Summary:
    """All three reports from one pass over the extractions. Returns the summary."""
    s = Summary()
    pf_rows: list[PfTotalRow] = []
    si_rows: list[SchedICountRow] = []
    md_rows: list[MissingDetailRow] = []

    for e in extractions:
        if e.filing.return_type == "990PF":
            s.pf_filings += 1
            r = pf_total(e)
            pf_rows.append(r)
            if r.status in ("within_tolerance", "over_tolerance"):
                s.pf_with_total += 1
                if r.status == "within_tolerance":
                    s.pf_within_tolerance += 1
            elif r.status == "no_total":
                s.pf_no_total += 1
            m = missing_detail(e)
            if m:
                md_rows.append(m)
                s.pf_missing_detail += 1
        elif e.filing.return_type == "990":
            s.sched_i_filings += 1
            r = sched_i_count(e)
            si_rows.append(r)
            if r.status != "no_count":
                s.sched_i_with_count += 1
                if r.status == "exact":
                    s.sched_i_exact += 1

    s.rows["pf-total-reconciliation.csv"] = _write(
        reports_dir / "pf-total-reconciliation.csv",
        sorted(pf_rows, key=lambda r: (r.status, -(r.delta_pct or 0), r.object_id)),
        [
            "object_id",
            "funder_ein",
            "return_version",
            "parsed_paid",
            "reported_paid",
            "delta",
            "delta_pct",
            "status",
        ],
    )
    s.rows["schedule-i-count-reconciliation.csv"] = _write(
        reports_dir / "schedule-i-count-reconciliation.csv",
        sorted(si_rows, key=lambda r: (r.status, r.object_id)),
        [
            "object_id",
            "funder_ein",
            "return_version",
            "parsed_rows",
            "reported_501c3",
            "reported_other",
            "reported_total",
            "delta",
            "status",
        ],
    )
    s.rows["pf-missing-detail.csv"] = _write(
        reports_dir / "pf-missing-detail.csv",
        sorted(md_rows, key=lambda r: -(r.reported_paid or 0)),
        [
            "object_id",
            "funder_ein",
            "funder_name",
            "return_version",
            "reported_paid",
            "structured_rows",
            "placeholder_rows",
            "reason",
        ],
    )
    return s
