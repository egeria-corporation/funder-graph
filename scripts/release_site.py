"""Release the hosted site for one dataset version, end to end, in order, logged.

    python scripts/release_site.py --env-file C:/Users/sturb/.config/funder-graph/r2.env
        [--version 2026.09.0] [--years 2019-2026] [--workers 64] [--skip-ingest]

Steps, each gated on the previous one's exit code, each logged to build/logs/release-<step>.log:

1. ingest   `build site` over every filing year in build/parquet, BMF from build/bmf/*.csv
2. upload   `build site-upload` through the R2 S3 API; unchanged objects are skipped
3. reseed   site/scripts/reseed_remote.py: replace the D1 index, mark current, stamp the cache
4. check    five live URLs must answer as expected

Idempotent: a run that dies can be started again and picks up where the upload left off. It
uses the venv's Python directly (never `uv run`, which re-syncs and can fail on a locked
executable) and never prints a credential.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
LOGS = ROOT / "build" / "logs"
SITE = "https://funders.opengrants.io"


def run(step: str, cmd: list[str]) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"release-{step}.log"
    t0 = time.time()
    print(f"##### {step} start {time.strftime('%H:%M:%S')} -> {log.name}", flush=True)
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(
            cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, check=False
        ).returncode
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
    for line in tail:
        print("   ", line[:200], flush=True)
    print(f"##### {step} exit {rc} after {(time.time() - t0) / 60:.0f} min", flush=True)
    return rc


def check(expect: dict[str, int]) -> int:
    print(f"##### check {time.strftime('%H:%M:%S')}", flush=True)
    bad = 0
    for path, want in expect.items():
        try:
            req = urllib.request.Request(
                SITE + path, headers={"User-Agent": "funder-graph-release/1"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                got = resp.status
                body = resp.read() if path == "/" else b""
        except urllib.error.HTTPError as e:
            got, body = e.code, b""
        ok = got == want
        bad += not ok
        print(f"    {path:<36} HTTP {got} {'ok' if ok else f'expected {want}'}", flush=True)
        if path == "/" and body:
            import re

            bigs = re.findall(rb'<p class="big">([0-9,]+)</p>', body)[:3]
            print("    landing counts:", " / ".join(b.decode() for b in bigs), flush=True)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--version", default="2026.09.0")
    ap.add_argument("--years", default=None, help="e.g. 2019-2026; default is every year present")
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument(
        "--skip-ingest", action="store_true", help="Upload what build/site already holds"
    )
    args = ap.parse_args()
    if not Path(args.env_file).exists():
        print(f"env file {args.env_file} does not exist", flush=True)
        return 2
    cli = [str(PY), "-m", "funder_graph.cli"]
    if not args.skip_ingest:
        cmd = [*cli, "build", "site", "--work-dir", "build", "--bmf-csv", "build/bmf"]
        if args.years:
            cmd += ["--years", args.years]
        if run("ingest", cmd):
            return 1
        manifest = json.loads(
            (ROOT / "build" / "site" / args.version / "site-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        print(
            f"    ingest: {manifest['funders']:,} funders, {manifest['recipients']:,} recipients, {manifest['grant_rows']:,} rows",
            flush=True,
        )
    if run(
        "upload",
        [
            *cli,
            "build",
            "site-upload",
            "--work-dir",
            "build",
            "--dataset-version",
            args.version,
            "--workers",
            str(args.workers),
            "--env-file",
            args.env_file,
        ],
    ):
        print(
            "upload reported failures; not reseeding. Run again: unchanged objects are skipped.",
            flush=True,
        )
        return 1
    if run(
        "reseed",
        [str(PY), str(ROOT / "site" / "scripts" / "reseed_remote.py"), "--version", args.version],
    ):
        return 1
    time.sleep(5)
    return check(
        {
            "/": 200,
            "/funders/942278431": 200,
            "/recipients/363673599": 200,
            "/funders/110303001/2022?page=65": 200,
            "/funders/000000001": 404,
        }
    )


if __name__ == "__main__":
    sys.exit(main())
