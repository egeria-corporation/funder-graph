"""The bulk uploader against a fake S3 client: keys, content types, skipping, retries, secrets."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from funder_graph.pipeline.r2_upload import (
    MissingCredentials,
    R2Config,
    S3Uploader,
    plan,
    upload_tree,
)


class FakeClient:
    def __init__(self, existing: dict[str, str] | None = None, fail_keys: set[str] | None = None):
        self.existing = existing or {}
        self.fail_keys = fail_keys or set()
        self.calls: list[tuple[str, str, str]] = []
        self.attempts: dict[str, int] = {}

    def upload_file(
        self, Filename: str, Bucket: str, Key: str, ExtraArgs: dict | None = None
    ) -> None:
        self.attempts[Key] = self.attempts.get(Key, 0) + 1
        if Key in self.fail_keys:
            raise ConnectionError("simulated")
        self.calls.append((Key, Bucket, (ExtraArgs or {}).get("ContentType", "")))

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        existing = self.existing

        class P:
            def paginate(self, Bucket: str, Prefix: str):
                yield {
                    "Contents": [
                        {"Key": k, "ETag": f'"{v}"'}
                        for k, v in existing.items()
                        if k.startswith(Prefix)
                    ]
                }

        return P()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "2026.09.0"
    (root / "funders").mkdir(parents=True)
    (root / "funders" / "111.json").write_text('{"ein":"111"}', encoding="utf-8")
    (root / "funders" / "222" / "2023").mkdir(parents=True)
    (root / "funders" / "222" / "2023" / "p1.json").write_text("{}", encoding="utf-8")
    (root / "sitemaps").mkdir()
    (root / "sitemaps" / "funders-00001.xml.gz").write_bytes(b"\x1f\x8b")
    (root / "recipients").mkdir()
    (root / "recipients" / "333.json").write_text("{}", encoding="utf-8")
    (root / "site-manifest.json").write_text("{}", encoding="utf-8")
    return root


class TestPlan:
    def test_keys_are_prefix_plus_posix_relative_path_with_content_types(self, tree: Path) -> None:
        items = plan(tree, "funder-graph/2026.09.0")
        keys = {i.key: i.content_type for i in items}
        assert keys["funder-graph/2026.09.0/funders/111.json"] == "application/json"
        assert keys["funder-graph/2026.09.0/funders/222/2023/p1.json"] == "application/json"
        assert keys["funder-graph/2026.09.0/sitemaps/funders-00001.xml.gz"] == "application/gzip"
        assert keys["funder-graph/2026.09.0/site-manifest.json"] == "application/json"
        assert len(items) == 5

    def test_only_restricts_to_top_level_names(self, tree: Path) -> None:
        items = plan(tree, "p", only=["funders", "sitemaps"])
        assert all(i.key.split("/")[1] in {"funders", "sitemaps"} for i in items)
        assert len(items) == 3


class TestUploadTree:
    def test_uploads_everything_in_parallel_and_reports(self, tree: Path) -> None:
        client = FakeClient()
        seen: list[tuple[int, int]] = []
        result = upload_tree(
            client,
            "opengrants-data",
            tree,
            "funder-graph/2026.09.0",
            workers=4,
            progress=lambda i, n, _r: seen.append((i, n)),
        )
        assert result.ok and result.uploaded == 5 and result.skipped == 0
        assert {k for k, _, _ in client.calls} == {
            i.key for i in plan(tree, "funder-graph/2026.09.0")
        }
        assert all(b == "opengrants-data" for _, b, _ in client.calls)
        assert seen[-1] == (5, 5)

    def test_skips_objects_whose_etag_matches_the_local_md5(self, tree: Path) -> None:
        f = tree / "funders" / "111.json"
        etag = hashlib.md5(f.read_bytes()).hexdigest()
        client = FakeClient(
            existing={
                "funder-graph/2026.09.0/funders/111.json": etag,
                "funder-graph/2026.09.0/site-manifest.json": "stale",
            }
        )
        result = upload_tree(client, "b", tree, "funder-graph/2026.09.0", workers=2)
        assert result.skipped == 1 and result.uploaded == 4
        assert "funder-graph/2026.09.0/funders/111.json" not in {k for k, _, _ in client.calls}
        assert "funder-graph/2026.09.0/site-manifest.json" in {k for k, _, _ in client.calls}

    def test_retries_three_times_then_reports_the_failure_without_stopping(
        self, tree: Path
    ) -> None:
        bad = "funder-graph/2026.09.0/recipients/333.json"
        client = FakeClient(fail_keys={bad})
        result = upload_tree(
            client, "b", tree, "funder-graph/2026.09.0", workers=2, skip_unchanged=False
        )
        assert not result.ok
        assert result.uploaded == 4 and [k for k, _ in result.failed] == [bad]
        assert client.attempts[bad] == 3
        assert "ConnectionError" in result.failed[0][1]


class TestConfigAndUploader:
    def test_credentials_come_from_the_environment_only_and_say_what_is_missing(self) -> None:
        cfg = R2Config.from_env(
            {"R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "CLOUDFLARE_ACCOUNT_ID": "acct"}
        )
        assert cfg.endpoint == "https://acct.r2.cloudflarestorage.com"
        with pytest.raises(MissingCredentials, match="R2_SECRET_ACCESS_KEY"):
            R2Config.from_env({"R2_ACCESS_KEY_ID": "k", "CLOUDFLARE_ACCOUNT_ID": "acct"})
        with pytest.raises(MissingCredentials, match="never paste it"):
            R2Config.from_env({})

    def test_s3_uploader_fits_the_publish_protocol(self, tmp_path: Path) -> None:
        p = tmp_path / "x.parquet"
        p.write_bytes(b"pq")
        client = FakeClient()
        S3Uploader(client, "opengrants-data").put(
            "funder-graph/v/x.parquet", p, "application/vnd.apache.parquet"
        )
        assert client.calls == [
            ("funder-graph/v/x.parquet", "opengrants-data", "application/vnd.apache.parquet")
        ]


class TestEnvFile:
    def test_env_file_values_feed_the_config_and_never_touch_the_process_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        from funder_graph.pipeline.r2_upload import load_env_file

        f = tmp_path / "r2.env"
        f.write_text(
            "# the R2 token for the ingest\n"
            "R2_ACCESS_KEY_ID=abc123\n"
            'R2_SECRET_ACCESS_KEY="s3cr3t=with=equals"\n'
            "export CLOUDFLARE_ACCOUNT_ID = a5a2b776dd851a4e605683ef858e50de\n"
            "\n",
            encoding="utf-8",
        )
        for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID"):
            monkeypatch.delenv(k, raising=False)
        values = load_env_file(f)
        assert values == {
            "R2_ACCESS_KEY_ID": "abc123",
            "R2_SECRET_ACCESS_KEY": "s3cr3t=with=equals",
            "CLOUDFLARE_ACCOUNT_ID": "a5a2b776dd851a4e605683ef858e50de",
        }
        cfg = R2Config.from_env(values)
        assert cfg.access_key_id == "abc123"
        assert cfg.endpoint == "https://a5a2b776dd851a4e605683ef858e50de.r2.cloudflarestorage.com"
        assert "R2_SECRET_ACCESS_KEY" not in os.environ

    def test_missing_env_file_is_a_clear_error(self, tmp_path: Path) -> None:
        from funder_graph.pipeline.r2_upload import load_env_file

        with pytest.raises(MissingCredentials, match="does not exist"):
            load_env_file(tmp_path / "nope.env")
