"""Stage 6: stage a dataset version and upload it to the public bucket.

The published layout is the README's contract, one object per filing year (D-009)::

    <prefix>/<version>/grants/filing_year=2023/part-0000.parquet
    <prefix>/<version>/manifest.json
    <prefix>/latest/...                       # a copy of the current version

Why one shard per year when ``normalize`` writes one per archive: DuckDB cannot glob over
HTTPS - there is no directory listing - so every URL a reader needs must be predictable from
the year alone. The shards are merged here, globally sorted the way ``write_shard`` sorts, in
150,000-row groups, so a ``WHERE funder_ein = ...`` still prunes to a few row groups over HTTP.

Why ``latest/`` is a copy: a bare R2 custom domain serves objects and cannot alias a prefix.
About 190 MB per posting; not worth a Worker.

``grants_individuals`` is never staged. It exists locally for reconciliation and nothing else.

Uploads go through wrangler, which is already logged in on the publishing machine, so no
credential ever passes through this code. An ``Uploader`` that copies into a directory serves
the tests and ``--to-dir`` rehearsals.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import duckdb

from funder_graph.pipeline.write import COLUMNS, ROW_GROUP_SIZE

DATASET_LICENSE = "Apache-2.0"
DEFAULT_BUCKET = "opengrants-data"
DEFAULT_PREFIX = "funder-graph"
PUBLIC_BASE_URL = "https://data.opengrants.io"

_CONTENT_TYPES = {".parquet": "application/vnd.apache.parquet", ".json": "application/json"}


class PublishError(RuntimeError):
    pass


@dataclass
class PublishedFile:
    path: str  # relative to the version root, forward slashes
    filing_year: int
    bytes: int
    sha256: str
    rows: int


@dataclass
class Manifest:
    dataset_version: str
    generated_at: str
    concordance_version: str | None
    bmf_vintage: str | None
    license: str
    columns: list[str]
    filing_years: list[int]
    files: list[PublishedFile]
    rows: dict[str, int] = field(default_factory=dict)  # total and by amount_type
    match_tiers: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False) + "\n"

    def urls(self, base: str = PUBLIC_BASE_URL, prefix: str = DEFAULT_PREFIX) -> list[str]:
        """The list a reader passes to ``read_parquet([...])`` for this version."""
        return [f"{base}/{prefix}/{self.dataset_version}/{f.path}" for f in self.files]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _year_dirs(parquet_dir: Path) -> list[tuple[int, list[Path]]]:
    root = parquet_dir / "grants"
    out = []
    for p in sorted(root.glob("filing_year=*")):
        shards = sorted(p.glob("*.parquet"))
        if shards:
            out.append((int(p.name.split("=", 1)[1]), shards))
    return out


def _list(paths: list[Path]) -> str:
    return "[" + ", ".join(f"'{p.as_posix()}'" for p in paths) + "]"


def stage(
    parquet_dir: Path,
    staging_dir: Path,
    *,
    dataset_version: str | None = None,
    bmf_vintage: str | None = None,
    now: datetime | None = None,
) -> Manifest:
    """Merge each year's shards into one sorted object under ``staging_dir/<version>/``.

    ``dataset_version`` defaults to the one the rows carry; if given, it must agree with them.
    A staging directory for the version is replaced wholesale.
    """
    years = _year_dirs(parquet_dir)
    if not years:
        raise PublishError(f"no grants partitions under {parquet_dir}")
    conn = duckdb.connect()
    every_shard = [s for _, shards in years for s in shards]
    versions = conn.execute(
        f"SELECT DISTINCT dataset_version, concordance_version "
        f"FROM read_parquet({_list(every_shard)}, hive_partitioning = false)"
    ).fetchall()
    found = sorted({v for v, _ in versions})
    if len(found) != 1:
        raise PublishError(f"rows carry {len(found)} dataset versions: {found}; publish one")
    if dataset_version and dataset_version != found[0]:
        raise PublishError(f"rows carry dataset_version {found[0]}, not {dataset_version}")
    dataset_version = found[0]
    concordance_version = sorted({c for _, c in versions if c})[0] if versions else None

    root = staging_dir / dataset_version
    if root.exists():
        shutil.rmtree(root)
    files: list[PublishedFile] = []
    for year, shards in years:
        rel = f"grants/filing_year={year}/part-0000.parquet"
        out = root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(
            f"COPY (SELECT * FROM read_parquet({_list(shards)}, hive_partitioning = false) "
            "ORDER BY funder_ein, tax_year NULLS FIRST, amount_usd DESC NULLS LAST) "
            f"TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"
        )
        (rows,) = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{out.as_posix()}')").fetchone()
        files.append(PublishedFile(rel, year, out.stat().st_size, _sha256(out), rows))

    staged = [root / f.path for f in files]
    totals = conn.execute(
        f"SELECT amount_type, COUNT(*) FROM read_parquet({_list(staged)}) GROUP BY 1 ORDER BY 1"
    ).fetchall()
    tiers = conn.execute(
        f"SELECT match_tier, COUNT(*) FROM read_parquet({_list(staged)}) GROUP BY 1 ORDER BY 1"
    ).fetchall()
    conn.close()

    manifest = Manifest(
        dataset_version=dataset_version,
        generated_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        concordance_version=concordance_version,
        bmf_vintage=bmf_vintage,
        license=DATASET_LICENSE,
        columns=list(COLUMNS),
        filing_years=[y for y, _ in years],
        files=files,
        rows={"total": sum(f.rows for f in files), **{k: n for k, n in totals}},
        match_tiers={k or "null": n for k, n in tiers},
    )
    (root / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest


class Uploader(Protocol):
    def put(self, key: str, path: Path, content_type: str) -> None: ...


class DirUploader:
    """Copies objects into a directory tree. Tests, and ``--to-dir`` rehearsals."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, path: Path, content_type: str) -> None:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)


class WranglerUploader:
    """One ``wrangler r2 object put`` per object, on the machine's existing login."""

    def __init__(self, bucket: str, *, wrangler: list[str] | None = None) -> None:
        self.bucket = bucket
        self.wrangler = wrangler or [shutil.which("npx") or "npx", "--yes", "wrangler"]

    def put(self, key: str, path: Path, content_type: str) -> None:
        cmd = [
            *self.wrangler,
            "r2",
            "object",
            "put",
            f"{self.bucket}/{key}",
            "--file",
            str(path),
            "--content-type",
            content_type,
            "--remote",
        ]
        # wrangler prints emoji; on Windows the default console codec cannot decode them.
        done = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
        if done.returncode != 0:
            raise PublishError(f"upload of {key} failed: {done.stderr.strip()[-400:]}")


def upload(
    staging_dir: Path,
    manifest: Manifest,
    uploader: Uploader,
    *,
    prefix: str = DEFAULT_PREFIX,
    latest: bool = True,
) -> list[str]:
    """Upload the staged version, and the same objects again under ``latest/`` if asked."""
    root = staging_dir / manifest.dataset_version
    objects = [Path(f.path) for f in manifest.files] + [Path("manifest.json")]
    keys: list[str] = []
    for label in [manifest.dataset_version, "latest"] if latest else [manifest.dataset_version]:
        for rel in objects:
            key = f"{prefix}/{label}/{rel.as_posix()}"
            uploader.put(
                key, root / rel, _CONTENT_TYPES.get(rel.suffix, "application/octet-stream")
            )
            keys.append(key)
    return keys
