"""Run a literature search. Edit the CONFIG block below, then run this file.

    python run_search.py

All seven stages. Retrieval, dedup, snowballing, validation and reporting run to
completion here. The two stages that need a language model -- screening and evidence
extraction -- are handled by handoff: this file writes task files, an agent answers them
into `verdicts.jsonl` and `rows.jsonl`, and a second run consumes the answers. No model is
ever called from this file.

So a full search is:

    python run_search.py                 # retrieves, validates, writes screening batches
    # ... lit-screener answers runs/<name>/screen/verdicts.jsonl
    python run_search.py                 # applies verdicts, writes extraction tasks
    # ... lit-extractor answers runs/<name>/extract/rows.jsonl
    python run_search.py                 # writes evidence.csv and the final refs.bib

Re-running is cheap: every index response is cached on disk.
"""

from __future__ import annotations

from pathlib import Path

from bibcheck.verify import IndexClient
from litsearch import export, extract, report, retrieve, screen, snowball
from litsearch.config import OUT_DIR_ENV, SearchConfig, run_dir, warn_if_inside_repo
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
MAX_ROUNDS = 3
SEEDS_PER_ROUND = 8
REFS_PER_SEED = 4

# Papers you already know must be found. Retrieval that misses one of these is broken, and
# the run says so. Leave empty to skip the check.
KNOWN_ITEMS: list[str] = []

# Stage 4 needs criteria a screener can apply from a title and abstract alone.
INCLUSION_CRITERIA = (
    "Reports a measured T1, T2*, or T2 echo coherence time for a superconducting qubit "
    "or superconducting resonator/cavity, with an actual number."
)
EXCLUSION_CRITERIA = (
    "Review articles without new measurements; theory-only papers with no measured device; "
    "non-superconducting platforms (trapped ion, spin, photonic, NV centre)."
)

# Stage 6 columns. Every one of these must be quotable from the paper or it is recorded null.
EXTRACTION_SCHEMA = (
    "qubit_type",
    "material",
    "substrate",
    "T1_us",
    "T2_star_us",
    "T2_echo_us",
    "temperature_mK",
)

MAILTO = ""  # your address puts Crossref/OpenAlex requests in the polite pool
OFFLINE = False  # True replays the on-disk cache and opens no connection

# Outputs land OUTSIDE the repository by default -- see litsearch/config.py. Override the
# location with the LITSEARCH_OUT_DIR environment variable, not by editing this line.
RUN_NAME = "t1_t2_superconducting_qubits"
OUT_DIR = run_dir(RUN_NAME)
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
    warning = warn_if_inside_repo(cfg.out_dir)
    if warning:
        print(f"  [WARN] {warning}")
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)

    print(f"question: {cfg.question}")
    print(f"sources : {', '.join(cfg.sources)}")
    print(f"output  : {cfg.out_dir}  (override with ${OUT_DIR_ENV})")

    print("\n[1/7] retrieve")
    corpus = retrieve.run(fetcher, cfg)
    fetcher.save_cache()  # flush before each long stage, so an interrupt costs nothing

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
        print(f"  [{mark}] {row['wanted'][:60]} (similarity {row['similarity']})")
    if not known:
        print("  (none configured)")

    print("\n[5/7] screen (stage 4)")
    # Screening needs a model, which this file never calls. It writes batches and reads
    # verdicts back, so the stage is resumable: run once to emit the batches, have the
    # lit-screener subagent answer them, then run again to consume the answers.
    screen_dir = cfg.out_dir / "screen"
    batches = screen.prepare_batches(corpus, INCLUSION_CRITERIA, EXCLUSION_CRITERIA, screen_dir)
    screen_counts = screen.apply_verdicts(corpus, screen.load_verdicts(screen_dir / "verdicts.jsonl"))
    if screen_counts["unscreened"] == len(corpus):
        print(f"  {len(batches)} batches written to {screen_dir}")
        print(f"  no verdicts yet -- answer them into {screen_dir / 'verdicts.jsonl'}, then re-run")
    else:
        print(f"  include {screen_counts['include']}, exclude {screen_counts['exclude']}, "
              f"unsure {screen_counts['unsure']}, unscreened {screen_counts['unscreened']}")
    screen.write_review_queue(cfg.out_dir / "needs_review.md", screen.needs_review(corpus))

    print("\n[6/7] extract (stage 6)")
    # Only works that BOTH passed the gate and were screened in are worth reading.
    included = [work for work in screen.included(corpus) if work.validation == "verified"]
    if not included:
        included = passed if screen_counts["unscreened"] == len(corpus) else []
        if included:
            print("  no screening verdicts yet; preparing tasks for every validated work")
    extract_dir = cfg.out_dir / "extract"
    tasks = extract.prepare_tasks(included, extract_dir, schema=EXTRACTION_SCHEMA)
    rows = extract.load_rows(extract_dir / "rows.jsonl")
    accepted, complaints = extract.validate_rows(rows, schema=EXTRACTION_SCHEMA)
    print(f"  {len(tasks)} extraction tasks in {extract_dir}")
    if rows:
        print(f"  {len(accepted)}/{len(rows)} rows accepted")
        for complaint in complaints[:5]:
            print(f"    [reject] {complaint}")
    else:
        print(f"  no rows yet -- answer the tasks into {extract_dir / 'rows.jsonl'}, then re-run")

    print("\n[7/7] write outputs")
    corpus.write_jsonl(cfg.out_dir / "corpus.jsonl")
    report.write_shortlist(cfg.out_dir / "shortlist.md", passed)
    held = report.write_quarantine(cfg.out_dir / "quarantine.md", verdicts)
    report.write_run_log(cfg.out_dir / "run.json", cfg, corpus, rounds, verdicts, known)

    # Only validated works go into the bibliography. Quarantined ones never appear.
    bib_works = included or passed
    entry_count, findings = export.write_bibtex(cfg.out_dir / "refs.bib", bib_works)
    errors = [f for f in findings if f.level == "error"]
    kept = export.write_evidence_csv(cfg.out_dir / "evidence.csv", accepted)

    print(f"  corpus {len(corpus)} works -> {cfg.out_dir}")
    print(f"  {len(passed)} validated, {held} quarantined")
    print(f"  refs.bib: {entry_count} entries, {len(errors)} errors, {len(findings) - len(errors)} other findings")
    print(f"  evidence.csv: {kept} rows with a source quote")
    for finding in errors[:5]:
        print(f"    [error] {finding.key}: {finding.message}")

    missed = [row for row in known if not row["found"]]
    if missed:
        print(f"\nWARNING: {len(missed)} known-item(s) not found -- retrieval is incomplete")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
