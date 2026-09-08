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
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5

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


def make_client(cfg: R2Config, *, pool_connections: int = 64) -> S3Client:
    """An S3 client for R2. ``pool_connections`` must exceed the uploader's worker count.

    Retries are ``standard``, not ``adaptive``, deliberately. Adaptive mode adds a
    *client-side* rate limiter shared by every thread: one burst of throttled responses
    collapses its token bucket and it recovers over many minutes. A 1.1M-object run took a
    burst of failures at 130,000 objects, fell from ~127 objects/s to ~7, and never came
    back. Standard mode retries the request that was throttled without penalising the
    other 63 workers.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
        config=Config(
            max_pool_connections=pool_connections,
            retries={"max_attempts": 6, "mode": "standard"},
            # Without these a hung connection holds a worker for botocore's 60s default on
            # connect as well as read, which on a 1M-object run is indistinguishable from
            # a stall.
            connect_timeout=15,
            read_timeout=60,
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
    on_failure: Callable[[str, str], None] | None = None,
    on_listed: Callable[[int], None] | None = None,
    items: list[Planned] | None = None,
) -> UploadResult:
    """Upload every file under ``root`` to ``bucket`` at ``prefix/...``, in parallel.

    ``on_failure(key, error)`` fires the moment an object gives up, so a run that degrades
    after five hours says why while it is still running rather than only in its summary.

    ``items`` is a plan the caller has already built. Walking 1.1M payloads across 197,000
    directories and stat-ing each one takes minutes on NTFS, and a caller that printed the
    object count first was paying for it twice before any byte moved.

    ``on_listed(n)`` fires once the bucket has been enumerated for skip-if-unchanged, which
    on a full prefix is minutes of listing before the first object moves. A caller timing
    throughput has to start its clock here or it reports the listing as slow uploading.
    """
    items = plan(root, prefix, only=only) if items is None else items
    result = UploadResult()
    have = existing_etags(client, bucket, prefix) if skip_unchanged else {}
    if on_listed:
        on_listed(len(have))

    def one(item: Planned) -> tuple[str, str | None, int]:
        # The membership test comes first: ``have.get(key) == _md5(path)`` evaluates both
        # sides, so a first upload of a million objects hashes every one of them against a
        # key that is not there.
        if skip_unchanged and item.key in have and have[item.key] == _md5(item.path):
            return item.key, "skip", 0
        for attempt in range(ATTEMPTS):
            try:
                client.upload_file(
                    str(item.path), bucket, item.key, ExtraArgs={"ContentType": item.content_type}
                )
                return item.key, None, item.path.stat().st_size
            except Exception as error:
                last = f"{type(error).__name__}: {error}"
                if attempt == ATTEMPTS - 1:
                    return item.key, last, 0
                # Back off between our own attempts; retrying a throttled request instantly
                # is how a burst of 429s becomes a sustained one.
                time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
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
                if on_failure:
                    on_failure(key, status)
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
