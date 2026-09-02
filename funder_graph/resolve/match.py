"""Entity resolution: a recipient string to an EIN, with a tier and a confidence that mean something.

The recipient of a 990-PF grant is a name and, usually, a mailing address; the EIN is rarely
reported. Resolution turns those strings into graph edges, and it is where the dataset's
credibility is won or lost, so every rule here is the build spec's rule (section 7), with its
exact confidence bands, and a resolved EIN never leaves this module without a tier.

The shape:

* ``block`` - one SQL pass over the ``bmf`` table returns, per distinct recipient tuple, every
  BMF row surviving any of the blocking keys (exact normalized name, exact sort name, a
  configured alias, state + first token, ZIP5 + first token, state + phonetic key). The
  candidates carry DuckDB's own Jaro-Winkler against both the legal and the sort name, and are
  prefiltered at the probable threshold, so Python never sees the tens of thousands of
  "COMMUNITY ..." rows a state holds.
* ``resolve_one`` - a pure function from one recipient and its candidates to a ``Resolution``.
  This is the whole of the tier logic and it is what the tests exercise.

Two rules the spec singles out are load-bearing and easy to get wrong:

* Ambiguity resolves to U, never to the top candidate. Two candidates within
  ``AMBIGUITY_MARGIN`` of each other is an unknown, not a 51/49 call; a fuzzy (tier D) match
  with any second candidate at all is likewise unknown.
* Chapter organizations (Boys and Girls Club, United Way, ...) need geography. Without ZIP5 or
  city agreement they are capped at tier C and at the middle of its band.

Address disagreement is weak negative evidence only - BMF addresses are often a lawyer, an
accountant, or a lockbox - so it lowers confidence within a band and never vetoes a match.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from pathlib import Path

import duckdb
import pyarrow as pa

from funder_graph.resolve.bmf import first_token
from funder_graph.resolve.normalize import is_chapter_organization
from funder_graph.resolve.phonetic import phonetic_key

# The bands, verbatim from the spec's tier table.
TIER_A_VERIFIED = 1.00
TIER_A_UNVERIFIED = 0.95
TIER_B_FLOOR, TIER_B_CEIL = 0.90, 0.94
TIER_C_FLOOR, TIER_C_CEIL = 0.75, 0.89
TIER_D_FLOOR, TIER_D_CEIL = 0.50, 0.74
AMBIGUITY_MARGIN = 0.03

# Jaro-Winkler thresholds. JW_STRONG is the spec's ("Jaro-Winkler >= 0.94 on normalized name with
# matching ZIP5"). JW_PROBABLE is ours: the floor below which a fuzzy in-state match is not
# offered even as a guess. The blocking query prefilters at it.
JW_STRONG = 0.94
JW_PROBABLE = 0.90

# Within-band adjustments. Agreement is strong positive evidence; disagreement is weak negative
# evidence and only ever moves confidence within a band.
_CITY_AGREES = 0.05
_RAW_NAME_EXACT = 0.04
_ZIP_CONFLICT = -0.03
_CITY_CONFLICT = -0.02
_CHAPTER_CAP = 0.80

SOURCE_BY_TIER = {"B": "bmf_deterministic", "C": "bmf_strong", "D": "bmf_probable"}

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_DIGITS = re.compile(r"\D")


@dataclass(frozen=True)
class Recipient:
    """One distinct recipient tuple, resolved once however many rows carry it."""

    name_normalized: str
    name_raw: str | None = None
    city: str | None = None
    state: str | None = None
    zip5: str | None = None
    ein_reported: str | None = None
    recipient_type: str = "organization"
    alias: str | None = None  # canonical normalized name from data/overrides/name-aliases.csv


@dataclass(frozen=True)
class Candidate:
    """A BMF row that survived a block, with its best Jaro-Winkler against the recipient."""

    ein: str
    name: str
    sort_name: str | None
    name_normalized: str
    sort_name_normalized: str | None
    city: str | None
    state: str | None
    zip5: str | None
    subsection: str | None
    ntee_cd: str | None
    sim: float


@dataclass(frozen=True)
class Resolution:
    ein: str | None
    source: str
    confidence: float | None
    tier: str
    method: str | None
    bmf_name: str | None = None
    ntee_code: str | None = None
    subsection_code: str | None = None


UNRESOLVED = Resolution(None, "unresolved", None, "U", None)


def unresolved(method: str) -> Resolution:
    return replace(UNRESOLVED, method=method)


@dataclass(frozen=True)
class Correction:
    ein: str
    source: str
    note: str | None


def valid_ein(raw: str | None) -> str | None:
    """Nine digits after stripping punctuation, not all zeros; else None."""
    if not raw:
        return None
    digits = _DIGITS.sub("", raw)
    if len(digits) != 9 or digits == "000000000":
        return None
    return digits


def _squash(text: str | None) -> str:
    return _NON_ALNUM.sub("", (text or "").upper())


def _clamp(value: float, floor: float, ceil: float) -> float:
    return round(min(max(value, floor), ceil), 2)


@dataclass(frozen=True)
class _Scored:
    candidate: Candidate
    tier: str
    confidence: float
    method: str


def _score(r: Recipient, c: Candidate) -> _Scored | None:
    """The tier and confidence one candidate earns against one recipient, or None."""
    if r.state and c.state and r.state != c.state:
        return None
    names = (c.name_normalized, c.sort_name_normalized)
    exact = r.name_normalized in names or (r.alias is not None and r.alias in names)
    zip_agree = bool(r.zip5 and c.zip5 and r.zip5 == c.zip5)
    zip_conflict = bool(r.zip5 and c.zip5 and r.zip5 != c.zip5)
    city_agree = bool(r.city and c.city and _squash(r.city) == _squash(c.city))
    city_conflict = bool(r.city and c.city and not city_agree)
    raw_exact = bool(r.name_raw and _squash(r.name_raw) == _squash(c.name))
    in_state = bool(r.state and c.state)

    if exact and zip_agree and in_state:
        conf = TIER_B_FLOOR + (0.02 if city_agree else 0.0) + (0.02 if raw_exact else 0.0)
        return _Scored(c, "B", _clamp(conf, TIER_B_FLOOR, TIER_B_CEIL), "name_zip5_state_exact")
    if exact and in_state:
        conf = 0.80
        conf += _CITY_AGREES if city_agree else 0.0
        conf += _RAW_NAME_EXACT if raw_exact else 0.0
        conf += _ZIP_CONFLICT if zip_conflict else 0.0
        conf += _CITY_CONFLICT if city_conflict else 0.0
        return _Scored(c, "C", _clamp(conf, TIER_C_FLOOR, TIER_C_CEIL), "name_state_exact")
    if exact:
        # No state on either side: an exact name alone is a guess with a number attached.
        conf = 0.70 + (_ZIP_CONFLICT if zip_conflict else 0.0)
        return _Scored(c, "D", _clamp(conf, TIER_D_FLOOR, TIER_D_CEIL), "name_exact_no_state")
    if c.sim >= JW_STRONG and zip_agree and in_state:
        span = (c.sim - JW_STRONG) / (1.0 - JW_STRONG)
        conf = TIER_C_FLOOR + span * 0.08 + (0.06 if city_agree else 0.0)
        return _Scored(c, "C", _clamp(conf, TIER_C_FLOOR, TIER_C_CEIL), "name_jw_zip5")
    if c.sim >= JW_PROBABLE and in_state:
        span = (c.sim - JW_PROBABLE) / (1.0 - JW_PROBABLE)
        conf = TIER_D_FLOOR + span * 0.24
        conf += _ZIP_CONFLICT if zip_conflict else 0.0
        conf += _CITY_CONFLICT if city_conflict else 0.0
        return _Scored(c, "D", _clamp(conf, TIER_D_FLOOR, TIER_D_CEIL), "name_jw_state")
    return None


def _chapter_cap(r: Recipient, s: _Scored) -> _Scored:
    """Chapter organizations without ZIP5 or city agreement: at most tier C, mid-band."""
    if not is_chapter_organization(r.name_normalized):
        return s
    c = s.candidate
    zip_agree = bool(r.zip5 and c.zip5 and r.zip5 == c.zip5)
    city_agree = bool(r.city and c.city and _squash(r.city) == _squash(c.city))
    if zip_agree or city_agree:
        return s
    if s.tier == "B" or (s.tier == "C" and s.confidence > _CHAPTER_CAP):
        return replace(s, tier="C", confidence=_CHAPTER_CAP, method=s.method + "+chapter_capped")
    return s


def _from_candidate(
    c: Candidate, source: str, confidence: float, tier: str, method: str
) -> Resolution:
    return Resolution(
        ein=c.ein,
        source=source,
        confidence=confidence,
        tier=tier,
        method=method,
        bmf_name=c.name,
        ntee_code=c.ntee_cd,
        subsection_code=c.subsection,
    )


def resolve_one(
    r: Recipient,
    candidates: list[Candidate],
    *,
    reported: Candidate | None = None,
    revoked: bool = False,
) -> Resolution:
    """Resolve one recipient tuple.

    ``reported`` is the BMF row for the recipient's reported EIN when it exists; ``revoked`` says
    the reported EIN appears on the Automatic Revocation list. Pure: no I/O.
    """
    if r.recipient_type in ("individual", "government"):
        return unresolved(f"recipient_type_{r.recipient_type}")

    ein = valid_ein(r.ein_reported)
    if ein:
        if reported is not None and reported.ein == ein:
            return _from_candidate(
                reported, "reported_verified", TIER_A_VERIFIED, "A", "reported_ein_in_bmf"
            )
        method = "reported_ein_revoked" if revoked else "reported_ein_not_in_bmf"
        return Resolution(ein, "reported_unverified", TIER_A_UNVERIFIED, "A", method)

    if not r.name_normalized or not candidates:
        return unresolved("no_candidates")

    scored = [s for s in (_score(r, c) for c in candidates) if s is not None]
    scored = [_chapter_cap(r, s) for s in scored]
    if not scored:
        return unresolved("no_candidate_above_threshold")
    scored.sort(key=lambda s: (-s.confidence, s.candidate.ein))

    top = scored[0]
    if len(scored) > 1:
        runner_up = scored[1]
        if top.confidence - runner_up.confidence < AMBIGUITY_MARGIN:
            return unresolved(f"ambiguous_{len(scored)}_candidates")
        if top.tier == "D":
            return unresolved("probable_not_unique")
    return _from_candidate(
        top.candidate, SOURCE_BY_TIER[top.tier], top.confidence, top.tier, top.method
    )


# --- overrides -----------------------------------------------------------------------------


def load_aliases(path: Path) -> dict[str, str]:
    """``alias_normalized -> canonical_normalized`` from data/overrides/name-aliases.csv."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    aliases = {}
    for row in rows:
        if not row.get("source"):
            raise ValueError(f"{path}: every alias row must carry a source; missing on {row!r}")
        aliases[row["alias_normalized"].strip()] = row["canonical_normalized"].strip()
    return aliases


def load_corrections(path: Path) -> dict[tuple[str, str | None, str | None], Correction]:
    """``(name_normalized, state, zip5) -> Correction`` from data/overrides/ein-corrections.csv."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    corrections = {}
    for row in rows:
        ein = valid_ein(row.get("ein"))
        if not ein or not row.get("source"):
            raise ValueError(f"{path}: a correction needs a valid EIN and a source; got {row!r}")
        key = (
            row["recipient_name_normalized"].strip(),
            row.get("state", "").strip() or None,
            row.get("zip5", "").strip() or None,
        )
        corrections[key] = Correction(ein, row["source"].strip(), row.get("note") or None)
    return corrections


def apply_correction(
    r: Recipient, resolution: Resolution, corrections, bmf_row: Candidate | None
) -> Resolution:
    """A manual correction outranks the automated result and is published as its own source."""
    correction = corrections.get((r.name_normalized, r.state, r.zip5))
    if correction is None:
        return resolution
    return Resolution(
        ein=correction.ein,
        source="manual_correction",
        confidence=TIER_A_VERIFIED,
        tier="A",
        method="manual_correction",
        bmf_name=bmf_row.name if bmf_row else None,
        ntee_code=bmf_row.ntee_cd if bmf_row else None,
        subsection_code=bmf_row.subsection if bmf_row else None,
    )


# --- blocking ------------------------------------------------------------------------------

_RECIPIENT_SCHEMA = pa.schema(
    [
        ("idx", pa.int64()),
        ("name_normalized", pa.string()),
        ("alias", pa.string()),
        ("first_token", pa.string()),
        ("phonetic", pa.string()),
        ("state", pa.string()),
        ("zip5", pa.string()),
        ("ein_reported", pa.string()),
    ]
)

_CANDIDATE_COLUMNS = (
    "b.ein, b.name, b.sort_name, b.name_normalized, b.sort_name_normalized, "
    "b.city, b.state, b.zip5, b.subsection, b.ntee_cd"
)

_SIM = (
    "GREATEST(jaro_winkler_similarity(r.name_normalized, b.name_normalized), "
    "COALESCE(jaro_winkler_similarity(r.name_normalized, b.sort_name_normalized), 0.0), "
    "COALESCE(jaro_winkler_similarity(r.alias, b.name_normalized), 0.0))"
)

_BLOCKS = (
    "b.name_normalized = r.name_normalized",
    "b.sort_name_normalized = r.name_normalized",
    "b.name_normalized = r.alias",
    "b.state = r.state AND b.first_token = r.first_token",
    "b.zip5 = r.zip5 AND b.first_token = r.first_token",
    "b.state = r.state AND b.phonetic = r.phonetic",
)


def _block_sql() -> str:
    unions = " UNION ".join(
        f"SELECT r.idx, {_CANDIDATE_COLUMNS}, {_SIM} AS sim FROM rcpt r JOIN bmf b ON {on}"
        for on in _BLOCKS
    )
    return f"SELECT * FROM ({unions}) WHERE sim >= {JW_PROBABLE} ORDER BY idx, ein"


def recipients_table(recipients: list[Recipient]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "idx": i,
                "name_normalized": r.name_normalized,
                "alias": r.alias,
                "first_token": first_token(r.name_normalized),
                "phonetic": phonetic_key(r.name_normalized),
                "state": r.state,
                "zip5": r.zip5,
                "ein_reported": valid_ein(r.ein_reported),
            }
            for i, r in enumerate(recipients)
        ],
        schema=_RECIPIENT_SCHEMA,
    )


def _candidate(row: tuple) -> Candidate:
    return Candidate(*row[1:11], sim=float(row[11]))


def block(
    conn: duckdb.DuckDBPyConnection, recipients: list[Recipient]
) -> tuple[dict[int, list[Candidate]], dict[int, Candidate]]:
    """Candidates per recipient index, and the BMF row for each valid reported EIN."""
    if not recipients:
        return {}, {}
    conn.register("rcpt", recipients_table(recipients))
    try:
        by_idx: dict[int, list[Candidate]] = {}
        for row in conn.execute(_block_sql()).fetchall():
            by_idx.setdefault(row[0], []).append(_candidate(row))
        reported: dict[int, Candidate] = {}
        rows = conn.execute(
            f"SELECT r.idx, {_CANDIDATE_COLUMNS}, 1.0 AS sim FROM rcpt r "
            "JOIN bmf b ON b.ein = r.ein_reported WHERE r.ein_reported IS NOT NULL"
        ).fetchall()
        for row in rows:
            reported[row[0]] = _candidate(row)
    finally:
        conn.unregister("rcpt")
    return by_idx, reported


def resolve_all(
    conn: duckdb.DuckDBPyConnection,
    recipients: list[Recipient],
    *,
    corrections=None,
    revoked: set[str] | None = None,
) -> list[Resolution]:
    """Block once, then resolve each tuple; corrections applied last."""
    by_idx, reported = block(conn, recipients)
    corrections = corrections or {}
    revoked = revoked or set()
    out = []
    for i, r in enumerate(recipients):
        ein = valid_ein(r.ein_reported)
        resolution = resolve_one(
            r,
            by_idx.get(i, []),
            reported=reported.get(i),
            revoked=bool(ein and ein in revoked),
        )
        if corrections:
            resolution = apply_correction(r, resolution, corrections, None)
        out.append(resolution)
    return out
