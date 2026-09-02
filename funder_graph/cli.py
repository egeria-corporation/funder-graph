"""The command-line surface. A thin adapter over the library — logic here is a bug.

Milestone 1 ships one command, ``parse-filing``, which is the milestone's exit demo: real
grant rows from one real filing, through the concordance, printed. The full command set in
the build spec lands milestone by milestone.
"""

from __future__ import annotations

import dataclasses
import json
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
