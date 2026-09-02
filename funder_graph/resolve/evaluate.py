"""The labeled evaluation set and the per-tier precision gates.

The build spec's precision targets, treated as gates: tier A 100%, B at least 99%, C at least
95%, D at least 80%, measured on at least 1,000 hand-verified pairs sampled stratified across
tiers and committed at ``tests/fixtures/matching/labeled_pairs.csv``. Below 1,000 rows the gate
is not "passed on a small sample"; it is unevaluable, and ``build eval`` says so and fails. If
a tier misses its target the fix is to demote rows out of it, never to loosen the target.

``sample_for_labeling`` draws the rows to verify from the matcher's own ``resolutions`` table,
stratified across its tiers so every band is represented. Verification must be independent of
the matcher's suggestion: the person checks the BMF and the filing, and the suggestion columns
are there to make that faster, not to be copied.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from funder_graph.resolve.match import Recipient, Resolution, resolve_all, valid_ein
from funder_graph.resolve.normalize import normalize_name

MIN_LABELED = 1000
PRECISION_TARGETS = {"A": 1.00, "B": 0.99, "C": 0.95, "D": 0.80}

LABELED_COLUMNS = (
    "recipient_name_raw",
    "recipient_city",
    "recipient_state",
    "recipient_zip5",
    "recipient_ein_reported",
    "expected_ein",
    "verified_by",
    "verified_on",
    "source",
    "note",
)
SUGGESTION_COLUMNS = ("matcher_ein", "matcher_tier", "matcher_method", "matcher_bmf_name")
_REQUIRED = ("recipient_name_raw", "verified_by", "verified_on", "source")


@dataclass(frozen=True)
class LabeledPair:
    name_raw: str
    city: str | None
    state: str | None
    zip5: str | None
    ein_reported: str | None
    expected_ein: str | None  # None: verified to have no resolvable EIN in the BMF
    verified_by: str
    verified_on: str
    source: str

    def recipient(self) -> Recipient:
        return Recipient(
            normalize_name(self.name_raw),
            name_raw=self.name_raw,
            city=self.city,
            state=self.state,
            zip5=self.zip5,
            ein_reported=self.ein_reported,
        )


def load_labeled(path: Path) -> list[LabeledPair]:
    """Every row needs a verifier, a date, and a source; ``expected_ein`` is nine digits or empty."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in LABELED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing columns {missing}")
        pairs = []
        for line, row in enumerate(reader, start=2):
            for column in _REQUIRED:
                if not (row.get(column) or "").strip():
                    raise ValueError(f"{path}:{line}: {column} is required on every labeled row")
            raw_expected = (row.get("expected_ein") or "").strip()
            expected = valid_ein(raw_expected)
            if raw_expected and not expected:
                raise ValueError(f"{path}:{line}: expected_ein {raw_expected!r} is not nine digits")
            pairs.append(
                LabeledPair(
                    name_raw=row["recipient_name_raw"].strip(),
                    city=(row.get("recipient_city") or "").strip() or None,
                    state=(row.get("recipient_state") or "").strip() or None,
                    zip5=(row.get("recipient_zip5") or "").strip() or None,
                    ein_reported=(row.get("recipient_ein_reported") or "").strip() or None,
                    expected_ein=expected,
                    verified_by=row["verified_by"].strip(),
                    verified_on=row["verified_on"].strip(),
                    source=row["source"].strip(),
                )
            )
    return pairs


@dataclass
class TierStats:
    tier: str
    resolved: int = 0
    correct: int = 0

    @property
    def precision(self) -> float | None:
        return self.correct / self.resolved if self.resolved else None


@dataclass
class Evaluation:
    n: int
    tiers: dict[str, TierStats]
    with_expected: int = 0
    recalled: int = 0  # labeled rows with an EIN that were resolved to it, at any tier
    expected_unresolved: int = 0
    abstained: int = 0  # labeled rows with no EIN that came back U
    misses: list[tuple[LabeledPair, Resolution]] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        return self.recalled / self.with_expected if self.with_expected else None

    @property
    def abstention(self) -> float | None:
        return self.abstained / self.expected_unresolved if self.expected_unresolved else None

    def gate(self) -> list[str]:
        """Reasons the gate fails; empty means it passes."""
        failures = []
        if self.n < MIN_LABELED:
            failures.append(
                f"labeled set has {self.n:,} rows; {MIN_LABELED:,} hand-verified pairs are "
                "required before the per-tier precision targets mean anything"
            )
        for tier, target in PRECISION_TARGETS.items():
            stats = self.tiers[tier]
            if stats.precision is not None and stats.precision < target:
                failures.append(
                    f"tier {tier} precision {stats.precision:.1%} is below the {target:.0%} "
                    f"target ({stats.correct}/{stats.resolved}); demote rows out of the tier"
                )
        return failures


def evaluate(conn: duckdb.DuckDBPyConnection, pairs: list[LabeledPair]) -> Evaluation:
    """Resolve every labeled pair against the loaded BMF and score the answers per tier."""
    ev = Evaluation(n=len(pairs), tiers={t: TierStats(t) for t in ("A", "B", "C", "D")})
    if not pairs:
        return ev
    resolutions = resolve_all(conn, [p.recipient() for p in pairs])
    for pair, res in zip(pairs, resolutions, strict=True):
        if pair.expected_ein:
            ev.with_expected += 1
        else:
            ev.expected_unresolved += 1
        if res.tier == "U":
            if pair.expected_ein is None:
                ev.abstained += 1
            continue
        stats = ev.tiers[res.tier]
        stats.resolved += 1
        if res.ein == pair.expected_ein:
            stats.correct += 1
            ev.recalled += 1
        else:
            ev.misses.append((pair, res))
    return ev


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def write_matching_eval(
    ev: Evaluation, path: Path, *, bmf_vintage: str, now: datetime, labeled_path: Path
) -> None:
    lines = [
        "# Matching evaluation",
        "",
        f"Generated {now:%Y-%m-%d %H:%M} UTC against BMF vintage `{bmf_vintage}` on "
        f"{ev.n:,} labeled pairs from `{labeled_path.as_posix()}`.",
        "",
        "| Tier | Resolved | Correct | Precision | Target | Status |",
        "|---|---|---|---|---|---|",
    ]
    for tier, target in PRECISION_TARGETS.items():
        s = ev.tiers[tier]
        if s.precision is None:
            status = "no rows"
        else:
            status = "meets" if s.precision >= target else "**BELOW**"
        lines.append(
            f"| {tier} | {s.resolved:,} | {s.correct:,} | {_pct(s.precision)} | {target:.0%} | {status} |"
        )
    lines += [
        "",
        f"- Recall at any tier over the {ev.with_expected:,} pairs with a verified EIN: "
        f"{_pct(ev.recall)}.",
        f"- Abstention over the {ev.expected_unresolved:,} pairs verified to have no resolvable "
        f"EIN (came back `U`): {_pct(ev.abstention)}.",
        "",
    ]
    failures = ev.gate()
    if failures:
        lines.append("## Gate: FAIL")
        lines += [f"- {f}" for f in failures]
    else:
        lines.append("## Gate: PASS")
    if ev.misses:
        lines += [
            "",
            f"## Misses ({len(ev.misses):,})",
            "",
            "| Recipient | State | Expected | Resolved to | Tier | Method |",
            "|---|---|---|---|---|---|",
        ]
        for pair, res in ev.misses[:100]:
            lines.append(
                f"| {pair.name_raw} | {pair.state or ''} | {pair.expected_ein or '(none)'} | "
                f"{res.ein or ''} | {res.tier} | {res.method or ''} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_SAMPLE_SQL = """
SELECT name_raw, city, state, zip5, ein_reported, ein, tier, method, bmf_name
FROM resolutions
WHERE tier = ? AND recipient_type = 'organization' AND name_raw IS NOT NULL
ORDER BY hash(name_normalized || '|' || COALESCE(state, '') || '|' || COALESCE(zip5, '') || ?)
LIMIT ?
"""


def sample_for_labeling(conn: duckdb.DuckDBPyConnection, n: int, *, seed: str) -> list[dict]:
    """``n`` rows from ``resolutions``, an equal share per tier, deterministic for a seed.

    The output has the labeled-set columns with ``expected_ein`` and the verification columns
    empty, plus the matcher's suggestion columns for the verifier's convenience.
    """
    tiers = ("A", "B", "C", "D", "U")
    per = max(n // len(tiers), 1)
    out: list[dict] = []
    for tier in tiers:
        for name_raw, city, state, zip5, ein_reported, ein, t, method, bmf_name in conn.execute(
            _SAMPLE_SQL, [tier, seed, per]
        ).fetchall():
            out.append(
                {
                    "recipient_name_raw": name_raw,
                    "recipient_city": city or "",
                    "recipient_state": state or "",
                    "recipient_zip5": zip5 or "",
                    "recipient_ein_reported": ein_reported or "",
                    "expected_ein": "",
                    "verified_by": "",
                    "verified_on": "",
                    "source": "",
                    "note": "",
                    "matcher_ein": ein or "",
                    "matcher_tier": t,
                    "matcher_method": method or "",
                    "matcher_bmf_name": bmf_name or "",
                }
            )
    return out


def write_sample(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*LABELED_COLUMNS, *SUGGESTION_COLUMNS])
        writer.writeheader()
        writer.writerows(rows)
