"""The publish stage: one object per year, a manifest that checks out, latest as a copy."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from test_write import BRODERICK, HARDSHIP, SCHED_I, records

from funder_graph.pipeline.publish import (
    DirUploader,
    Manifest,
    PublishError,
    stage,
    upload,
)
from funder_graph.pipeline.write import COLUMNS, write_shard


@pytest.fixture
def parquet_dir(tmp_path: Path) -> Path:
    out = tmp_path / "parquet"
    # Two shards for 2023, deliberately out of global order across shards; one for 2024.
    write_shard(records(SCHED_I), out, 2023, 0)
    write_shard(records(BRODERICK) + records(HARDSHIP), out, 2023, 1)
    write_shard(records(BRODERICK, filing_year=2024), out, 2024, 0)
    return out


class TestStage:
    def test_one_object_per_year_with_every_row(self, parquet_dir: Path, tmp_path: Path) -> None:
        m = stage(parquet_dir, tmp_path / "staging", now=datetime(2026, 9, 2, tzinfo=UTC))
        assert [f.path for f in m.files] == [
            "grants/filing_year=2023/part-0000.parquet",
            "grants/filing_year=2024/part-0000.parquet",
        ]
        assert m.filing_years == [2023, 2024]
        shards = sorted((parquet_dir / "grants").rglob("*.parquet"))
        expected = sum(
            duckdb.sql(f"SELECT COUNT(*) FROM read_parquet('{s.as_posix()}')").fetchone()[0]
            for s in shards
        )
        assert m.rows["total"] == sum(f.rows for f in m.files) == expected
        assert m.columns == list(COLUMNS)
        assert m.license == "Apache-2.0" and m.generated_at == "2026-09-02T00:00:00+00:00"

    def test_manifest_checksums_and_sizes_are_the_files(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        m = stage(parquet_dir, tmp_path / "staging")
        root = tmp_path / "staging" / m.dataset_version
        for f in m.files:
            p = root / f.path
            assert p.stat().st_size == f.bytes
            assert hashlib.sha256(p.read_bytes()).hexdigest() == f.sha256
        on_disk = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert on_disk["dataset_version"] == m.dataset_version
        assert [f["sha256"] for f in on_disk["files"]] == [f.sha256 for f in m.files]

    def test_merged_object_is_globally_sorted_like_a_shard(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        m = stage(parquet_dir, tmp_path / "staging")
        p = tmp_path / "staging" / m.dataset_version / m.files[0].path
        got = duckdb.sql(f"SELECT funder_ein FROM read_parquet('{p.as_posix()}')").fetchall()
        eins = [e for (e,) in got]
        assert eins == sorted(eins)

    def test_individuals_are_never_staged(self, parquet_dir: Path, tmp_path: Path) -> None:
        assert (parquet_dir / "grants_individuals").exists()
        m = stage(parquet_dir, tmp_path / "staging")
        root = tmp_path / "staging" / m.dataset_version
        assert not (root / "grants_individuals").exists()
        assert all("individual" not in f.path for f in m.files)

    def test_version_comes_from_the_rows_and_must_agree(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        m = stage(parquet_dir, tmp_path / "staging")
        carried = duckdb.sql(
            f"SELECT DISTINCT dataset_version FROM read_parquet('{(parquet_dir / 'grants' / 'filing_year=2023' / 'part-0000.parquet').as_posix()}')"
        ).fetchone()[0]
        assert m.dataset_version == carried
        with pytest.raises(PublishError, match=r"not 1999\.01\.0"):
            stage(parquet_dir, tmp_path / "staging", dataset_version="1999.01.0")

    def test_empty_parquet_dir_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(PublishError, match="no grants partitions"):
            stage(tmp_path / "nothing", tmp_path / "staging")

    def test_urls_are_the_readme_layout(self, parquet_dir: Path, tmp_path: Path) -> None:
        m = stage(parquet_dir, tmp_path / "staging")
        assert m.urls() == [
            f"https://data.opengrants.io/funder-graph/{m.dataset_version}/grants/filing_year=2023/part-0000.parquet",
            f"https://data.opengrants.io/funder-graph/{m.dataset_version}/grants/filing_year=2024/part-0000.parquet",
        ]


class TestUpload:
    def _staged(self, parquet_dir: Path, tmp_path: Path) -> tuple[Path, Manifest]:
        staging = tmp_path / "staging"
        return staging, stage(parquet_dir, staging)

    def test_version_and_latest_are_the_same_objects(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        staging, m = self._staged(parquet_dir, tmp_path)
        bucket = tmp_path / "bucket"
        keys = upload(staging, m, DirUploader(bucket), prefix="funder-graph", latest=True)
        v = m.dataset_version
        assert keys == [
            f"funder-graph/{v}/grants/filing_year=2023/part-0000.parquet",
            f"funder-graph/{v}/grants/filing_year=2024/part-0000.parquet",
            f"funder-graph/{v}/manifest.json",
            "funder-graph/latest/grants/filing_year=2023/part-0000.parquet",
            "funder-graph/latest/grants/filing_year=2024/part-0000.parquet",
            "funder-graph/latest/manifest.json",
        ]
        for f in m.files:
            a = (bucket / "funder-graph" / v / f.path).read_bytes()
            b = (bucket / "funder-graph" / "latest" / f.path).read_bytes()
            assert a == b and hashlib.sha256(a).hexdigest() == f.sha256

    def test_without_latest_only_the_version_is_written(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        staging, m = self._staged(parquet_dir, tmp_path)
        bucket = tmp_path / "bucket"
        keys = upload(staging, m, DirUploader(bucket), latest=False)
        assert len(keys) == 3 and not (bucket / "funder-graph" / "latest").exists()

    def test_duckdb_reads_the_published_layout_with_a_url_list(
        self, parquet_dir: Path, tmp_path: Path
    ) -> None:
        # The README quickstart's shape: an explicit list, hive partitioning on, no glob.
        staging, m = self._staged(parquet_dir, tmp_path)
        bucket = tmp_path / "bucket"
        upload(staging, m, DirUploader(bucket))
        paths = [(bucket / "funder-graph" / "latest" / f.path).as_posix() for f in m.files]
        rows = duckdb.sql(
            f"SELECT filing_year, COUNT(*) FROM read_parquet({json.dumps(paths)}, hive_partitioning = 1) "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall()
        assert [y for y, _ in rows] == [2023, 2024]
        assert sum(n for _, n in rows) == m.rows["total"]


class TestWranglerUploader:
    def test_command_shape_and_non_zero_exit_is_an_error(self, tmp_path: Path) -> None:
        import sys

        from funder_graph.pipeline.publish import WranglerUploader

        obj = tmp_path / "x.parquet"
        obj.write_bytes(b"pq")
        seen = tmp_path / "seen.txt"
        # A stand-in for wrangler: records its arguments, prints an emoji, exits 1.
        fake = tmp_path / "fake.py"
        fake.write_text(
            "import sys, pathlib\n"
            f"pathlib.Path({str(seen)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
            "sys.stdout.buffer.write('\\u26c5 wrangler\\n'.encode('utf-8'))\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        up = WranglerUploader("opengrants-data", wrangler=[sys.executable, str(fake)])
        with pytest.raises(PublishError, match=r"upload of funder-graph/v/x.parquet failed"):
            up.put("funder-graph/v/x.parquet", obj, "application/vnd.apache.parquet")
        args = seen.read_text(encoding="utf-8")
        assert args.startswith("r2 object put opengrants-data/funder-graph/v/x.parquet --file ")
        assert args.endswith("--content-type application/vnd.apache.parquet --remote")
