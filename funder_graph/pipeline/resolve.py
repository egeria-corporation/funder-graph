"""The resolve stage: every distinct recipient tuple once, then the partitions rewritten in one join.

The same recipient string appears thousands of times across the corpus. Resolving per row would
be wasted work by three orders of magnitude, so this stage reads the distinct
``(name_normalized, name_raw, city, state, zip5, ein_reported, recipient_type)`` tuples out of
the written Parquet, resolves each once against the ``bmf`` table, stores the answer in a
``resolutions`` table in the build state, and rewrites each ``grants/`` file through a single
DuckDB join that preserves row order and row count.

Keeping the answers in state is what makes the monthly update cheap (build spec section 8):
tuples already resolved are not touched, and ``re_resolve_unresolved`` drops last month's ``U``
rows so they get another chance against the new BMF. ``grants_individuals/`` is never
resolved: individuals are tagged, not matched.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from funder_graph.resolve.bmf import bmf_count, bmf_vintage, ensure_bmf_schema
from funder_graph.resolve.match import (
    Recipient,
    Resolution,
    load_aliases,
    load_corrections,
    resolve_all,
)

OVERRIDES_DIR = Path(__file__).resolve().parents[2] / "data" / "overrides"

# The published columns that make up one distinct recipient tuple, and their names in
# ``resolutions``. Order matters: it is the join key everywhere below.
TUPLE = (
    ("recipient_name_normalized", "name_normalized"),
    ("recipient_name_raw", "name_raw"),
    ("recipient_city", "city"),
    ("recipient_state", "state"),
    ("recipient_zip5", "zip5"),
    ("recipient_ein_reported", "ein_reported"),
    ("recipient_type", "recipient_type"),
)

RESOLUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS resolutions (
  name_normalized  VARCHAR NOT NULL,
  name_raw         VARCHAR,
  city             VARCHAR,
  state            VARCHAR,
  zip5             VARCHAR,
  ein_reported     VARCHAR,
  recipient_type   VARCHAR NOT NULL,
  ein              VARCHAR,
  source           VARCHAR NOT NULL,
  confidence       DOUBLE,
  tier             VARCHAR NOT NULL,
  method           VARCHAR,
  bmf_name         VARCHAR,
  ntee_code        VARCHAR,
  subsection_code  VARCHAR,
  bmf_vintage      VARCHAR,
  resolved_at      TIMESTAMP NOT NULL
)
"""

_RESOLUTIONS_ARROW = pa.schema(
    [
        ("name_normalized", pa.string()),
        ("name_raw", pa.string()),
        ("city", pa.string()),
        ("state", pa.string()),
        ("zip5", pa.string()),
        ("ein_reported", pa.string()),
        ("recipient_type", pa.string()),
        ("ein", pa.string()),
        ("source", pa.string()),
        ("confidence", pa.float64()),
        ("tier", pa.string()),
        ("method", pa.string()),
        ("bmf_name", pa.string()),
        ("ntee_code", pa.string()),
        ("subsection_code", pa.string()),
        ("bmf_vintage", pa.string()),
        ("resolved_at", pa.timestamp("us")),
    ]
)


class BmfMissing(RuntimeError):
    """The ``bmf`` table is absent or empty; ``build bmf`` has not been run."""


@dataclass
class ResolveResult:
    bmf_vintage: str
    tuples_pending: int
    tuples_resolved: int = 0
    tier_counts: dict[str, int] = field(default_factory=dict)
    files_rewritten: int = 0
    rows_rewritten: int = 0


def _join_on(left: str, right: str) -> str:
    return " AND ".join(f"{left}.{g} IS NOT DISTINCT FROM {right}.{r}" for g, r in TUPLE)


def grant_files(out_dir: Path, years: list[int] | None) -> list[Path]:
    root = out_dir / "grants"
    if not root.exists():
        return []
    partitions = sorted(root.glob("filing_year=*"))
    if years is not None:
        wanted = {f"filing_year={y}" for y in years}
        partitions = [p for p in partitions if p.name in wanted]
    return [f for p in partitions for f in sorted(p.glob("*.parquet"))]


def _parquet_list(files: list[Path]) -> str:
    return "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"


def pending_recipients(conn: duckdb.DuckDBPyConnection, files: list[Path]) -> list[Recipient]:
    """Distinct tuples in ``files`` with no row in ``resolutions`` yet."""
    if not files:
        return []
    select = ", ".join(f"g.{g} AS {r}" for g, r in TUPLE)
    rows = conn.execute(
        f"SELECT DISTINCT {select} "
        f"FROM read_parquet({_parquet_list(files)}, hive_partitioning = false) g "
        f"ANTI JOIN resolutions r ON {_join_on('g', 'r')} "
        "ORDER BY 1, 4, 5, 3, 2, 6, 7"
    ).fetchall()
    return [
        Recipient(
            name_normalized=name_normalized or "",
            name_raw=name_raw,
            city=city,
            state=state,
            zip5=zip5,
            ein_reported=ein_reported,
            recipient_type=recipient_type or "organization",
        )
        for name_normalized, name_raw, city, state, zip5, ein_reported, recipient_type in rows
    ]


CHUNK_MIN = 20_000


def _by_state(
    recipients: list[Recipient], *, chunk_min: int = CHUNK_MIN
) -> list[tuple[str, list[Recipient]]]:
    """Recipients grouped by state, largest first; small groups batched together.

    The state field carries thousands of foreign province and city names, most with a
    handful of tuples, and every chunk costs a full pass of the blocking query. Groups
    under ``chunk_min`` are concatenated into batches of at least that size, labelled by
    their count. Blocking is by the recipient's own state either way; batching only
    changes how many tuples share one query.
    """
    groups: dict[str, list[Recipient]] = {}
    for r in recipients:
        groups.setdefault(r.state or "", []).append(r)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out: list[tuple[str, list[Recipient]]] = []
    batch: list[Recipient] = []
    batched = 0
    for state, members in ordered:
        if len(members) >= chunk_min:
            out.append((state, members))
            continue
        batch.extend(members)
        batched += 1
        if len(batch) >= chunk_min:
            out.append((f"{batched} small groups", batch))
            batch, batched = [], 0
    if batch:
        out.append((f"{batched} small groups", batch))
    return out


def _with_aliases(recipients: list[Recipient], aliases: dict[str, str]) -> list[Recipient]:
    if not aliases:
        return recipients
    return [
        Recipient(**{**r.__dict__, "alias": aliases.get(r.name_normalized)}) for r in recipients
    ]


def store_resolutions(
    conn: duckdb.DuckDBPyConnection,
    recipients: list[Recipient],
    resolutions: list[Resolution],
    *,
    vintage: str,
    resolved_at: datetime,
) -> None:
    if not recipients:
        return
    records = [
        {
            "name_normalized": r.name_normalized,
            "name_raw": r.name_raw,
            "city": r.city,
            "state": r.state,
            "zip5": r.zip5,
            "ein_reported": r.ein_reported,
            "recipient_type": r.recipient_type,
            "ein": s.ein,
            "source": s.source,
            "confidence": s.confidence,
            "tier": s.tier,
            "method": s.method,
            "bmf_name": s.bmf_name,
            "ntee_code": s.ntee_code,
            "subsection_code": s.subsection_code,
            "bmf_vintage": vintage,
            "resolved_at": resolved_at.replace(tzinfo=None),
        }
        for r, s in zip(recipients, resolutions, strict=True)
    ]
    conn.register("resolutions_incoming", pa.Table.from_pylist(records, schema=_RESOLUTIONS_ARROW))
    conn.execute("INSERT INTO resolutions SELECT * FROM resolutions_incoming")
    conn.unregister("resolutions_incoming")


_REPLACE = (
    ("recipient_ein_resolved", "ein"),
    ("recipient_ein_source", "source"),
    ("match_confidence", "confidence"),
    ("match_tier", "tier"),
    ("match_method", "method"),
    ("recipient_bmf_name", "bmf_name"),
    ("recipient_ntee_code", "ntee_code"),
    ("recipient_subsection_code", "subsection_code"),
)


def rewrite_file(conn: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Rewrite one grants file with the resolved columns; row order and count preserved."""
    src = path.as_posix()
    tmp = path.with_suffix(".parquet.tmp")
    replace = ", ".join(
        f"CASE WHEN r.tier IS NULL THEN g.{g} ELSE r.{r} END AS {g}" for g, r in _REPLACE
    )
    (before,) = conn.execute(
        f"SELECT COUNT(*) FROM read_parquet('{src}', hive_partitioning = false)"
    ).fetchone()
    conn.execute(
        f"COPY (SELECT g.* EXCLUDE (file_row_number) REPLACE ({replace}) "
        f"FROM read_parquet('{src}', hive_partitioning = false, file_row_number = true) g "
        f"LEFT JOIN resolutions r ON {_join_on('g', 'r')} "
        f"ORDER BY g.file_row_number) "
        f"TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    (after,) = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{tmp.as_posix()}')").fetchone()
    if after != before:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{path}: rewrite changed the row count ({before} -> {after}); not replaced"
        )
    os.replace(tmp, path)
    return after


def resolve(
    out_dir: Path,
    work_dir: Path,
    years: list[int] | None,
    *,
    re_resolve_unresolved: bool = False,
    overrides_dir: Path = OVERRIDES_DIR,
    now: datetime | None = None,
    on_chunk: Callable[[str, int, dict[str, int]], None] | None = None,
) -> ResolveResult:
    """Resolve the pending tuples for ``years`` and rewrite their partitions."""
    now = now or datetime.now(UTC)
    conn = duckdb.connect(str(work_dir / "state.duckdb"))
    try:
        ensure_bmf_schema(conn)
        vintage = bmf_vintage(conn)
        if not vintage or bmf_count(conn) == 0:
            raise BmfMissing("no Business Master File loaded; run `build bmf` first")
        conn.execute(RESOLUTIONS_DDL)
        if re_resolve_unresolved:
            conn.execute("DELETE FROM resolutions WHERE tier = 'U'")

        files = grant_files(out_dir, years)
        recipients = _with_aliases(
            pending_recipients(conn, files), load_aliases(overrides_dir / "name-aliases.csv")
        )
        corrections = load_corrections(overrides_dir / "ein-corrections.csv")
        result = ResolveResult(bmf_vintage=vintage, tuples_pending=len(recipients))
        # One chunk per recipient state, stored as it finishes. Memory is bounded by the largest
        # state instead of the corpus, and a run that dies resumes from the stored chunks: the
        # ANTI JOIN in pending_recipients is the checkpoint.
        for state, chunk in _by_state(recipients):
            resolutions = resolve_all(conn, chunk, corrections=corrections)
            store_resolutions(conn, chunk, resolutions, vintage=vintage, resolved_at=now)
            result.tuples_resolved += len(resolutions)
            tiers: dict[str, int] = {}
            for s in resolutions:
                tiers[s.tier] = tiers.get(s.tier, 0) + 1
                result.tier_counts[s.tier] = result.tier_counts.get(s.tier, 0) + 1
            if on_chunk is not None:
                on_chunk(state, len(chunk), tiers)
        for path in files:
            result.rows_rewritten += rewrite_file(conn, path)
            result.files_rewritten += 1
        return result
    finally:
        conn.close()


def tier_distribution(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Tier counts over every stored resolution - for `dataset info` and the eval report."""
    rows = conn.execute(
        "SELECT tier, COUNT(*) FROM resolutions GROUP BY tier ORDER BY tier"
    ).fetchall()
    return {tier: n for tier, n in rows}
