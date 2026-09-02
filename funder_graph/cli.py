"""The command-line surface. A thin adapter over the library — logic here is a bug.

Milestone 1 ships one command, ``parse-filing``, which is the milestone's exit demo: real
grant rows from one real filing, through the concordance, printed. The full command set in
the build spec lands milestone by milestone.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from pathlib import Path

import click

from funder_graph import __version__
from funder_graph.concordance import load, resolved
from funder_graph.extract import extract

DISCLOSURE = (
    "This is informational only, derived from public data on the dates shown. It is not an "
    "eligibility determination, and not legal, tax, or accounting advice. Verify against the "
    "official source before relying on it."
)


def _emit(text: str) -> None:
    # Write bytes, not str: click's own stream wrapper is cached and does not honour a
    # reconfigured sys.stdout, so a redirected file on Windows would come out cp1252.
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


@click.group()
@click.version_option(__version__, prog_name="funder-graph")
def main() -> None:
    """The open 990 funding graph."""


@main.command("parse-filing")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit rows as JSON instead of a table.")
def parse_filing(path: Path, as_json: bool) -> None:
    """Extract grant rows from one IRS e-file XML document.

    PATH is a single ``{OBJECT_ID}_public.xml`` file as found inside an IRS bulk ZIP.
    """
    # Fixtures under tests/ are named ``{returnVersion}__{OBJECT_ID}.xml`` per CONTRIBUTING;
    # the object_id is the part after the double underscore. Corpus members carry no prefix.
    stem = path.name.removesuffix("_public.xml").removesuffix(".xml")
    object_id = re.sub(r"^\d{4}v[\d.]+__", "", stem)
    result = extract(path.read_bytes(), object_id)
    filing = result.filing
    concordance = load()

    if as_json:
        payload = {
            "filing": dataclasses.asdict(filing)
            | {"tax_period_end": str(filing.tax_period_end or "")},
            "reported_total_paid": result.reported_total_paid,
            "reported_total_future": result.reported_total_future,
            "parsed_total_paid": result.parsed_total("paid"),
            "rows": [
                {k: v for k, v in dataclasses.asdict(r).items() if k != "filing"}
                | {"recipient_type": r.recipient_type}
                for r in result.rows
            ],
            "errors": result.errors,
            "concordance_version": concordance.commit,
        }
        _emit(json.dumps(payload, indent=2, default=str))
        return

    _emit(
        f"{filing.funder_name}  EIN {filing.funder_ein}  {filing.return_type}  "
        f"returnVersion {filing.return_version}  period ending {filing.tax_period_end}"
    )
    _emit(f"object_id {filing.object_id}")
    _emit("")
    for r in result.rows:
        who = r.recipient_name_raw or (
            f"[individual] {r.recipient_person_name}" if r.recipient_person_name else "?"
        )
        where = ", ".join(filter(None, (r.city, r.state, r.country if r.country != "US" else None)))
        amt = f"{r.amount_usd:>12,}" if r.amount_usd is not None else f"{'(unparsed)':>12}"
        _emit(f"  {r.amount_type:15} {amt}  {who}" + (f"  — {where}" if where else ""))
        if r.purpose:
            _emit(f"  {'':15} {'':12}  purpose: {r.purpose}")
        for err in r.errors:
            _emit(f"  {'':15} {'':12}  ERROR: {err}")
    _emit("")
    paid = result.parsed_total("paid")
    if result.reported_total_paid is not None:
        delta = paid - result.reported_total_paid
        _emit(
            f"paid rows sum to {paid:,}; filer reported {result.reported_total_paid:,}  (delta {delta:+,})"
        )
    if result.reported_501c3_org_count is not None:
        _emit(
            f"Schedule I rows: {len(result.rows)}; filer reported "
            f"{result.reported_501c3_org_count} 501(c)(3) + {result.reported_other_org_count or 0} other organizations"
        )
    for err in result.errors:
        _emit(f"ERROR: {err}")
    _emit("")
    _emit(
        f"Mapped via IRS E-file Master Concordance File @ {concordance.commit[:12]} "
        f"(Nonprofit Open Data Collective). {DISCLOSURE}"
    )


@main.command("concordance-check")
def concordance_check() -> None:
    """Report which logical fields the vendored concordance failed to resolve to any XPath."""
    missing = resolved().unresolved()
    if missing:
        _emit("UNRESOLVED logical fields (the concordance yielded no XPath):")
        for m in missing:
            _emit(f"  {m}")
        sys.exit(1)
    _emit("every logical grant field resolves to at least one concordance XPath")


@main.group()
def build() -> None:
    """Pipeline stages. Users do not run these; the published dataset is the product."""


@build.command("download")
@click.option(
    "--years", default="2019-2026", show_default=True, help="e.g. 2023, 2019-2026, 2019,2021"
)
@click.option(
    "--work-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Defaults to $FUNDER_GRAPH_WORK_DIR or ./build",
)
def build_download(years: str, work_dir: Path | None) -> None:
    """Fetch the IRS bulk archives and index CSVs, resumably, recording checksums."""
    from funder_graph.pipeline.download import download, parse_years

    work = work_dir or Path(os.environ.get("FUNDER_GRAPH_WORK_DIR", "build"))
    wanted = parse_years(years)
    _emit(f"downloading {', '.join(map(str, wanted))} into {work} (4 connections, resumable)")

    def report(f) -> None:
        size = f"{(f.bytes or 0) / 1e6:9.1f} MB" if f.bytes else f"{'':12}"
        tail = f"  {f.sha256[:12]}" if f.sha256 else (f"  {f.error}" if f.error else "")
        _emit(f"  {f.status:8} {f.year} {f.filename:28}{size}{tail}")

    results = download(wanted, work, progress=report)
    done = [f for f in results if f.status == "complete"]
    failed = [f for f in results if f.status != "complete"]
    total = sum(f.bytes or 0 for f in done)
    _emit("")
    _emit(f"{len(done)} complete ({total / 1e9:.2f} GB), {len(failed)} not complete")
    if failed:
        _emit("re-run to resume; partial files are kept and continued")
        sys.exit(1)


@build.command("extract")
@click.option("--years", default="2019-2026", show_default=True)
@click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
def build_extract(years: str, work_dir: Path | None) -> None:
    """Index each posting, deduplicate amended returns, reconcile against the archives."""
    import duckdb

    from funder_graph.pipeline.download import parse_years
    from funder_graph.pipeline.extract import load_index, reconcile, register_zip

    work = work_dir or Path(os.environ.get("FUNDER_GRAPH_WORK_DIR", "build"))
    conn = duckdb.connect(str(work / "state.duckdb"))
    try:
        for year in parse_years(years):
            raw = work / "raw" / str(year)
            index = raw / f"index_{year}.csv"
            if not index.exists():
                _emit(f"{year}: no index at {index}; run `build download --years {year}` first")
                continue
            s = load_index(conn, index, year)
            _emit(
                f"{year}: {s.rows_read:,} index rows; {s.grant_bearing:,} on 990/990-PF; "
                f"{s.kept:,} kept after dedup, {s.superseded:,} superseded"
            )
            _emit(f"      by return type: {s.by_return_type}")
            zips = sorted(raw.glob("*.zip"))
            members = sum(register_zip(conn, z) for z in zips)
            _emit(f"      {len(zips)} archives, {members:,} XML members registered")
            r = reconcile(conn, year)
            report = work / "reports" / f"index-reconciliation-{year}.csv"
            r.write_csv(conn, report)
            _emit(
                f"      reconciliation: {r.matched:,} matched, {r.index_only:,} index-only, "
                f"{r.zip_only:,} zip-only -> {report}"
            )
    finally:
        conn.close()


COVERAGE_GATE_PCT = 95.0


@build.command("map")
@click.option("--years", default="2019-2026", show_default=True)
@click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--workers", type=int, default=None, help="Processes; default is the CPU count.")
def build_map(years: str, work_dir: Path | None, workers: int | None) -> None:
    """Measure concordance coverage across every schema version in the corpus.

    Writes version-coverage.csv, xpath-version-count.csv and unmapped-fields.csv under
    build/reports/, prints the headline coverage, and refuses to proceed below 95%.
    """
    import duckdb

    from funder_graph.pipeline.coverage import (
        Tally,
        tally_corpus,
        write_unmapped_fields,
        write_version_coverage,
        write_xpath_version_count,
    )
    from funder_graph.pipeline.download import parse_years
    from funder_graph.pipeline.extract import wanted_object_ids

    work = work_dir or Path(os.environ.get("FUNDER_GRAPH_WORK_DIR", "build"))
    reports = work / "reports"
    conn = duckdb.connect(str(work / "state.duckdb"), read_only=True)
    try:
        total = Tally()
        all_errors: list[str] = []
        for year in parse_years(years):
            wanted = wanted_object_ids(conn, year)
            archives = sorted((work / "raw" / str(year)).glob("*.zip"))
            if not wanted or not archives:
                _emit(
                    f"{year}: nothing indexed or no archives; run `build download` and `build extract` first"
                )
                continue
            _emit(
                f"{year}: {len(wanted):,} grant-bearing filings across {len(archives)} archives ..."
            )
            t, errors = tally_corpus(archives, wanted, workers=workers)
            total.merge(t)
            all_errors += errors
            _emit(f"      {t.filings_seen:,} filings read, {len(errors)} unparseable")
    finally:
        conn.close()

    if not total.versions:
        sys.exit(1)

    pct = write_version_coverage(total, reports / "version-coverage.csv")
    n_paths = write_xpath_version_count(total, reports / "xpath-version-count.csv")
    n_unmapped = write_unmapped_fields(total, reports / "unmapped-fields.csv")
    if all_errors:
        (reports / "unparseable-filings.txt").write_text(
            "\n".join(all_errors) + "\n", encoding="utf-8"
        )

    _emit("")
    _emit(f"{'version':12} {'form':6} {'filings':>9} {'with rows':>10} {'resolved':>9}  pct")
    for (version, rtype), s in sorted(total.versions.items()):
        share = f"{100.0 * s.fully_resolved / s.with_rows:6.2f}%" if s.with_rows else "     -"
        _emit(
            f"{version:12} {rtype:6} {s.filings:>9,} {s.with_rows:>10,} {s.fully_resolved:>9,}  {share}"
        )
    _emit("")
    _emit(f"headline: {pct:.2f}% of filings with grant rows fully resolved every required field")
    _emit(
        f"reports: {reports}/version-coverage.csv, xpath-version-count.csv ({n_paths:,} paths), "
        f"unmapped-fields.csv ({n_unmapped:,} unconsumed paths)"
    )
    if pct < COVERAGE_GATE_PCT:
        _emit("")
        _emit(
            f"STOP: coverage is below {COVERAGE_GATE_PCT:.0f}% by volume. Per the build spec this is a"
        )
        _emit(
            "strategy decision, not an engineering one: report the number before writing more code."
        )
        sys.exit(2)


RECONCILIATION_GATE_PCT = 95.0


@build.command("normalize")
@click.option("--years", default="2019-2026", show_default=True)
@click.option("--work-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--workers", type=int, default=None, help="Processes; default is the CPU count.")
@click.option(
    "--dataset-version",
    default=None,
    help="YYYY.MM.PATCH stamped into every row. Defaults to this month's .0",
)
def build_normalize(
    years: str, work_dir: Path | None, workers: int | None, dataset_version: str | None
) -> None:
    """Parse every indexed filing to the published schema and write the edge list as Parquet.

    Also writes the three reconciliation reports and refuses to proceed if fewer than 95% of
    990-PF filings with a stated total reconcile within 1% - the milestone-3 gate.
    """
    from datetime import UTC, datetime

    import duckdb

    from funder_graph.pipeline.download import parse_years
    from funder_graph.pipeline.extract import wanted_object_ids
    from funder_graph.pipeline.normalize import index_sub_dates, normalize_year

    work = work_dir or Path(os.environ.get("FUNDER_GRAPH_WORK_DIR", "build"))
    version = dataset_version or datetime.now(UTC).strftime("%Y.%m.0")
    commit = load().commit
    out_dir, reports = work / "parquet", work / "reports"

    conn = duckdb.connect(str(work / "state.duckdb"), read_only=True)
    try:
        per_year = []
        for year in parse_years(years):
            wanted = wanted_object_ids(conn, year)
            sub_dates = index_sub_dates(conn, year)
            archives = sorted((work / "raw" / str(year)).glob("*.zip"))
            if not wanted or not archives:
                _emit(f"{year}: nothing indexed or no archives; run download and extract first")
                continue
            _emit(f"{year}: {len(wanted):,} filings across {len(archives)} archives -> {out_dir}")
            per_year.append(
                normalize_year(
                    archives,
                    wanted,
                    sub_dates,
                    year,
                    out_dir,
                    reports,
                    concordance_version=commit,
                    dataset_version=version,
                    workers=workers,
                )
            )
    finally:
        conn.close()

    if not per_year:
        sys.exit(1)

    worst: float | None = None
    for y in per_year:
        s = y.summary
        counts = y.table_counts()
        share = s.pf_within_share
        if share is not None and (worst is None or share < worst):
            worst = share
        _emit("")
        _emit(f"{y.filing_year}: {y.filings:,} filings parsed, {y.rows:,} rows written")
        _emit(
            f"      grants: {counts.get('grants', 0):,}   "
            f"grants_individuals: {counts.get('grants_individuals', 0):,}"
        )
        if share is None:
            _emit("      990-PF totals: no filings stated a total")
        else:
            _emit(
                f"      990-PF totals: {s.pf_within_tolerance:,} of {s.pf_with_total:,} with a "
                f"stated total reconcile within 1% ({share:.2f}%); {s.pf_no_total:,} stated no "
                f"total; {s.pf_missing_detail:,} missing detail"
            )
        if s.sched_i_exact_share is not None:
            _emit(
                f"      Schedule I counts: {s.sched_i_exact:,} of {s.sched_i_with_count:,} exact "
                f"({s.sched_i_exact_share:.2f}%)"
            )
        if y.errors:
            path = reports / f"normalize-errors-{y.filing_year}.txt"
            path.write_text("\n".join(y.errors) + "\n", encoding="utf-8")
            _emit(f"      {len(y.errors):,} row/filing errors -> {path.name}")

    _emit("")
    _emit(f"dataset_version {version}, concordance {commit[:12]}; reports in {reports}")
    if worst is not None and worst < RECONCILIATION_GATE_PCT:
        _emit("")
        _emit(
            f"STOP: 990-PF reconciliation is below {RECONCILIATION_GATE_PCT:.0f}%. The parsed edges "
            "do not reproduce the filers' own totals; that is a parsing bug with a built-in detector."
        )
        sys.exit(3)
