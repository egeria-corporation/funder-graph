"""The reference data for entity resolution: the IRS Exempt Organizations Business Master File.

Roughly 1.9 million rows - EIN, legal name, sort name, address, subsection, NTEE - loaded into
DuckDB with the normalized name and the blocking keys precomputed once, so that thirty
million grant rows are never compared against it pairwise.

Parsing is delegated to ``grantcheck.ingest.teos.parse_bmf``, the sibling repository's parser
verified against the real files: the BMF is comma-delimited with a header and RFC 4180
quoting, unlike the other three TEOS files, and its quirks are already handled there. The
import is deferred to call time so that a checkout without the dependency can still import
this package; only the loader needs it.

Blocking keys, from the build spec: exact normalized name (and sort name); state plus the first
name token; ZIP5 plus the first name token; state plus the phonetic key of the first two tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pyarrow as pa

from funder_graph.resolve.normalize import normalize_name, zip5
from funder_graph.resolve.phonetic import phonetic_key

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bmf (
  ein                  VARCHAR PRIMARY KEY,
  name                 VARCHAR NOT NULL,
  sort_name            VARCHAR,
  street               VARCHAR,
  city                 VARCHAR,
  state                VARCHAR,
  zip                  VARCHAR,
  zip5                 VARCHAR,
  subsection           VARCHAR,
  foundation           VARCHAR,
  status               VARCHAR,
  ntee_cd              VARCHAR,
  group_exemption      VARCHAR,
  affiliation          VARCHAR,
  name_normalized      VARCHAR NOT NULL,
  sort_name_normalized VARCHAR,
  first_token          VARCHAR,
  phonetic             VARCHAR,
  vintage              VARCHAR
);
CREATE INDEX IF NOT EXISTS bmf_name_normalized ON bmf(name_normalized);
CREATE INDEX IF NOT EXISTS bmf_sort_name_normalized ON bmf(sort_name_normalized);
CREATE INDEX IF NOT EXISTS bmf_state_token ON bmf(state, first_token);
CREATE INDEX IF NOT EXISTS bmf_zip5_token ON bmf(zip5, first_token);
CREATE INDEX IF NOT EXISTS bmf_state_phonetic ON bmf(state, phonetic);
"""

# DDL order. A record is built in this order so that a schema change fails loudly here rather
# than silently shifting columns in the bulk insert.
COLUMNS = (
    "ein",
    "name",
    "sort_name",
    "street",
    "city",
    "state",
    "zip",
    "zip5",
    "subsection",
    "foundation",
    "status",
    "ntee_cd",
    "group_exemption",
    "affiliation",
    "name_normalized",
    "sort_name_normalized",
    "first_token",
    "phonetic",
    "vintage",
)

_ARROW = pa.schema([(column, pa.string()) for column in COLUMNS])


@dataclass(frozen=True)
class BmfLoad:
    rows: int  # rows parsed from the file
    organizations: int  # distinct EINs inserted; the real file carries duplicate rows
    quarantined: int
    field_warnings: int
    vintage: str


def ensure_bmf_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(_SCHEMA)


def first_token(normalized: str | None) -> str | None:
    return normalized.split(" ", 1)[0] if normalized else None


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def bmf_record(row: dict, vintage: str) -> dict:
    """One ``bmf`` row from one parsed BMF row, derived columns included."""
    name = (row.get("name") or "").strip()
    sort_name = _clean(row.get("sort_name"))
    normalized = normalize_name(name)
    record = {
        "ein": row["ein"],
        "name": name,
        "sort_name": sort_name,
        "street": _clean(row.get("street")),
        "city": _clean(row.get("city")),
        "state": _clean(row.get("state")),
        "zip": _clean(row.get("zip")),
        "zip5": zip5(row.get("zip")),
        "subsection": _clean(row.get("subsection")),
        "foundation": _clean(row.get("foundation")),
        "status": _clean(row.get("status")),
        "ntee_cd": _clean(row.get("ntee_cd")),
        "group_exemption": _clean(row.get("group_exemption")),
        "affiliation": _clean(row.get("affiliation")),
        "name_normalized": normalized,
        "sort_name_normalized": normalize_name(sort_name) if sort_name else None,
        "first_token": first_token(normalized),
        "phonetic": phonetic_key(normalized),
        "vintage": vintage,
    }
    assert tuple(record) == COLUMNS
    return record


def insert_bmf_records(conn: duckdb.DuckDBPyConnection, records: list[dict]) -> None:
    """Bulk insert through Arrow; ``INSERT OR REPLACE`` on the EIN key."""
    if not records:
        return
    # One bulk insert through Arrow rather than executemany: DuckDB's executemany is a
    # row-at-a-time bind loop, which is minutes for the real 1.9M-row file.
    table = pa.Table.from_pylist(records, schema=_ARROW)
    conn.register("bmf_incoming", table)
    conn.execute("INSERT OR REPLACE INTO bmf SELECT * FROM bmf_incoming")
    conn.unregister("bmf_incoming")


def load_bmf(
    conn: duckdb.DuckDBPyConnection, text: str, *, vintage: str, replace: bool = True
) -> BmfLoad:
    """Load one BMF file (or a concatenation of the regional files) into ``bmf``.

    Idempotent for a vintage: with ``replace`` (the default) rows for ``vintage`` are dropped
    first. Pass ``replace=False`` for the second and later regional files of one vintage. A row
    whose EIN already exists - the real file carries duplicates - is kept once.
    """
    # Deferred so the package imports without the dependency; see the module docstring.
    from grantcheck.ingest.teos import parse_bmf

    parsed = parse_bmf(text)
    ensure_bmf_schema(conn)
    if replace:
        conn.execute("DELETE FROM bmf WHERE vintage = ?", [vintage])

    seen: set[str] = set()
    records = []
    for row in parsed.rows:
        ein = row.get("ein")
        if not ein or ein in seen:
            continue
        seen.add(ein)
        records.append(bmf_record(row, vintage))
    insert_bmf_records(conn, records)

    return BmfLoad(
        rows=len(parsed.rows),
        organizations=len(records),
        quarantined=parsed.rejected,
        field_warnings=parsed.warned,
        vintage=vintage,
    )


def bmf_count(conn: duckdb.DuckDBPyConnection) -> int:
    (n,) = conn.execute("SELECT COUNT(*) FROM bmf").fetchone()
    return n


def bmf_vintage(conn: duckdb.DuckDBPyConnection) -> str | None:
    """The newest vintage loaded, or None when the table is absent or empty."""
    try:
        (v,) = conn.execute("SELECT MAX(vintage) FROM bmf").fetchone()
    except duckdb.CatalogException:
        return None
    return v
