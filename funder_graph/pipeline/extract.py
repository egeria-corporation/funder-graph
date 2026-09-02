"""Stage 2: index the corpus, deduplicate amended returns, reconcile, stream.

The index CSV is the map from a filing to the XML document that contains it, and it carries
``SUB_DATE`` — the only source for when a filing became public, absent from the XML. Three
rules from the spec live here:

* **Map headers defensively; fail loudly on one we do not recognise.** Headers have varied
  across years. Guessing what an unknown column means is how a whole year silently gets the
  wrong submission date.
* **Amended and superseded returns are deduplicated, not discarded.** Group on
  ``(EIN, TAX_PERIOD, RETURN_TYPE)``, keep the latest ``SUB_DATE``, and write the losers to
  ``superseded_filings``. The count is a health signal worth logging.
* **Reconcile the index against ZIP contents in both directions and report the delta.**
  Verified on the 2023 posting: ``2023_TEOS_XML_12A.zip`` holds 20,007 members, all present
  in the index, but the spec is right that this is not guaranteed for every posting.

Extraction is lazy: members are streamed out of the ZIP one at a time. Nothing is exploded
to disk.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa

# Every header we have seen or expect, mapped to the canonical name. Matching is
# case-insensitive and ignores surrounding whitespace. Anything else is an error.
_HEADER_ALIASES: dict[str, str] = {
    "RETURN_ID": "return_id",
    "FILING_TYPE": "filing_type",
    "EIN": "ein",
    "TAX_PERIOD": "tax_period",
    "SUB_DATE": "sub_date",
    "TAXPAYER_NAME": "taxpayer_name",
    "RETURN_TYPE": "return_type",
    "DLN": "dln",
    "OBJECT_ID": "object_id",
    # Variants seen in older postings and in community mirrors of the index.
    "TAX_PRD": "tax_period",
    "SUBMISSION_DATE": "sub_date",
    "TAXPAYER NAME": "taxpayer_name",
    "RETURN TYPE": "return_type",
    "OBJECT ID": "object_id",
}
REQUIRED = {"ein", "tax_period", "sub_date", "taxpayer_name", "return_type", "object_id"}

# Only these carry grant edges. 990-EZ and 990-T do not; 990-N has no schedules at all.
# The index marks a Form 990 with Schedule O attached as "990O" (2019-2020 postings) and
# a 990-PF as "990PR" in 2020; the XML ReturnTypeCd is plain 990 / 990PF, which is what
# the published funder_form_type carries. 990EO is a 990-EZ and has no grant edges.
GRANT_RETURN_TYPES = ("990", "990O", "990PF", "990PR")


class IndexHeaderError(ValueError):
    """The index CSV has a column we do not know how to interpret."""


def normalize_header(raw: list[str]) -> list[str]:
    """Canonical column names, or a loud error naming the unknown header."""
    out = []
    unknown = []
    for name in raw:
        key = name.strip().upper().lstrip("﻿")
        if key in _HEADER_ALIASES:
            out.append(_HEADER_ALIASES[key])
        else:
            unknown.append(name)
    if unknown:
        raise IndexHeaderError(
            f"unrecognised index column(s) {unknown!r}; add an alias in "
            "funder_graph/pipeline/extract.py rather than guessing"
        )
    missing = REQUIRED - set(out)
    if missing:
        raise IndexHeaderError(f"index is missing required column(s) {sorted(missing)!r}")
    return out


_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings_index (
  object_id     VARCHAR PRIMARY KEY,
  filing_year   INTEGER NOT NULL,
  return_id     VARCHAR,
  ein           VARCHAR NOT NULL,
  tax_period    VARCHAR NOT NULL,
  sub_date      VARCHAR,
  taxpayer_name VARCHAR,
  return_type   VARCHAR NOT NULL,
  dln           VARCHAR
);
CREATE TABLE IF NOT EXISTS superseded_filings (
  object_id      VARCHAR PRIMARY KEY,
  filing_year    INTEGER NOT NULL,
  ein            VARCHAR NOT NULL,
  tax_period     VARCHAR NOT NULL,
  return_type    VARCHAR NOT NULL,
  sub_date       VARCHAR,
  superseded_by  VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS zip_members (
  object_id  VARCHAR NOT NULL,
  zip_file   VARCHAR NOT NULL,
  member     VARCHAR NOT NULL,
  bytes      BIGINT NOT NULL,
  PRIMARY KEY (object_id, zip_file)
);
"""


def _bulk_insert(conn: duckdb.DuckDBPyConnection, table: str, records: list[dict]) -> None:
    """One INSERT ... SELECT from a registered Arrow table.

    Dict key order must match the table's column order; every caller writes its dicts in
    DDL order. ``from_pylist`` preserves that order, and DuckDB inserts positionally.
    """
    if not records:
        return
    incoming = pa.Table.from_pylist(records)
    conn.register("incoming", incoming)
    try:
        conn.execute(f"INSERT INTO {table} SELECT * FROM incoming")
    finally:
        conn.unregister("incoming")


@dataclass(frozen=True)
class IndexSummary:
    filing_year: int
    rows_read: int
    grant_bearing: int
    kept: int
    superseded: int
    by_return_type: dict[str, int]


def load_index(conn: duckdb.DuckDBPyConnection, csv_path: Path, filing_year: int) -> IndexSummary:
    """Load one year's index CSV, filtered to grant-bearing forms and deduplicated.

    Idempotent for a given year: rows for ``filing_year`` are replaced.
    """
    conn.execute(_SCHEMA)
    with csv_path.open(encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        columns = normalize_header(next(reader))
        rows = [dict(zip(columns, row, strict=False)) for row in reader if any(row)]

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["return_type"]] = by_type.get(r["return_type"], 0) + 1
    grant = [r for r in rows if r["return_type"] in GRANT_RETURN_TYPES]

    # Dedup: latest SUB_DATE wins; ties broken by OBJECT_ID so the choice is deterministic.
    best: dict[tuple[str, str, str], dict[str, str]] = {}
    losers: list[tuple[dict[str, str], str]] = []
    for r in sorted(grant, key=lambda r: (r["sub_date"] or "", r["object_id"])):
        key = (r["ein"], r["tax_period"], r["return_type"])
        prior = best.get(key)
        if prior is not None:
            losers.append((prior, r["object_id"]))
        best[key] = r

    conn.execute("DELETE FROM filings_index WHERE filing_year = ?", [filing_year])
    conn.execute("DELETE FROM superseded_filings WHERE filing_year = ?", [filing_year])
    # Bulk inserts through Arrow, not executemany. DuckDB's executemany is a row-at-a-time
    # bind loop: on the real 2023 index it turned "load 470,000 rows" into a quarter of an
    # hour with no output. Registering an Arrow table and INSERT ... SELECT is one statement.
    if best:
        _bulk_insert(
            conn,
            "filings_index",
            [
                {
                    "object_id": r["object_id"],
                    "filing_year": filing_year,
                    "return_id": r.get("return_id"),
                    "ein": r["ein"],
                    "tax_period": r["tax_period"],
                    "sub_date": r["sub_date"],
                    "taxpayer_name": r["taxpayer_name"],
                    "return_type": r["return_type"],
                    "dln": r.get("dln"),
                }
                for r in best.values()
            ],
        )
    # DuckDB's executemany raises on an empty parameter list, and "nothing was superseded"
    # is the common case - it is what every test without an amended pair looked like.
    if losers:
        _bulk_insert(
            conn,
            "superseded_filings",
            [
                {
                    "object_id": r["object_id"],
                    "filing_year": filing_year,
                    "ein": r["ein"],
                    "tax_period": r["tax_period"],
                    "return_type": r["return_type"],
                    "sub_date": r["sub_date"],
                    "superseded_by": winner,
                }
                for r, winner in losers
            ],
        )
    return IndexSummary(
        filing_year=filing_year,
        rows_read=len(rows),
        grant_bearing=len(grant),
        kept=len(best),
        superseded=len(losers),
        by_return_type=dict(sorted(by_type.items())),
    )


def _object_id(member: str) -> str | None:
    name = member.rsplit("/", 1)[-1]
    if not name.lower().endswith("_public.xml"):
        return None
    return name[: -len("_public.xml")]


def register_zip(conn: duckdb.DuckDBPyConnection, zip_path: Path) -> int:
    """Record every XML member of an archive. Returns the member count."""
    conn.execute(_SCHEMA)
    conn.execute("DELETE FROM zip_members WHERE zip_file = ?", [zip_path.name])
    rows = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            oid = _object_id(info.filename)
            if oid:
                rows.append(
                    {
                        "object_id": oid,
                        "zip_file": zip_path.name,
                        "member": info.filename,
                        "bytes": info.file_size,
                    }
                )
    _bulk_insert(conn, "zip_members", rows)
    return len(rows)


@dataclass(frozen=True)
class Reconciliation:
    filing_year: int
    index_only: int  # in the (deduplicated) index, in no registered ZIP
    zip_only: int  # in a ZIP, not in the index at all (including superseded)
    matched: int

    def write_csv(self, conn: duckdb.DuckDBPyConnection, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(
            f"""
            COPY (
              SELECT 'index_only' AS kind, i.object_id, i.return_type, NULL AS zip_file
              FROM filings_index i LEFT JOIN zip_members z USING (object_id)
              WHERE i.filing_year = {self.filing_year} AND z.object_id IS NULL
              UNION ALL
              SELECT 'zip_only', z.object_id, NULL, z.zip_file
              FROM zip_members z
              LEFT JOIN filings_index i USING (object_id)
              LEFT JOIN superseded_filings s USING (object_id)
              WHERE i.object_id IS NULL AND s.object_id IS NULL
              ORDER BY 1, 2
            ) TO '{path.as_posix()}' (HEADER, DELIMITER ',')
            """
        )


def reconcile(conn: duckdb.DuckDBPyConnection, filing_year: int) -> Reconciliation:
    """Index vs. ZIP contents, both directions. Reports; never raises."""
    conn.execute(_SCHEMA)
    (index_only,) = conn.execute(
        "SELECT COUNT(*) FROM filings_index i LEFT JOIN zip_members z USING (object_id) "
        "WHERE i.filing_year = ? AND z.object_id IS NULL",
        [filing_year],
    ).fetchone()
    # Only this posting's archives: both IRS naming schemes carry the year in the file name.
    (zip_only,) = conn.execute(
        "SELECT COUNT(DISTINCT z.object_id) FROM zip_members z "
        "LEFT JOIN filings_index i USING (object_id) "
        "LEFT JOIN superseded_filings s USING (object_id) "
        "WHERE i.object_id IS NULL AND s.object_id IS NULL "
        r"AND regexp_extract(z.zip_file, '(\d{4})', 1) = ?",
        [str(filing_year)],
    ).fetchone()
    (matched,) = conn.execute(
        "SELECT COUNT(*) FROM filings_index i JOIN zip_members z USING (object_id) "
        "WHERE i.filing_year = ?",
        [filing_year],
    ).fetchone()
    return Reconciliation(filing_year, index_only, zip_only, matched)


DEFLATE64 = 9
_LOCAL_HEADER = 30  # fixed part of a ZIP local file header, before the name and extra fields


def inflate64(data: bytes) -> bytes:
    """Decompress Deflate64 (ZIP method 9) bytes. The IRS's 2020_TEOS_XML_CT1.zip uses it."""
    import inflate64 as _inflate64

    inflater = _inflate64.Inflater()
    out = inflater.inflate(data)
    return out


def read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """One member's bytes, including the Deflate64 method the standard library refuses.

    For method 9 the raw compressed bytes are read straight from the local header offset -
    the central directory's sizes are authoritative, and the local header's own name and
    extra lengths say where the data starts - and inflated with ``inflate64``.
    """
    if info.compress_type != DEFLATE64:
        with archive.open(info) as member:
            return member.read()
    fp = archive.fp
    assert fp is not None
    fp.seek(info.header_offset)
    header = fp.read(_LOCAL_HEADER)
    if header[:4] != b"PK":
        raise zipfile.BadZipFile(f"{info.filename}: bad local header")
    name_len = int.from_bytes(header[26:28], "little")
    extra_len = int.from_bytes(header[28:30], "little")
    fp.seek(info.header_offset + _LOCAL_HEADER + name_len + extra_len)
    raw = fp.read(info.compress_size)
    data = inflate64(raw)
    if len(data) != info.file_size:
        raise zipfile.BadZipFile(
            f"{info.filename}: inflated {len(data):,} bytes, header says {info.file_size:,}"
        )
    return data


def iter_filings(zip_path: Path, only: set[str] | None = None) -> Iterator[tuple[str, bytes]]:
    """Stream ``(object_id, xml_bytes)`` out of an archive without extracting it.

    ``only`` restricts to a set of OBJECT_IDs — the deduplicated, grant-bearing ones — so
    superseded returns and 990-EZ filings are never even decompressed.
    """
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            oid = _object_id(info.filename)
            if oid is None or (only is not None and oid not in only):
                continue
            yield oid, read_member(archive, info)


def wanted_object_ids(conn: duckdb.DuckDBPyConnection, filing_year: int) -> set[str]:
    """The deduplicated, grant-bearing OBJECT_IDs for a posting year."""
    rows = conn.execute(
        "SELECT object_id FROM filings_index WHERE filing_year = ?", [filing_year]
    ).fetchall()
    return {r[0] for r in rows}


def build_zip(members: dict[str, bytes], zip_stem: str) -> bytes:
    """A ZIP in the IRS layout (``{stem}/{OBJECT_ID}_public.xml``). For tests."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for oid, data in members.items():
            archive.writestr(f"{zip_stem}/{oid}_public.xml", data)
    return buffer.getvalue()
