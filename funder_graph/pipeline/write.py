"""The published row: one grant line as a Parquet record, in the README's exact schema.

The README's schema table is the public contract; consumers pin it. This module is the only
place that contract is turned into bytes, and ``COLUMNS`` below is checked against the README
by a test, so the two cannot drift silently.

Three rules from the build spec, enforced here because here is where they become permanent:

* **``grant_id`` is deterministic.** ``sha256(object_id + ':' + group + ':' + ordinal)``,
  first 32 hex characters. Two ingests of the same filing produce the same id, which is what
  makes incremental updates and consumer joins possible.
* **Sort within a partition by ``funder_ein``, then ``tax_year``, then ``amount_usd`` DESC.**
  This is what lets Parquet row-group statistics prune for the dominant query,
  ``WHERE funder_ein = ?``. Without it the quickstart reads far more than it needs.
* **Individuals never enter the default edge view.** Rows tagged ``individual`` are written
  to ``grants_individuals/`` with the same layout, never to ``grants/``. They are not deleted
  - a wrong tag is recoverable - but they are not published against a person's name.

Resolution columns are written in their unresolved state here; milestone 4 fills them.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from funder_graph.extract import GrantRow
from funder_graph.resolve.normalize import normalize_name, tax_year, zip5

# The README's "Published schema" table, in order. A test asserts this list against the
# README's markdown so the contract and the bytes cannot diverge.
COLUMNS: list[str] = [
    "grant_id",
    "funder_ein",
    "funder_name",
    "funder_state",
    "funder_form_type",
    "object_id",
    "tax_year",
    "tax_period_end",
    "filing_submission_date",
    "filing_year",
    "return_version",
    "amount_usd",
    "noncash_amount_usd",
    "amount_type",
    "grant_purpose",
    "recipient_name_raw",
    "recipient_name_normalized",
    "recipient_ein_reported",
    "recipient_ein_resolved",
    "recipient_ein_source",
    "match_confidence",
    "match_tier",
    "match_method",
    "recipient_address_line1",
    "recipient_city",
    "recipient_state",
    "recipient_zip",
    "recipient_zip5",
    "recipient_country",
    "recipient_bmf_name",
    "recipient_ntee_code",
    "recipient_subsection_code",
    "recipient_type",
    "recipient_relationship",
    "recipient_foundation_status",
    "concordance_version",
    "dataset_version",
    "ingested_at",
]

SCHEMA = pa.schema(
    [
        ("grant_id", pa.string()),
        ("funder_ein", pa.string()),
        ("funder_name", pa.string()),
        ("funder_state", pa.string()),
        ("funder_form_type", pa.string()),
        ("object_id", pa.string()),
        ("tax_year", pa.int32()),
        ("tax_period_end", pa.date32()),
        ("filing_submission_date", pa.date32()),
        ("filing_year", pa.int32()),
        ("return_version", pa.string()),
        ("amount_usd", pa.int64()),
        ("noncash_amount_usd", pa.int64()),
        ("amount_type", pa.string()),
        ("grant_purpose", pa.string()),
        ("recipient_name_raw", pa.string()),
        ("recipient_name_normalized", pa.string()),
        ("recipient_ein_reported", pa.string()),
        ("recipient_ein_resolved", pa.string()),
        ("recipient_ein_source", pa.string()),
        ("match_confidence", pa.float64()),
        ("match_tier", pa.string()),
        ("match_method", pa.string()),
        ("recipient_address_line1", pa.string()),
        ("recipient_city", pa.string()),
        ("recipient_state", pa.string()),
        ("recipient_zip", pa.string()),
        ("recipient_zip5", pa.string()),
        ("recipient_country", pa.string()),
        ("recipient_bmf_name", pa.string()),
        ("recipient_ntee_code", pa.string()),
        ("recipient_subsection_code", pa.string()),
        ("recipient_type", pa.string()),
        ("recipient_relationship", pa.string()),
        ("recipient_foundation_status", pa.string()),
        ("concordance_version", pa.string()),
        ("dataset_version", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)
assert [f.name for f in SCHEMA] == COLUMNS

ROW_GROUP_SIZE = 150_000


def grant_id(object_id: str, group: str, ordinal: int) -> str:
    """Stable across re-ingests of the same filing. 32 hex characters."""
    return hashlib.sha256(f"{object_id}:{group}:{ordinal}".encode()).hexdigest()[:32]


def to_record(
    row: GrantRow,
    *,
    filing_year: int,
    filing_submission_date: date | None,
    concordance_version: str,
    dataset_version: str,
    ingested_at: datetime,
) -> dict:
    """One ``GrantRow`` as a published record. Resolution columns are left unresolved."""
    f = row.filing
    name_raw = row.recipient_name_raw or row.recipient_person_name or ""
    return {
        "grant_id": grant_id(f.object_id, row.group, row.ordinal),
        "funder_ein": f.funder_ein,
        "funder_name": f.funder_name,
        "funder_state": f.funder_state,
        "funder_form_type": f.return_type,
        "object_id": f.object_id,
        "tax_year": tax_year(f.tax_period_end),
        "tax_period_end": f.tax_period_end,
        "filing_submission_date": filing_submission_date,
        "filing_year": filing_year,
        "return_version": f.return_version,
        "amount_usd": row.amount_usd if row.amount_usd is not None else 0,
        "noncash_amount_usd": row.noncash_amount_usd,
        "amount_type": row.amount_type,
        "grant_purpose": row.purpose,
        "recipient_name_raw": name_raw,
        "recipient_name_normalized": normalize_name(name_raw),
        "recipient_ein_reported": row.recipient_ein_reported,
        "recipient_ein_resolved": None,
        "recipient_ein_source": "unresolved",
        "match_confidence": None,
        "match_tier": "U",
        "match_method": None,
        "recipient_address_line1": row.address_line1,
        "recipient_city": row.city,
        "recipient_state": row.state,
        "recipient_zip": row.zip_raw,
        "recipient_zip5": zip5(row.zip_raw),
        "recipient_country": row.country,
        "recipient_bmf_name": None,
        "recipient_ntee_code": None,
        "recipient_subsection_code": None,
        "recipient_type": row.recipient_type,
        "recipient_relationship": row.relationship,
        "recipient_foundation_status": row.foundation_status,
        "concordance_version": concordance_version,
        "dataset_version": dataset_version,
        "ingested_at": ingested_at,
    }


def _sort_key(r: dict) -> tuple:
    return (r["funder_ein"], r["tax_year"] or 0, -(r["amount_usd"] or 0))


def write_shard(
    records: list[dict], out_dir: Path, filing_year: int, shard_index: int
) -> dict[str, int]:
    """Write one worker's rows as two shards: the edge list and the individuals table.

    Returns row counts by table. An empty table writes no file; a zero-row Parquet file is
    not an error but it is clutter, and the manifest records counts anyway.
    """
    edges = sorted((r for r in records if r["recipient_type"] != "individual"), key=_sort_key)
    people = sorted((r for r in records if r["recipient_type"] == "individual"), key=_sort_key)
    counts = {}
    for table, rows in (("grants", edges), ("grants_individuals", people)):
        counts[table] = len(rows)
        if not rows:
            continue
        path = out_dir / table / f"filing_year={filing_year}" / f"part-{shard_index:04d}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = {c: [r[c] for r in rows] for c in COLUMNS}
        # filing_year is the Hive partition key; DuckDB reads it from the path.
        arrays = {c: pa.array(v, type=SCHEMA.field(c).type) for c, v in columns.items()}
        table_ = pa.table(arrays, schema=SCHEMA).drop_columns(["filing_year"])
        pq.write_table(table_, path, compression="zstd", row_group_size=ROW_GROUP_SIZE)
    return counts
