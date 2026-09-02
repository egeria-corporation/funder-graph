"""Bulk upload to R2 through its S3-compatible API.

``wrangler r2 object put`` is one process per object and about three seconds each; a dataset
version is half a million site payloads. This module uploads a directory tree with a pool of
threads over one authenticated client, skips objects whose content already matches, and never
reads a credential from anywhere but the environment:

    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   an R2 API token with Object Read & Write
    CLOUDFLARE_ACCOUNT_ID                    the account the bucket lives in
    R2_ENDPOINT (optional)                   overrides https://<account>.r2.cloudflarestorage.com

The token is created in the Cloudflare dashboard (R2 -> Manage R2 API Tokens) and set as a
secret where the ingest runs. Nothing here prints or stores it. ``boto3`` is an optional
dependency (``pip install funder-graph[upload]``); the import is deferred so the CLI does not
pay for it.

Skip-if-unchanged compares the local file's MD5 with the object's ETag, which R2 sets to the
MD5 for single-part uploads. Multipart objects carry a different ETag and are re-uploaded;
that only affects objects above the multipart threshold, which no payload reaches.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

CONTENT_TYPES = {
    ".json": "application/json",
    ".gz": "application/gzip",
    ".xml": "application/xml",
    ".parquet": "application/vnd.apache.parquet",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".md": "text/markdown",
}


class MissingCredentials(RuntimeError):
    pass


@dataclass(frozen=True)
class R2Config:
    endpoint: str
    access_key_id: str
    secret_access_key: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> R2Config:
        e = os.environ if env is None else env
        key, secret = e.get("R2_ACCESS_KEY_ID", ""), e.get("R2_SECRET_ACCESS_KEY", "")
        endpoint = e.get("R2_ENDPOINT", "")
        account = e.get("CLOUDFLARE_ACCOUNT_ID", "")
        if not endpoint and account:
            endpoint = f"https://{account}.r2.cloudflarestorage.com"
        missing = [
            n
            for n, v in (
                ("R2_ACCESS_KEY_ID", key),
                ("R2_SECRET_ACCESS_KEY", secret),
                ("CLOUDFLARE_ACCOUNT_ID or R2_ENDPOINT", endpoint),
            )
            if not v
        ]
        if missing:
            raise MissingCredentials(
                "R2 upload needs " + ", ".join(missing) + " in the environment. Create an R2 API "
                "token (Object Read & Write on the bucket) in the Cloudflare dashboard and set it "
                "as a secret where this runs; never paste it into a chat or a file in the repo."
            )
        return cls(endpoint, key, secret)


def load_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a file kept outside the repo, for the three R2 variables.

    Blank lines and ``#`` comments are ignored, a leading ``export`` is tolerated, quotes
    around a value are stripped. The values are returned, not exported: the process
    environment is never modified and nothing is printed.
    """
    if not path.exists():
        raise MissingCredentials(f"env file {path} does not exist")
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def credentials(env_file: Path | None) -> R2Config:
    """The environment, with an env file's values on top when one is given."""
    merged: dict[str, str] = dict(os.environ)
    if env_file is not None:
        merged.update(load_env_file(env_file))
    return R2Config.from_env(merged)


class S3Client(Protocol):
    """The two calls this module makes, so a test can stand in for boto3."""

    def upload_file(
        self, Filename: str, Bucket: str, Key: str, ExtraArgs: dict | None = None
    ) -> None: ...
    def get_paginator(self, name: str) -> Any: ...


def make_client(cfg: R2Config) -> S3Client:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
        config=Config(
            max_pool_connections=64,
            retries={"max_attempts": 6, "mode": "adaptive"},
            s3={"addressing_style": "path"},
        ),
    )


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


@dataclass(frozen=True)
class Planned:
    path: Path
    key: str
    content_type: str


def plan(root: Path, prefix: str, *, only: Iterable[str] | None = None) -> list[Planned]:
    """Every file under ``root`` as (path, key) with keys ``prefix/<relative posix path>``.

    ``only`` restricts to these top-level names under root (e.g. ``funders``, ``sitemaps``).
    """
    keep = set(only) if only else None
    out: list[Planned] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if keep is not None and rel.parts[0] not in keep:
            continue
        out.append(Planned(p, f"{prefix.strip('/')}/{rel.as_posix()}", content_type(p)))
    return out


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def existing_etags(client: S3Client, bucket: str, prefix: str) -> dict[str, str]:
    """ETags of every object under ``prefix``, for skip-if-unchanged."""
    out: dict[str, str] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.strip("/") + "/"):
        for obj in page.get("Contents", []) or []:
            out[obj["Key"]] = obj.get("ETag", "").strip('"')
    return out


@dataclass
class UploadResult:
    uploaded: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    bytes_sent: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def upload_tree(
    client: S3Client,
    bucket: str,
    root: Path,
    prefix: str,
    *,
    only: Iterable[str] | None = None,
    workers: int = 32,
    skip_unchanged: bool = True,
    progress: Callable[[int, int, UploadResult], None] | None = None,
) -> UploadResult:
    """Upload every file under ``root`` to ``bucket`` at ``prefix/...``, in parallel."""
    items = plan(root, prefix, only=only)
    result = UploadResult()
    have = existing_etags(client, bucket, prefix) if skip_unchanged else {}

    def one(item: Planned) -> tuple[str, str | None, int]:
        if skip_unchanged and have.get(item.key) == _md5(item.path):
            return item.key, "skip", 0
        for attempt in range(3):
            try:
                client.upload_file(
                    str(item.path), bucket, item.key, ExtraArgs={"ContentType": item.content_type}
                )
                return item.key, None, item.path.stat().st_size
            except Exception as error:
                last = f"{type(error).__name__}: {error}"
                if attempt == 2:
                    return item.key, last, 0
        return item.key, "unreachable", 0

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (key, status, size) in enumerate(pool.map(one, items), 1):
            if status is None:
                result.uploaded += 1
                result.bytes_sent += size
            elif status == "skip":
                result.skipped += 1
            else:
                result.failed.append((key, status))
            if progress and (i % 500 == 0 or i == len(items)):
                progress(i, len(items), result)
    return result


class S3Uploader:
    """The publish stage's ``Uploader`` protocol over the S3 client, one object at a time."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    def put(self, key: str, path: Path, content_type: str) -> None:
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )
