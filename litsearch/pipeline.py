"""The seven-stage run, so a search is a configuration rather than a copied script.

Every search differs only in its question, its queries and its criteria. Keeping the stage
sequence here means a fix reaches every search at once, and an example is short enough to
read in one screen.

Stages 4 and 6 need a language model, and nothing in this module calls one. They hand off
through files: the run writes tasks, an agent answers them, and the next run reads the
answers. So a full search is three invocations:

    python <search>.py     # retrieve, validate, write screening batches
    #   lit-screener answers screen/verdicts.jsonl
    python <search>.py     # apply verdicts, write extraction tasks
    #   lit-extractor answers extract/rows.jsonl
    python <search>.py     # write evidence.csv and the final refs.bib

Re-running is cheap: every index response is cached on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bibcheck.verify import IndexClient
from litsearch import export, extract, relevance, report, retrieve, screen, snowball
from litsearch.config import OUT_DIR_ENV, SearchConfig, run_dir, warn_if_inside_repo
from litsearch.gate import validate_all
from litsearch.sources.base import Fetcher


@dataclass
class SearchSpec:
    """Everything that distinguishes one search from another.

    The defaults are the ones a first run should have: two snowball rounds, a handful of
    seeds, and the keyless sources. They are deliberately small -- expansion is throttled
    at one request per second, so the round settings, not the query count, set the wall
    clock.
    """

    name: str
    question: str
    queries: list[str]

    # Known papers to expand from. Naming a paper that is definitively on topic sidesteps
    # the vocabulary problem that keyword queries have.
    seed_dois: tuple[str, ...] = ()

    year_from: int | None = None
    year_to: int | None = None
    sources: tuple[str, ...] = ("openalex", "inspire")
    per_query_limit: int = 50
    max_rounds: int = 2
    seeds_per_round: int = 10
    refs_per_seed: int = 8

    # Titles that must appear if retrieval works. A miss fails the run.
    known_items: list[str] = field(default_factory=list)

    # Stage 4. Written for a screener that sees only a title and an abstract.
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""

    # Rules applied before any model call: a phrase in `forbidden` excludes outright, and
    # a work matching none of `required` is excluded. There is no rule that *includes* --
    # see relevance.triage.
    screen_forbidden: tuple[str, ...] = ()
    screen_required: tuple[str, ...] = ()

    # Stage 6 columns. Each must be quotable from the paper or it is recorded null.
    extraction_schema: tuple[str, ...] = ()

    mailto: str = ""
    offline: bool = False

    def to_config(self) -> SearchConfig:
        return SearchConfig(
            question=self.question,
            queries=list(self.queries),
            seed_dois=self.seed_dois,
            year_from=self.year_from,
            year_to=self.year_to,
            sources=self.sources,
            per_query_limit=self.per_query_limit,
            max_rounds=self.max_rounds,
            seeds_per_round=self.seeds_per_round,
            refs_per_seed=self.refs_per_seed,
            known_items=list(self.known_items),
            mailto=self.mailto,
            out_dir=run_dir(self.name),
            offline=self.offline,
        )


def run(spec: SearchSpec) -> int:
    """Run every stage. Returns 0, or 1 when a known item was not found."""
    cfg = spec.to_config()
    warning = warn_if_inside_repo(cfg.out_dir)
    if warning:
        print(f"  [WARN] {warning}")
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)

    print(f"question: {cfg.question}")
    print(f"output  : {cfg.out_dir}  (override with ${OUT_DIR_ENV})\n")

    print("[1/7] retrieve")
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
        print(f"  [{mark}] {row['wanted'][:62]} (similarity {row['similarity']})")
    if not known:
        print("  (none configured)")

    print("\n[5/7] screen")
    screen_dir = cfg.out_dir / "screen"
    to_model, rule_excluded = relevance.triage_all(
        corpus.works, spec.screen_required, spec.screen_forbidden
    )
    print(f"  triage: {len(rule_excluded)} excluded by rule, {len(to_model)} need the model")
    batches = screen.prepare_batches(
        corpus, spec.inclusion_criteria, spec.exclusion_criteria, screen_dir, works=to_model
    )
    counts = screen.apply_verdicts(corpus, screen.load_verdicts(screen_dir / "verdicts.jsonl"))
    batch_bytes = sum(path.stat().st_size for path in batches)
    print(f"  {len(batches)} batches, {batch_bytes / 1024:.0f} KB (~{batch_bytes // 4000} k tokens)")
    if counts["realigned"]:
        print(f"  {counts['realigned']} verdict(s) relocated by checksum after the corpus shifted")
    if counts["unverified"]:
        print(f"  [WARN] {counts['unverified']} verdict(s) carry no checksum and could not be "
              f"verified; re-screen them if the corpus has changed since they were written")
    if counts["misaligned"]:
        print(f"  [WARN] {counts['misaligned']} verdict(s) named a different paper and were refused")
    if counts["unscreened"] + counts["by_rule"] == len(corpus):
        print(f"  no verdicts yet -- answer the batches into {screen_dir / 'verdicts.jsonl'}, then re-run")
    else:
        print(f"  include {counts['include']}, exclude {counts['exclude']}, "
              f"unsure {counts['unsure']}, unscreened {counts['unscreened']}")
    screen.write_review_queue(cfg.out_dir / "needs_review.md", screen.needs_review(corpus))

    print("\n[6/7] extract")
    # Only works that BOTH passed the gate and were screened in are worth reading.
    included = [work for work in screen.included(corpus) if work.validation == "verified"]
    if not included and counts["unscreened"] + counts["by_rule"] == len(corpus):
        included = passed
        print("  no screening verdicts yet; preparing tasks for every validated work")
    extract_dir = cfg.out_dir / "extract"
    tasks = extract.prepare_tasks(included, extract_dir, schema=spec.extraction_schema)
    rows = extract.load_rows(extract_dir / "rows.jsonl")
    accepted, complaints = extract.validate_rows(rows, schema=spec.extraction_schema)
    print(f"  {len(tasks)} extraction tasks, {len(accepted)}/{len(rows)} rows accepted")
    if complaints:
        print(f"  {len(complaints)} row(s) flagged for review (kept, but the quote is weak):")
    for complaint in complaints[:5]:
        print(f"    [flag] {complaint}")
    if not rows:
        print(f"  no rows yet -- answer the tasks into {extract_dir / 'rows.jsonl'}, then re-run")

    print("\n[7/7] write outputs")
    corpus.write_jsonl(cfg.out_dir / "corpus.jsonl")
    report.write_shortlist(cfg.out_dir / "shortlist.md", passed)
    held = report.write_quarantine(cfg.out_dir / "quarantine.md", verdicts)
    report.write_run_log(cfg.out_dir / "run.json", cfg, corpus, rounds, verdicts, known)

    # Only validated works reach the bibliography. Quarantined ones never appear.
    entry_count, findings, uncitable = export.write_bibtex(cfg.out_dir / "refs.bib", included or passed)
    errors = [finding for finding in findings if finding.level == "error"]
    kept = export.write_evidence_csv(
        cfg.out_dir / "evidence.csv", accepted, columns=export.columns_for(spec.extraction_schema)
    )

    print(f"  corpus {len(corpus)} | validated {len(passed)} | quarantined {held}")
    print(f"  refs.bib: {entry_count} entries, {len(errors)} errors")
    print(f"  evidence.csv: {kept} rows, each with a source quote")
    if uncitable:
        print(f"  {len(uncitable)} work(s) dropped as uncitable (no author on the index record)")
    for finding in errors[:5]:
        print(f"    [error] {finding.key}: {finding.message}")

    missed = [row for row in known if not row["found"]]
    if missed:
        print(f"\nWARNING: {len(missed)} known-item(s) not found -- retrieval is incomplete")
        return 1
    return 0
