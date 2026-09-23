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
from litsearch import conflict, export, extract, plan, relevance, report, retrieve, screen, snowball
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

    # Path to a gold set: papers a domain expert says must be found, matched by DOI.
    # Unlike known_items these are chosen without seeing the output, so recall against
    # them is a real measurement. A miss is reported, not fatal -- the point is to know
    # the number and watch it move.
    gold_set: str = ""

    # Stage 4. Written for a screener that sees only a title and an abstract.
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""

    # Rules applied before any model call: a phrase in `forbidden` excludes outright, and
    # a work matching none of `required` is excluded. There is no rule that *includes* --
    # see relevance.triage.
    screen_forbidden: tuple[str, ...] = ()
    screen_required: tuple[str, ...] = ()

    # Stage 4, second output. What KIND of paper each one is, decided in the same pass
    # as relevance because it is nearly free there. Override to suit the field -- a
    # clinical search wants "rct" and "cohort" where a physics one wants "primary".
    roles: tuple[str, ...] = screen.ROLES

    # Stage 6 columns. Each must be quotable from the paper or it is recorded null. Required:
    # with none, stage 6 writes no tasks.
    extraction_schema: tuple[str, ...] = ()
    # Stage 6 refuses to write more tasks than this. Each task is a full paper read.
    max_extraction_tasks: int = extract.DEFAULT_MAX_TASKS

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


def select_for_extraction(corpus, counts: dict[str, int], spec: SearchSpec) -> tuple[list, list[str]]:
    """The works stage 6 may read, and the reasons it may not run at all.

    Only works that both passed the gate and were screened in are candidates. There is
    deliberately no fallback to "every validated work" when screening has not happened:
    that fallback once turned a run with no verdicts into hundreds of full-paper reads.

    Args:
        corpus: the screened corpus.
        counts: the verdict counts returned by ``screen.apply_verdicts``.
        spec: the search, for its extraction schema and task limit.

    Returns:
        (candidates, blockers). Tasks are written only when ``blockers`` is empty.
    """
    candidates = [work for work in screen.included(corpus) if work.validation == "verified"]
    blockers = extract.extraction_blockers(
        len(candidates), counts["unscreened"], spec.extraction_schema, spec.max_extraction_tasks
    )
    return candidates, blockers


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
        print("  known items: (none configured)")

    gold_result = None
    if spec.gold_set:
        gold_result = report.gold_recall(corpus, report.load_gold_set(spec.gold_set))
        print(
            f"  gold set: {gold_result['found']}/{gold_result['total']} found "
            f"({gold_result['recall_pct']}% recall)"
        )
        for row in gold_result["rows"]:
            mark = "OK  " if row["found"] else "MISS"
            print(f"    [{mark}] {row['key']:30s} {row['screen']}")

    print("\n[5/7] screen")
    screen_dir = cfg.out_dir / "screen"
    to_model, rule_excluded = relevance.triage_all(corpus.works, spec.screen_required, spec.screen_forbidden)
    print(f"  triage: {len(rule_excluded)} excluded by rule, {len(to_model)} need the model")
    batches = screen.prepare_batches(
        corpus,
        spec.inclusion_criteria,
        spec.exclusion_criteria,
        screen_dir,
        works=to_model,
        roles=spec.roles,
    )
    counts = screen.apply_verdicts(corpus, screen.load_verdicts(screen_dir / "verdicts.jsonl", roles=spec.roles))
    batch_bytes = sum(path.stat().st_size for path in batches)
    print(f"  {len(batches)} batches, {batch_bytes / 1024:.0f} KB (~{batch_bytes // 4000} k tokens)")
    if counts["realigned"]:
        print(f"  {counts['realigned']} verdict(s) relocated by checksum after the corpus shifted")
    if counts["unverified"]:
        print(
            f"  [WARN] {counts['unverified']} verdict(s) carry no checksum and could not be "
            f"verified; re-screen them if the corpus has changed since they were written"
        )
    if counts["misaligned"]:
        print(f"  [WARN] {counts['misaligned']} verdict(s) named a different paper and were refused")
    if counts["unscreened"] + counts["by_rule"] == len(corpus):
        print(f"  no verdicts yet -- answer the batches into {screen_dir / 'verdicts.jsonl'}, then re-run")
    else:
        print(
            f"  include {counts['include']}, exclude {counts['exclude']}, "
            f"unsure {counts['unsure']}, unscreened {counts['unscreened']}"
        )
        screened = counts["include"] + counts["exclude"] + counts["unsure"]
        if screened and not counts["roled"]:
            print("  [WARN] no verdict carries a role; reading_plan.md cannot group by paper kind")
        elif screened:
            print(f"  roles on {counts['roled']}/{screened} screened work(s)")
    review_queue = screen.write_review_queue(cfg.out_dir / "needs_review.md", screen.needs_review(corpus))

    print("\n[6/7] extract")
    included, blockers = select_for_extraction(corpus, counts, spec)
    extract_dir = cfg.out_dir / "extract"
    # The works that will be rendered into refs.bib, and the keys they will carry there.
    # Extraction is given those keys so that evidence.csv can be joined to the bibliography
    # -- the output contract requires every cite_key to name a real entry, and tasks used to
    # be handed placeholders like work007 instead.
    bib_works = included or passed
    cite_keys = export.cite_keys_for(bib_works)
    print(f"  {len(included)} work(s) screened in and verified (limit {spec.max_extraction_tasks})")
    if blockers:
        extract.clear_tasks(extract_dir)
        tasks = []
        print("  [BLOCKED] no extraction tasks written:")
        for reason in blockers:
            print(f"    - {reason}")
    else:
        tasks = extract.prepare_tasks(included, extract_dir, schema=spec.extraction_schema, cite_keys=cite_keys)
        print(
            f"  {len(tasks)} extraction task(s) x {len(spec.extraction_schema)} field(s); "
            f"each task is one extractor reading one full paper"
        )
    rows = extract.load_rows(extract_dir / "rows.jsonl")
    accepted, complaints = extract.validate_rows(rows, schema=spec.extraction_schema)
    print(f"  {len(accepted)}/{len(rows)} rows accepted")
    if complaints:
        print(f"  {len(complaints)} row(s) flagged for review (kept, but the quote is weak):")
    for complaint in complaints[:5]:
        print(f"    [flag] {complaint}")
    if tasks and not rows:
        print(f"  no rows yet -- answer the tasks into {extract_dir / 'rows.jsonl'}, then re-run")

    print("\n[7/7] write outputs")
    corpus.write_jsonl(cfg.out_dir / "corpus.jsonl")
    report.write_shortlist(cfg.out_dir / "shortlist.md", passed)
    held = report.write_quarantine(cfg.out_dir / "quarantine.md", verdicts)
    report.write_run_log(cfg.out_dir / "run.json", cfg, corpus, rounds, verdicts, known, gold=gold_result)
    placed = plan.write_reading_plan(
        cfg.out_dir / "reading_plan.md",
        bib_works,
        cfg.question,
        cite_keys=cite_keys,
        review_queue=review_queue,
        quarantined=held,
    )
    conflicts = conflict.find_conflicts(accepted, spec.extraction_schema)
    conflict.write_conflicts(cfg.out_dir / "conflicts.md", conflicts, len(accepted))

    # Only validated works reach the bibliography. Quarantined ones never appear.
    entry_count, findings, uncitable = export.write_bibtex(cfg.out_dir / "refs.bib", bib_works)
    errors = [finding for finding in findings if finding.level == "error"]
    kept = export.write_evidence_csv(
        cfg.out_dir / "evidence.csv", accepted, columns=export.columns_for(spec.extraction_schema)
    )

    print(f"  corpus {len(corpus)} | validated {len(passed)} | quarantined {held}")
    print(f"  refs.bib: {entry_count} entries, {len(errors)} errors")
    print(f"  evidence.csv: {kept} rows, each with a source quote")
    print(
        f"  reading_plan.md: foundation {placed.get('foundation', 0)}, "
        f"core evidence {placed.get('core evidence', 0)}, frontier {placed.get('frontier', 0)}"
    )
    if conflicts:
        print(
            f"  conflicts.md: {len(conflicts)} group(s) of rows disagree by more than "
            f"{conflict.DISAGREEMENT_RATIO:g}x -- read the quotes before citing either side"
        )
    if uncitable:
        print(f"  {len(uncitable)} work(s) dropped as uncitable (no author on the index record)")
    for finding in errors[:5]:
        print(f"    [error] {finding.key}: {finding.message}")

    missed = [row for row in known if not row["found"]]
    if missed:
        print(f"\nWARNING: {len(missed)} known-item(s) not found -- retrieval is incomplete")
        return 1
    return 0
