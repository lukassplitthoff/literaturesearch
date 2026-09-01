"""Which other parametric gates have been studied in bosonic cavities?

Seeded from Chapman et al., "High-On-Off-Ratio Beam-Splitter Interaction for Gates on
Bosonically Encoded Qubits", PRX Quantum 4, 020355 (2023) -- a SNAIL-based programmable
beam splitter between two bosonic modes.

The question is explicitly "what OTHER gates", which is a related-work search rather than
a topic search: the reference paper defines the neighbourhood, and its citation graph is
a better guide to that neighbourhood than any set of keywords. The queries below are there
to catch parametric gates that neither cite nor are cited by the seed.

Run from the repository root:

    python examples/parametric_gates_bosonic_cavities/search.py
"""

from __future__ import annotations

from pathlib import Path

from bibcheck.verify import IndexClient
from litsearch import export, extract, relevance, report, retrieve, screen, snowball
from litsearch.config import SearchConfig, run_dir, warn_if_inside_repo
from litsearch.gate import validate_all
from litsearch.sources.base import Fetcher

# ---------------------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------------------
QUESTION = "Which other parametric gates have been studied in bosonic cavities?"

# The reference paper. Its references and citers are the primary evidence.
SEED_DOIS = ("10.1103/PRXQuantum.4.020355",)

# Deliberately spanning the different parametric interactions, because each community
# names its gate differently and a single phrasing would find only one of them.
QUERIES = [
    "parametric beam splitter interaction bosonic cavity microwave",
    "two-mode squeezing gate superconducting microwave resonator",
    "frequency conversion parametric coupler bosonic modes",
    "SNAP gate selective number-dependent arbitrary phase cavity",
    "echoed conditional displacement gate bosonic qubit",
    "SNAIL ATS parametric coupler three-wave mixing cavity",
    "controlled-SWAP exponential-SWAP bosonic mode gate",
]

YEAR_FROM = 2010
YEAR_TO = None
SOURCES = ("openalex", "inspire")  # semanticscholar throttles keyless clients to nothing
PER_QUERY_LIMIT = 50
MAX_ROUNDS = 2
SEEDS_PER_ROUND = 10
REFS_PER_SEED = 12  # the seed has 71 references and they are the point of the exercise

# Papers that must appear if retrieval is working. These are NOT supplied from memory:
# each was found by an earlier run of this search, passed the validation gate against
# Crossref, and was screened in. That makes this a regression check -- it catches a future
# change that breaks retrieval -- rather than an independent test of recall, which would
# need titles chosen by someone who knows the field before the search is run.
KNOWN_ITEMS: list[str] = [
    "High-On-Off-Ratio Beam-Splitter Interaction for Gates on Bosonically Encoded Qubits",
    "Programmable Interference between Two Microwave Quantum Memories",
    "Observation of Two-Mode Squeezing in the Microwave Frequency Domain",
    "Efficient cavity control with SNAP gates",
    "Stabilization and operation of a Kerr-cat qubit",
]

INCLUSION_CRITERIA = (
    "Reports a parametric gate or parametric interaction between bosonic modes -- a "
    "microwave cavity, resonator or bosonic-encoded qubit. Beam splitter, two-mode "
    "squeezing, frequency conversion, SNAP, conditional displacement, controlled-SWAP "
    "and similar count. Both experiment and gate-design theory are in scope. "
    "The interaction must be DRIVE-ACTIVATED: a pump or flux modulation turns it on. An "
    "always-on static coupling (bare cross-Kerr, bare dispersive shift) does not qualify "
    "on its own, but engineering such a term with a drive does. A parametric drive between "
    "two TWO-LEVEL qubits does not qualify either -- at least one side must be a bosonic "
    "mode."
)
EXCLUSION_CRITERIA = (
    "Gates between two-level qubits only, with no bosonic mode; non-superconducting "
    "platforms (trapped ion, optical photonics, spin, NV centre); papers about "
    "amplifiers or sensing with no gate; reviews with no new gate."
)

# Scope: SUPERCONDUCTING MICROWAVE bosonic cavities only.
#
# An optical cavity is also a bosonic cavity, and optomechanics is genuinely parametric,
# so the first run legitimately returned a large optical/optomechanical set -- 42% of the
# corpus, against 34% superconducting. That is a scope decision rather than drift, and the
# scope chosen here is the one the seed paper and this group work in.
SCREEN_FORBIDDEN = (
    # other platforms that share the entire parametric vocabulary
    "optomechanic", "opto-mechanic", "magnon", "nanophotonic", "photonic crystal",
    "silicon photonic", "optical fiber", "optical fibre", "optical parametric oscillator",
    "telecom wavelength", "cold atom", "atomic ensemble", "bose-einstein",
    "nitrogen-vacancy", "nv centre", "nv center", "trapped ion", "trapped-ion",
    "molecular spin", "single-ion magnet", "vanadyl",
)
# Superconducting-circuit vocabulary a qualifying paper is unlikely to avoid. Narrower
# than the previous list, which accepted a bare "cavity" and so admitted all of optics.
SCREEN_REQUIRED = (
    "superconduct", "transmon", "fluxonium", "josephson", "snail",
    "circuit qed", "cqed", "microwave cavity", "microwave resonator",
    "coaxial cavity", "3d cavity", "cooper pair", "bosonic mode", "bosonic qubit",
)

EXTRACTION_SCHEMA = (
    "gate_type",
    "interaction_order",
    "coupler",
    "modes",
    "fidelity_pct",
    "gate_time_ns",
    "on_off_ratio",
    "platform",
)

MAILTO = ""
OFFLINE = False
RUN_NAME = "parametric_gates_bosonic_cavities"
OUT_DIR = run_dir(RUN_NAME)
# ---------------------------------------------------------------------------------------


def main() -> int:
    cfg = SearchConfig(
        question=QUESTION,
        queries=QUERIES,
        seed_dois=SEED_DOIS,
        year_from=YEAR_FROM,
        year_to=YEAR_TO,
        sources=SOURCES,
        per_query_limit=PER_QUERY_LIMIT,
        max_rounds=MAX_ROUNDS,
        seeds_per_round=SEEDS_PER_ROUND,
        refs_per_seed=REFS_PER_SEED,
        known_items=KNOWN_ITEMS,
        mailto=MAILTO,
        out_dir=OUT_DIR,
        offline=OFFLINE,
    )
    warning = warn_if_inside_repo(cfg.out_dir)
    if warning:
        print(f"  [WARN] {warning}")
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)

    print(f"question: {cfg.question}")
    print(f"output  : {cfg.out_dir}\n")

    print("[1/7] retrieve")
    corpus = retrieve.run(fetcher, cfg)
    fetcher.save_cache()

    print("\n[2/7] snowball")
    rounds = snowball.expand(fetcher, corpus, cfg)
    fetcher.save_cache()

    print("\n[3/7] validate (the gate)")
    client = IndexClient(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)
    passed, verdicts = validate_all(corpus.works, client)
    fetcher.save_cache()
    print(f"  {len(passed)} verified, {len(verdicts) - len(passed)} quarantined")

    print("\n[4/7] known-item check")
    known = report.known_item_results(corpus, cfg.known_items)
    for row in known:
        mark = "OK  " if row["found"] else "MISS"
        print(f"  [{mark}] {row['wanted'][:62]} (similarity {row['similarity']})")
    if not known:
        print("  (none configured)")

    print("\n[5/7] screen")
    screen_dir = cfg.out_dir / "screen"
    to_model, rule_excluded = relevance.triage_all(corpus.works, SCREEN_REQUIRED, SCREEN_FORBIDDEN)
    print(f"  triage: {len(rule_excluded)} excluded by rule, {len(to_model)} need the model")
    batches = screen.prepare_batches(corpus, INCLUSION_CRITERIA, EXCLUSION_CRITERIA, screen_dir, works=to_model)
    screen_counts = screen.apply_verdicts(corpus, screen.load_verdicts(screen_dir / "verdicts.jsonl"))
    batch_bytes = sum(p.stat().st_size for p in batches)
    print(f"  {len(batches)} batches, {batch_bytes / 1024:.0f} KB (~{batch_bytes // 4000} k tokens)")
    screen.write_review_queue(cfg.out_dir / "needs_review.md", screen.needs_review(corpus))

    print("\n[6/7] extract")
    included = [w for w in screen.included(corpus) if w.validation == "verified"]
    if not included:
        included = passed if screen_counts["unscreened"] + screen_counts["by_rule"] == len(corpus) else []
    tasks = extract.prepare_tasks(included, cfg.out_dir / "extract", schema=EXTRACTION_SCHEMA)
    rows = extract.load_rows(cfg.out_dir / "extract" / "rows.jsonl")
    accepted, complaints = extract.validate_rows(rows, schema=EXTRACTION_SCHEMA)
    print(f"  {len(tasks)} extraction tasks, {len(accepted)}/{len(rows)} rows accepted")

    print("\n[7/7] write outputs")
    corpus.write_jsonl(cfg.out_dir / "corpus.jsonl")
    report.write_shortlist(cfg.out_dir / "shortlist.md", passed)
    held = report.write_quarantine(cfg.out_dir / "quarantine.md", verdicts)
    report.write_run_log(cfg.out_dir / "run.json", cfg, corpus, rounds, verdicts, known)
    entry_count, findings, uncitable = export.write_bibtex(cfg.out_dir / "refs.bib", included or passed)
    errors = [f for f in findings if f.level == "error"]
    export.write_evidence_csv(cfg.out_dir / "evidence.csv", accepted,
                              columns=export.columns_for(EXTRACTION_SCHEMA))
    print(f"  corpus {len(corpus)} | validated {len(passed)} | quarantined {held}")
    print(f"  refs.bib: {entry_count} entries, {len(errors)} errors")
    for finding in errors[:5]:
        print(f"    [error] {finding.key}: {finding.message}")

    missed = [row for row in known if not row["found"]]
    if missed:
        print(f"\nWARNING: {len(missed)} known-item(s) not found -- retrieval is incomplete")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
