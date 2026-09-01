"""A regression benchmark for the deterministic layers.

    python benchmark/benchmark.py            # print the metrics
    python benchmark/benchmark.py --update   # rewrite the baseline after an intended change

## What this does and does not measure

It measures the parts of the pipeline that are pure functions of a fixed corpus: dedup,
the triage rules, the topical guard, and the bibliography exporter. It opens no socket and
calls no model, so it runs in about a second and its numbers move only when the code moves.

It does **not** measure retrieval recall or screening accuracy, and it cannot. The labels
in `fixtures/verdicts.jsonl` were produced by this pipeline's own screening pass, so
scoring the pipeline against them is circular -- it can only ever agree with itself. What
the labels *are* good for is the direction the triage rules must not drift: a work a
screener read and included must not later be thrown away for free by a keyword rule.

Answering "did we find everything we should have?" needs a gold set -- papers a domain
expert lists BEFORE seeing any run. That does not exist yet and is deliberately out of
scope here; see README.md.

## Why a frozen corpus rather than a live search

Two runs a month apart are not comparable: indexes gain papers, citation counts drift and
reorder the snowball seeds, and screening involves a model. During one afternoon of
development this corpus went 700 -> 698 -> 697 from code changes alone. A benchmark whose
baseline moves on its own measures nothing, so the corpus is a snapshot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from litsearch import export, relevance, screen
from litsearch.corpus import Corpus
from litsearch.sources.base import Work

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
BASELINE = HERE / "baseline.json"

# The same rules the worked example uses. Kept here rather than imported so the benchmark
# measures a fixed configuration even if the example is later retuned.
SCREEN_FORBIDDEN = (
    "optomechanic", "opto-mechanic", "magnon", "nanophotonic", "photonic crystal",
    "silicon photonic", "optical fiber", "optical fibre", "optical parametric oscillator",
    "telecom wavelength", "cold atom", "atomic ensemble", "bose-einstein",
    "nitrogen-vacancy", "nv centre", "nv center", "trapped ion", "trapped-ion",
    "molecular spin", "single-ion magnet", "vanadyl",
)
SCREEN_REQUIRED = (
    "superconduct", "transmon", "fluxonium", "josephson", "snail",
    "circuit qed", "cqed", "microwave cavity", "microwave resonator",
    "coaxial cavity", "3d cavity", "cooper pair", "bosonic mode", "bosonic qubit",
)
QUERIES = [
    "parametric beam splitter interaction bosonic cavity microwave",
    "two-mode squeezing gate superconducting microwave resonator",
    "frequency conversion parametric coupler bosonic modes",
    "SNAP gate selective number-dependent arbitrary phase cavity",
    "echoed conditional displacement gate bosonic qubit",
    "SNAIL ATS parametric coupler three-wave mixing cavity",
    "controlled-SWAP exponential-SWAP bosonic mode gate",
]

# Duplicate pairs seen in the wild, each of which defeated an earlier version of dedup.
DUPLICATE_CASES = [
    (
        "arXiv DOI vs publisher DOI",
        Work(title="Disentangling Losses in Tantalum Superconducting Circuits",
             doi="10.1103/physrevx.13.041005", year="2023", authors=["Chapman, Benjamin J."]),
        Work(title="Disentangling Losses in Tantalum Superconducting Circuits",
             doi="10.48550/arxiv.2301.07848", arxiv_id="2301.07848", year="2023",
             authors=["Benjamin J. Chapman"]),
    ),
    (
        "two publisher DOIs, author name order differs",
        Work(title="High-fidelity parametric beamsplitting with a parity-protected converter",
             doi="10.1038/s41467-023-41104-0", year="2023", authors=["Yao Lu"]),
        Work(title="High-fidelity parametric beamsplitting with a parity-protected converter",
             doi="10.1038/s41467-023-41822-5", year="2023", authors=["Lu, Yao"]),
    ),
]

# Pairs that must STAY separate. A dedup change that merges these has gone too far.
DISTINCT_CASES = [
    (
        "Part I vs Part II",
        Work(title="Coherence in transmon qubits Part I", doi="10.1/p1", year="2023",
             authors=["Ann Author"]),
        Work(title="Coherence in transmon qubits Part II", doi="10.1/p2", year="2023",
             authors=["Ann Author"]),
    ),
]


def load_corpus() -> Corpus:
    return Corpus.read_jsonl(FIXTURES / "corpus.jsonl")


def measure() -> dict:
    corpus = load_corpus()
    verdicts = screen.load_verdicts(FIXTURES / "verdicts.jsonl")
    counts = screen.apply_verdicts(corpus, verdicts)
    metrics: dict = {"corpus_size": len(corpus)}

    # --- dedup ------------------------------------------------------------------
    # Re-adding a corpus to itself must merge nothing: the fixture is already deduped, so
    # any new merge means dedup has become more aggressive than it was.
    again = Corpus()
    added = again.add_all([Work.from_dict(w.as_dict()) for w in corpus.works])
    metrics["dedup_idempotent"] = added == len(corpus)

    merged, split = [], []
    for label, left, right in DUPLICATE_CASES:
        probe = Corpus()
        probe.add(left)
        probe.add(right)
        (merged if len(probe) == 1 else split).append(label)
    metrics["duplicates_merged"] = len(merged)
    metrics["duplicates_missed"] = split

    kept_apart = []
    for label, left, right in DISTINCT_CASES:
        probe = Corpus()
        probe.add(left)
        probe.add(right)
        if len(probe) != 2:
            kept_apart.append(label)
    metrics["distinct_wrongly_merged"] = kept_apart

    # --- topical guard ----------------------------------------------------------
    terms = relevance.terms_from_queries(QUERIES)
    on_topic, dropped = relevance.filter_on_topic(corpus.works, terms)
    metrics["topical_kept"] = len(on_topic)
    metrics["topical_dropped"] = dropped

    # --- triage: the token/recall trade -----------------------------------------
    fresh = load_corpus()
    screen.apply_verdicts(fresh, screen.load_verdicts(FIXTURES / "verdicts.jsonl"))
    included_titles = {w.title for w in fresh.works if w.screen == "include"}
    to_model, by_rule = relevance.triage_all(load_corpus().works, SCREEN_REQUIRED, SCREEN_FORBIDDEN)
    metrics["triage_to_model"] = len(to_model)
    metrics["triage_by_rule"] = len(by_rule)
    metrics["triage_saving_pct"] = round(100 * len(by_rule) / max(len(corpus), 1), 1)

    # The number that matters: a work a screener read and INCLUDED must never be discarded
    # for free by a keyword rule. Non-zero here means the rules were tightened too far.
    lost = sorted(w.title for w in by_rule if w.title in included_titles)
    metrics["includes_lost_to_triage"] = lost

    # Of the works triage passes, how many survive screening. Low means the model is being
    # asked about a lot of papers it will reject -- wasted tokens, not wasted recall.
    metrics["triage_yield_pct"] = round(
        100 * len([w for w in to_model if w.title in included_titles]) / max(len(to_model), 1), 1
    )

    # --- gate and export --------------------------------------------------------
    metrics["gate_verified"] = sum(1 for w in corpus.works if w.validation == "verified")
    metrics["gate_quarantined"] = sum(1 for w in corpus.works if w.validation and w.validation != "verified")
    metrics["screened_include"] = counts["include"]
    metrics["screened_unsure"] = counts["unsure"]

    citable = [w for w in corpus.works if w.screen == "include" and w.validation == "verified"]
    text = export.build_bibtex(citable)
    from bibcheck.parser import loads
    from bibcheck.rules import check_database
    reports, file_findings = check_database(loads(text))
    findings = list(file_findings)
    for report in reports:
        findings.extend(report.findings)
    metrics["bib_entries"] = len(loads(text).entries)
    metrics["bib_errors"] = sum(1 for f in findings if f.level == "error")
    metrics["bib_non_ascii"] = sorted({c for c in text if ord(c) > 127})
    return metrics


# Metrics where a small drift is meaningless, and how much of it to tolerate.
TOLERANCE = {
    "triage_yield_pct": 2.0,
    "triage_saving_pct": 2.0,
}


def compare(current: dict, baseline: dict) -> list[str]:
    """Differences worth a human's attention. Empty means no regression."""
    problems = []
    for key, was in baseline.items():
        now = current.get(key)
        if isinstance(was, (int, float)) and isinstance(now, (int, float)) and not isinstance(was, bool):
            allowed = TOLERANCE.get(key, 0)
            if abs(now - was) > allowed:
                problems.append(f"{key}: {was} -> {now}")
        elif now != was:
            problems.append(f"{key}: {was!r} -> {now!r}")
    return problems


def main(argv: list[str]) -> int:
    current = measure()
    width = max(len(k) for k in current)
    print("deterministic benchmark\n")
    for key, value in current.items():
        print(f"  {key:<{width}}  {value}")

    if "--update" in argv:
        BASELINE.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nbaseline written to {BASELINE.name}")
        return 0

    if not BASELINE.exists():
        print(f"\nno baseline yet; run with --update to create {BASELINE.name}")
        return 0

    problems = compare(current, json.loads(BASELINE.read_text(encoding="utf-8")))
    if problems:
        print("\nCHANGED against the baseline:")
        for line in problems:
            print(f"  {line}")
        print("\nIf the change is intended, re-run with --update.")
        return 1
    print("\nno change against the baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
