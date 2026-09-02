"""The evaluation harness: the gate must fail on an incomplete set and on a missed target.

The labeled rows here are deliberately few, and one is deliberately wrong, so that both
failure reasons appear. The synthetic BMF is ``test_match``'s.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from test_match import FBC_DALLAS, FEEDING, FLINT_FOR, HARVARD, load_synthetic

from funder_graph.pipeline.resolve import RESOLUTIONS_DDL
from funder_graph.resolve.evaluate import (
    LABELED_COLUMNS,
    MIN_LABELED,
    PRECISION_TARGETS,
    SUGGESTION_COLUMNS,
    evaluate,
    load_labeled,
    sample_for_labeling,
    write_matching_eval,
    write_sample,
)

HEADER = ",".join(LABELED_COLUMNS) + "\n"


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    load_synthetic(c)
    yield c
    c.close()


def _labeled(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "labeled_pairs.csv"
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


ROWS = [
    f"Feeding America,Chicago,IL,60601,,{FEEDING},sd,2026-09-02,BMF row + 990-PF address,",
    f"Harvard University,Cambridge,MA,02138,,{HARVARD},sd,2026-09-02,BMF sort name,",
    f"FEEDNG AMERICA,,IL,,,{FEEDING},sd,2026-09-02,typo on the filing; BMF row,",
    "First Baptist Church,,TX,,,,sd,2026-09-02,two exact BMF rows in TX; not resolvable,",
    # Deliberately labeled to the other Flint foundation: a tier B miss.
    f"Community Foundation of Greater Flint,Flint,MI,48502,,{FLINT_FOR},sd,2026-09-02,verifier's call,",
]


class TestLoad:
    def test_loads_and_parses(self, tmp_path: Path) -> None:
        pairs = load_labeled(_labeled(tmp_path, ROWS))
        assert len(pairs) == 5
        assert pairs[0].expected_ein == FEEDING and pairs[3].expected_ein is None
        assert pairs[1].recipient().name_normalized == "HARVARD UNIV"

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_labeled(tmp_path / "none.csv") == []

    def test_refuses_a_row_without_a_source(self, tmp_path: Path) -> None:
        path = _labeled(tmp_path, [f"Feeding America,Chicago,IL,60601,,{FEEDING},sd,2026-09-02,,"])
        with pytest.raises(ValueError, match="source is required"):
            load_labeled(path)

    def test_refuses_a_malformed_expected_ein(self, tmp_path: Path) -> None:
        path = _labeled(tmp_path, ["Feeding America,Chicago,IL,60601,,12-34,sd,2026-09-02,src,"])
        with pytest.raises(ValueError, match="not nine digits"):
            load_labeled(path)

    def test_refuses_missing_columns(self, tmp_path: Path) -> None:
        path = tmp_path / "labeled_pairs.csv"
        path.write_text("recipient_name_raw,expected_ein\nX,\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing columns"):
            load_labeled(path)


class TestEvaluate:
    def test_per_tier_precision_recall_and_abstention(self, conn, tmp_path: Path) -> None:
        ev = evaluate(conn, load_labeled(_labeled(tmp_path, ROWS)))
        assert ev.n == 5
        b = ev.tiers["B"]
        assert (b.resolved, b.correct) == (
            3,
            2,
        )  # Feeding, Harvard correct; Flint labeled the other way
        assert ev.tiers["D"].precision == 1.0  # the typo, in-state, single candidate
        assert ev.recall == pytest.approx(3 / 4)
        assert ev.abstention == 1.0
        assert [p.name_raw for p, _ in ev.misses] == ["Community Foundation of Greater Flint"]

    def test_gate_fails_on_an_incomplete_set_and_a_missed_target(
        self, conn, tmp_path: Path
    ) -> None:
        failures = evaluate(conn, load_labeled(_labeled(tmp_path, ROWS))).gate()
        assert len(failures) == 2
        assert f"{MIN_LABELED:,}" in failures[0]
        assert failures[1].startswith("tier B precision 66.7% is below the 99% target")

    def test_empty_set_evaluates_to_nothing_and_fails_only_for_size(self, conn) -> None:
        ev = evaluate(conn, [])
        assert ev.recall is None and ev.abstention is None
        assert len(ev.gate()) == 1

    def test_targets_are_the_spec_s(self) -> None:
        assert PRECISION_TARGETS == {"A": 1.00, "B": 0.99, "C": 0.95, "D": 0.80}
        assert MIN_LABELED == 1000


class TestReport:
    def test_markdown_report(self, conn, tmp_path: Path) -> None:
        labeled = _labeled(tmp_path, ROWS)
        ev = evaluate(conn, load_labeled(labeled))
        out = tmp_path / "reports" / "matching-eval.md"
        write_matching_eval(
            ev,
            out,
            bmf_vintage="2026-08",
            now=datetime(2026, 9, 2, tzinfo=UTC),
            labeled_path=labeled,
        )
        text = out.read_text(encoding="utf-8")
        assert "| B | 3 | 2 | 66.7% | 99% | **BELOW** |" in text
        assert "| A | 0 | 0 | n/a | 100% | no rows |" in text
        assert "## Gate: FAIL" in text
        assert "Community Foundation of Greater Flint" in text and FBC_DALLAS not in text


class TestSampling:
    def _resolutions(self, conn) -> None:
        conn.execute(RESOLUTIONS_DDL)
        rows = []
        for i in range(30):
            tier = "ABCDU"[i % 5]
            rows.append(
                (
                    f"ORG {i}",
                    f"Org {i}",
                    "TOWN",
                    "CA",
                    "90001",
                    None,
                    "organization",
                    None if tier == "U" else f"{100000000 + i}",
                    "unresolved" if tier == "U" else "bmf_strong",
                    None,
                    tier,
                    "m",
                    None,
                    None,
                    None,
                    "2026-08",
                    datetime(2026, 9, 2),
                )
            )
        # An individual must never be sampled for labeling.
        rows.append(
            (
                "A PERSON",
                "A Person",
                None,
                "CA",
                None,
                None,
                "individual",
                None,
                "unresolved",
                None,
                "U",
                "recipient_type_individual",
                None,
                None,
                None,
                "2026-08",
                datetime(2026, 9, 2),
            )
        )
        conn.executemany("INSERT INTO resolutions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def test_stratified_deterministic_and_organizations_only(self, conn, tmp_path: Path) -> None:
        self._resolutions(conn)
        first = sample_for_labeling(conn, 10, seed="s1")
        again = sample_for_labeling(conn, 10, seed="s1")
        other = sample_for_labeling(conn, 10, seed="s2")
        assert len(first) == 10 and first == again
        assert sorted(r["matcher_tier"] for r in first) == list("AABBCCDDUU")
        assert all(r["recipient_name_raw"] != "A Person" for r in first)
        assert first != other
        assert all(r["expected_ein"] == "" and r["verified_by"] == "" for r in first)

        out = tmp_path / "labeling-sample.csv"
        write_sample(first, out)
        header = out.read_text(encoding="utf-8").splitlines()[0]
        assert header == ",".join([*LABELED_COLUMNS, *SUGGESTION_COLUMNS])
