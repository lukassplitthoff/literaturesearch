"""Run a literature search. Edit the CONFIG block below, then run this file.

    python run_search.py

This is the deterministic spine only -- stages 1, 2, 3, 5 and the part of 7 that needs no
model: retrieve, merge, snowball, validate, report. Screening and evidence extraction
(stages 4 and 6) are the Claude-facing half and are not wired up yet.
"""

from __future__ import annotations

from pathlib import Path

from bibcheck.verify import IndexClient
from litsearch import export, report, retrieve, snowball
from litsearch.config import SearchConfig
from litsearch.gate import validate_all
from litsearch.sources.base import Fetcher

# ---------------------------------------------------------------------------------------
# CONFIG -- edit these
# ---------------------------------------------------------------------------------------
QUESTION = "What are the longest reported T1 and T2 coherence times in superconducting qubits?"

QUERIES = [
    "superconducting qubit coherence time T1 T2 record",
    "tantalum transmon qubit long coherence",
    "fluxonium millisecond coherence time",
    "improving superconducting qubit relaxation time materials",
]

YEAR_FROM = 1995
YEAR_TO = None
SOURCES = ("openalex", "semanticscholar", "inspire")  # "ads" is deferred, needs a token
PER_QUERY_LIMIT = 50
# Snowballing is throttled to 1 request per second, so these two numbers set the wall
# clock: roughly SEEDS_PER_ROUND * (1 + REFS_PER_SEED) seconds per round. Start small.
MAX_ROUNDS = 1
SEEDS_PER_ROUND = 8
REFS_PER_SEED = 4

# Papers you already know must be found. Retrieval that misses one of these is broken, and
# the run says so. Leave empty to skip the check.
KNOWN_ITEMS: list[str] = []

MAILTO = ""  # your address puts Crossref/OpenAlex requests in the polite pool
OFFLINE = False  # True replays the on-disk cache and opens no connection
OUT_DIR = Path("runs/t1_t2_superconducting_qubits")
# ---------------------------------------------------------------------------------------


def main() -> int:
    cfg = SearchConfig(
        question=QUESTION,
        queries=QUERIES,
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
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)

    print(f"question: {cfg.question}")
    print(f"sources : {', '.join(cfg.sources)}")

    print("\n[1/5] retrieve")
    corpus = retrieve.run(fetcher, cfg)

    print("\n[2/5] snowball")
    rounds = snowball.expand(fetcher, corpus, cfg)

    print("\n[3/5] validate (the gate)")
    client = IndexClient(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)
    passed, verdicts = validate_all(corpus.works, client)
    client.save_cache()
    fetcher.save_cache()
    print(f"  {len(passed)} verified, {len(verdicts) - len(passed)} quarantined")

    print("\n[4/5] known-item check")
    known = report.known_item_results(corpus, cfg.known_items)
    for row in known:
        mark = "OK  " if row["found"] else "MISS"
        print(f"  [{mark}] {row['wanted'][:60]} (similarity {row['similarity']})")
    if not known:
        print("  (none configured)")

    print("\n[5/5] write outputs")
    corpus.write_jsonl(cfg.out_dir / "corpus.jsonl")
    report.write_shortlist(cfg.out_dir / "shortlist.md", passed)
    held = report.write_quarantine(cfg.out_dir / "quarantine.md", verdicts)
    report.write_run_log(cfg.out_dir / "run.json", cfg, corpus, rounds, verdicts, known)

    # Only validated works go into the bibliography. Quarantined ones never appear.
    entry_count, findings = export.write_bibtex(cfg.out_dir / "refs.bib", passed)
    errors = [f for f in findings if f.level == "error"]
    print(f"  corpus {len(corpus)} works -> {cfg.out_dir}")
    print(f"  {len(passed)} validated, {held} quarantined")
    print(f"  refs.bib: {entry_count} entries, {len(errors)} errors, {len(findings) - len(errors)} other findings")
    for finding in errors[:5]:
        print(f"    [error] {finding.key}: {finding.message}")

    missed = [row for row in known if not row["found"]]
    if missed:
        print(f"\nWARNING: {len(missed)} known-item(s) not found -- retrieval is incomplete")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
