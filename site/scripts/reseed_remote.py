"""Reseed the remote D1 index from build/site/<version>/d1/*.sql and stamp the cache.

    python site/scripts/reseed_remote.py --version 2026.09.0 [--site-dir build/site]

Steps, in order: merge the ingest's INSERT batches into files under 5 MB (wrangler's remote
execute is fast per file, slow per invocation), run a delete prelude so the tables are
replaced rather than appended to, execute every file, mark the version current, and write
the ingest's ``built_at`` to KV as ``current_dataset_stamp`` so every cached page re-reads.

This is the interim form of the ingest job's "build a new table set and swap"; it replaces
the live index in place, so the site can serve partial results for the minutes it runs.
The proper swap comes with the monthly job. Runs wrangler from site/ on its existing login.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
PNPM = shutil.which("pnpm") or "pnpm"


def wrangler(*args: str) -> str:
    done = subprocess.run(
        [PNPM, "exec", "wrangler", *args],
        cwd=SITE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**__import__("os").environ, "CI": "true"},
        check=False,
    )
    return (done.stdout or "") + (done.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--site-dir", default=str(SITE.parent / "build" / "site"))
    ap.add_argument("--database", default="funder-graph")
    args = ap.parse_args()
    root = Path(args.site_dir) / args.version
    manifest = json.loads((root / "site-manifest.json").read_text(encoding="utf-8"))
    src = sorted((root / "d1").glob("*.sql"))
    out = Path(args.site_dir) / "d1-remote"
    out.mkdir(exist_ok=True)
    for p in out.glob("*.sql"):
        p.unlink()
    (out / "r000.sql").write_text(
        "DELETE FROM entity_search; DELETE FROM funders; DELETE FROM recipients; "
        "DELETE FROM dataset_vintage;\n",
        encoding="utf-8",
    )
    buf: list[str] = []
    size = n = 0
    for p in src:
        t = p.read_text(encoding="utf-8")
        buf.append(t)
        size += len(t)
        if size > 5_000_000:
            n += 1
            (out / f"r{n:03d}.sql").write_text("".join(buf), encoding="utf-8")
            buf, size = [], 0
    if buf:
        n += 1
        (out / f"r{n:03d}.sql").write_text("".join(buf), encoding="utf-8")
    files = sorted(out.glob("r*.sql"))
    print(f"reseed {args.version}: {len(src)} batches -> {len(files)} files", flush=True)
    failed = 0
    for i, f in enumerate(files, 1):
        res = wrangler("d1", "execute", args.database, "--remote", f"--file={f}")
        ok = "Executed" in res
        failed += not ok
        if not ok or i % 25 == 0 or i == len(files):
            tail = "" if ok else " :: " + " ".join(res.split())[-160:]
            print(f"  {i}/{len(files)} {f.name} {'ok' if ok else 'FAILED'}{tail}", flush=True)
    res = wrangler(
        "d1",
        "execute",
        args.database,
        "--remote",
        "--command",
        f"UPDATE dataset_vintage SET is_current = 1 WHERE version = '{args.version}'; "
        "SELECT (SELECT COUNT(*) FROM funders) AS funders, "
        "(SELECT COUNT(*) FROM recipients) AS recipients, "
        "(SELECT COUNT(*) FROM entity_search) AS fts",
    )
    counts = " ".join(
        line.strip()
        for line in res.splitlines()
        if any(k in line for k in ("funders", "recipients", "fts"))
    )
    print("  counts:", counts[:200], flush=True)
    stamp = manifest["built_at"]
    res = wrangler(
        "kv", "key", "put", "--remote", "--binding", "VINTAGE", "current_dataset_stamp", stamp
    )
    print(
        f"  stamp {stamp}: {'written' if 'Writing' in res else 'FAILED ' + res[-120:]}", flush=True
    )
    print(f"done: {len(files) - failed}/{len(files)} files, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
