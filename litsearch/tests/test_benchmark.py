"""The benchmark must run, and must fail when the deterministic layers regress."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

benchmark = pytest.importorskip("benchmark.benchmark")


def test_the_benchmark_matches_its_baseline():
    """A change to dedup, triage, the topical guard or the exporter shows up here."""
    baseline = json.loads(benchmark.BASELINE.read_text(encoding="utf-8"))
    problems = benchmark.compare(benchmark.measure(), baseline)
    assert problems == [], (
        "the deterministic layers changed against benchmark/baseline.json:\n  "
        + "\n  ".join(problems)
        + "\nIf intended: python benchmark/benchmark.py --update"
    )


def test_no_screener_include_is_discarded_by_a_rule():
    """The trade the triage rules must never lose: a paper a screener read and kept must
    not later be thrown away for free by a keyword rule."""
    metrics = benchmark.measure()
    assert metrics["includes_lost_to_triage"] == []


def test_the_known_duplicates_still_merge():
    metrics = benchmark.measure()
    assert metrics["duplicates_missed"] == []
    assert metrics["duplicates_merged"] == len(benchmark.DUPLICATE_CASES)


def test_distinct_papers_are_not_merged():
    assert benchmark.measure()["distinct_wrongly_merged"] == []


def test_the_generated_bibliography_is_clean():
    metrics = benchmark.measure()
    assert metrics["bib_errors"] == 0
    assert metrics["bib_non_ascii"] == []


def test_the_benchmark_detects_an_over_tightened_rule(monkeypatch):
    """Proof the benchmark is not merely agreeing with itself: narrowing the required
    terms to one token looks like a 90% token saving and destroys dozens of includes."""
    monkeypatch.setattr(benchmark, "SCREEN_REQUIRED", ("transmon",))
    metrics = benchmark.measure()
    assert len(metrics["includes_lost_to_triage"]) > 10
    baseline = json.loads(benchmark.BASELINE.read_text(encoding="utf-8"))
    assert benchmark.compare(metrics, baseline), "an over-tightened rule must be reported"


def test_gold_recall_is_measured_and_reported():
    """The gold set was chosen by a domain expert without seeing this pipeline's output,
    which is what separates a measurement from the system agreeing with itself."""
    metrics = benchmark.measure()
    assert metrics["gold_total"] == 15
    assert 0 <= metrics["gold_recall_pct"] <= 100
    # Every gold paper is either found or named in the miss list -- no silent loss.
    assert metrics["gold_found"] + len(metrics["gold_missed"]) == metrics["gold_total"]


def test_the_fixture_retains_every_gold_paper_the_corpus_had():
    """The fixture is a sample of a larger corpus. If sampling drops gold papers, the
    recall number becomes an artifact of fixture construction rather than of retrieval --
    which it briefly was, reporting 53% against a real 67%."""
    metrics = benchmark.measure()
    assert metrics["gold_found"] == 15, (
        "the frozen corpus contained all 15 gold papers; a different number means "
        "the fixture was rebuilt without preserving them"
    )
