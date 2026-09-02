"""Stage 4: the unresolved edge list on disk, plus the reconciliation reports.

One archive per worker process, per the build spec: each worker streams its archive's wanted
filings, extracts, converts rows to the published schema, writes its own Parquet shard, and
computes its share of the three reconciliation reports. Only small things cross the process
boundary - counts and report rows - never the rows themselves. The parent merges and writes
the reports.

``filing_submission_date`` is honest about what the index actually says. The 2023 index
carries ``SUB_DATE`` as a bare year, and a bare year is not a date. It is stored as null
rather than as a fabricated January 1st; ``filing_year`` already carries the one reliable
fact, which is the posting a filing appeared in.
"""

from __future__ import annotations

import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from funder_graph.extract import extract
from funder_graph.pipeline.extract import iter_filings
from funder_graph.pipeline.reconcile import (
    MissingDetailRow,
    PfTotalRow,
    SchedICountRow,
    Summary,
    missing_detail,
    pf_total,
    sched_i_count,
    write_report_rows,
)
from funder_graph.pipeline.write import to_record, write_shard

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def submission_date(raw: str | None) -> date | None:
    """A real date when the index gives one; None for a bare year or anything else."""
    if raw and _ISO.match(raw.strip()):
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            return None
    return None


@dataclass
class ArchiveResult:
    archive: str
    filings: int = 0
    rows: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    pf_rows: list[PfTotalRow] = field(default_factory=list)
    si_rows: list[SchedICountRow] = field(default_factory=list)
    md_rows: list[MissingDetailRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_archive(
    zip_path: Path,
    wanted: set[str],
    sub_dates: dict[str, str | None],
    filing_year: int,
    out_dir: Path,
    shard_index: int,
    concordance_version: str,
    dataset_version: str,
    ingested_at: datetime,
) -> ArchiveResult:
    """One worker's share. Module-level so a spawned process can import it."""
    result = ArchiveResult(archive=zip_path.name)
    records: list[dict] = []
    for object_id, data in iter_filings(zip_path, only=wanted):
        try:
            e = extract(data, object_id)
        except Exception as error:  # a corrupt member is recorded, never fatal
            result.errors.append(f"{object_id}: {type(error).__name__}: {error}")
            continue
        result.filings += 1
        result.errors.extend(f"{object_id}: {err}" for err in e.errors)
        sub = submission_date(sub_dates.get(object_id))
        for row in e.rows:
            records.append(
                to_record(
                    row,
                    filing_year=filing_year,
                    filing_submission_date=sub,
                    concordance_version=concordance_version,
                    dataset_version=dataset_version,
                    ingested_at=ingested_at,
                )
            )
        if e.filing.return_type == "990PF":
            result.pf_rows.append(pf_total(e))
            m = missing_detail(e)
            if m:
                result.md_rows.append(m)
        elif e.filing.return_type == "990":
            result.si_rows.append(sched_i_count(e))
    result.rows = len(records)
    result.counts = write_shard(records, out_dir, filing_year, shard_index)
    return result


def index_sub_dates(conn: duckdb.DuckDBPyConnection, filing_year: int) -> dict[str, str | None]:
    rows = conn.execute(
        "SELECT object_id, sub_date FROM filings_index WHERE filing_year = ?", [filing_year]
    ).fetchall()
    return {oid: sd for oid, sd in rows}


@dataclass
class YearResult:
    filing_year: int
    archives: list[ArchiveResult]
    summary: Summary

    @property
    def filings(self) -> int:
        return sum(a.filings for a in self.archives)

    @property
    def rows(self) -> int:
        return sum(a.rows for a in self.archives)

    @property
    def errors(self) -> list[str]:
        return [e for a in self.archives for e in a.errors]

    def table_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.archives:
            for table, n in a.counts.items():
                out[table] = out.get(table, 0) + n
        return out


def normalize_year(
    archives: list[Path],
    wanted: set[str],
    sub_dates: dict[str, str | None],
    filing_year: int,
    out_dir: Path,
    reports_dir: Path,
    *,
    concordance_version: str,
    dataset_version: str,
    workers: int | None = None,
    ingested_at: datetime | None = None,
) -> YearResult:
    """Every archive of one posting year, parallel by archive, then the merged reports."""
    stamp = ingested_at or datetime.now(UTC)
    args = [
        (z, wanted, sub_dates, filing_year, out_dir, i, concordance_version, dataset_version, stamp)
        for i, z in enumerate(sorted(archives))
    ]
    if workers == 1 or len(args) <= 1:
        results = [normalize_archive(*a) for a in args]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(normalize_archive, *zip(*args, strict=True)))

    summary = write_report_rows(
        [r for a in results for r in a.pf_rows],
        [r for a in results for r in a.si_rows],
        [r for a in results for r in a.md_rows],
        reports_dir,
    )
    return YearResult(filing_year, results, summary)
