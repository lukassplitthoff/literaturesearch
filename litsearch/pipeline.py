"""The eight-stage run, so a search is a configuration rather than a copied script.

Every search differs only in its question, its queries and its criteria. Keeping the stage
sequence here means a fix reaches every search at once, and an example is short enough to
read in one screen.

Screening, the overview and extraction need a language model, and nothing in this module
calls one. They hand off through files: the run writes tasks, an agent answers them, and
the next run reads the answers. So a full search is a sequence of invocations:

    python <search>.py     # retrieve, validate, write screening batches
    #   lit-screener answers screen/verdicts.jsonl
    python <search>.py     # apply verdicts; write overview packets and extraction wave 1
    #   lit-summarizer writes overview/draft.md
    #   lit-extractor answers extract/rows.jsonl for the wave's papers
    python <search>.py     # publish overview.md; write evidence.csv and refs.bib
    #   to read further: raise extraction_waves by one and repeat the last two steps

Re-running is cheap: every index response is cached on disk.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from bibcheck.verify import IndexClient
from litsearch import (
    conflict,
    export,
    extract,
    overview,
    plan,
    prioritize,
    relevance,
    report,
    retrieve,
    screen,
    snowball,
)
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
    # Stage 6 reads papers in full in waves, most promising first (see priority.md). Only
    # `extraction_waves` waves are issued; raise it by one to read the next wave.
    extraction_wave_size: int = prioritize.DEFAULT_WAVE_SIZE
    extraction_waves: int = 1
    # Stage 6 refuses to issue more papers than this in total. Each is a full paper read.
    max_extraction_tasks: int = extract.DEFAULT_MAX_TASKS
    # How priority.md ranks papers for the waves. None keeps the defaults, which suit a
    # "what was measured" question: experiments first, terms from the column names. A
    # question about methods or theory should put "theory" and "method" first instead, and
    # name the terms that mark the papers worth reading in full.
    priority_role_points: dict[str, int] | None = None
    priority_terms: tuple[str, ...] | None = None

    # Stage 4b, the abstract-level overview of every screened-in paper. `summary_focus` is
    # the angle it takes ("which materials, and what limits T1"); empty describes the set.
    # `summary_group_by` is "role", "year" or "theme" (the summarizer proposes the themes).
    summary_focus: str = ""
    summary_group_by: str = "role"

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


def select_for_extraction(corpus) -> list:
    """The works later stages may read: screened in AND verified.

    There is deliberately no fallback to "every validated work" when screening has not
    happened: that fallback once turned a run with no verdicts into hundreds of full-paper
    reads. With no verdicts, this is empty.
    """
    return [work for work in screen.included(corpus) if work.validation == "verified"]


def run_overview(
    out_dir: Path,
    spec: SearchSpec,
    included: list,
    cite_keys: dict[int, str],
    unscreened: int,
    review_queue: int,
    quarantined: int,
) -> str:
    """Stage 4b: write overview packets, and publish overview.md once a clean draft exists.

    Returns the stage's state: blocked, empty, waiting, problems or published.
    """
    overview_dir = out_dir / "overview"
    published = out_dir / "overview.md"
    problems_path = overview_dir / "problems.txt"
    if unscreened or not included:
        # An overview.md from an earlier, finished screen no longer describes this one.
        published.unlink(missing_ok=True)
    if unscreened:
        print(f"  [BLOCKED] {unscreened} work(s) have no screening verdict; the overview needs a finished screen")
        return "blocked"
    if not included:
        print("  nothing screened in -- no overview")
        return "empty"
    packets, no_abstract = overview.prepare_packets(
        included,
        cite_keys,
        spec.question,
        overview_dir,
        focus=spec.summary_focus,
        group_by=spec.summary_group_by,
    )
    size = sum(path.stat().st_size for path in packets)
    print(
        f"  {len(included)} work(s) in {len(packets)} packet(s), {size / 1024:.0f} KB "
        f"(~{size // 4000} k tokens); {len(no_abstract)} without an abstract"
    )
    draft_path = overview_dir / "draft.md"
    if not draft_path.exists():
        print(f"  no draft yet -- lit-summarizer writes {draft_path}, then re-run")
        return "waiting"

    draft = draft_path.read_text(encoding="utf-8")
    keyed = [(cite_keys[position], work) for position, work in enumerate(included) if position in cite_keys]
    abstracts = {key: work.abstract for key, work in keyed if (work.abstract or "").strip()}
    problems = overview.check_draft(draft, abstracts)
    if problems:
        # Fail closed: a stale overview.md from an earlier draft must not outlive a bad one.
        published.unlink(missing_ok=True)
        problems_path.write_text("\n".join(problems) + "\n", encoding="utf-8")
        print(f"  [BLOCKED] the draft breaks {len(problems)} rule(s); overview.md not written. All in {problems_path}")
        for problem in problems[:5]:
            print(f"    - {problem}")
        return "problems"
    problems_path.unlink(missing_ok=True)
    not_discussed = overview.write_overview(
        published,
        draft,
        spec.question,
        spec.summary_focus,
        [key for key, _ in keyed],
        no_abstract,
        review_queue=review_queue,
        quarantined=quarantined,
    )
    print(f"  overview.md written; {len(not_discussed)} included work(s) not discussed, listed in its footer")
    return "published"


@dataclass
class ExtractionPlan:
    """What stage 6 will do: the ranked queue, the waves, and what blocks it."""

    ranked: list[dict]
    waves: list[list[str]]
    answered: set[str]
    pending: list[str]
    queued: list[str]
    skipped: set[str]
    ignored: list[str]
    blockers: list[str]


def plan_extraction(
    included: list,
    cite_keys: dict[int, str],
    unscreened: int,
    spec: SearchSpec,
    extract_dir: Path,
    rows: list[dict],
) -> ExtractionPlan:
    """Rank the screened-in papers, cut the authorised waves, and check the preconditions.

    Reads extract/selection.txt and extract/waves.json but writes nothing, so the plan can
    be inspected -- and tested -- before any task file exists.
    """
    pinned, skipped = prioritize.load_selection(extract_dir / "selection.txt")
    candidates = {cite_keys[position] for position in range(len(included)) if position in cite_keys}
    # A selection can only reorder or drop screened-in papers; it cannot pull one in.
    ignored = [key for key in list(pinned) + sorted(skipped) if key not in candidates]
    pinned = [key for key in pinned if key in candidates]
    ranked = prioritize.score_works(
        included,
        cite_keys,
        spec.extraction_schema,
        pinned,
        role_points=spec.priority_role_points,
        terms=set(spec.priority_terms) if spec.priority_terms is not None else None,
    )
    waves = prioritize.plan_waves(
        [row["key"] for row in ranked],
        prioritize.load_waves(extract_dir / "waves.json"),
        spec.extraction_wave_size,
        spec.extraction_waves,
        skipped,
    )
    issued = {key for wave in waves for key in wave}
    answered = prioritize.answered_keys(rows)
    return ExtractionPlan(
        ranked=ranked,
        waves=waves,
        answered=answered,
        pending=[key for wave in waves for key in wave if key not in answered],
        queued=[row["key"] for row in ranked if row["key"] not in issued and row["key"] not in skipped],
        skipped=skipped,
        ignored=ignored,
        blockers=extract.extraction_blockers(
            len(issued), unscreened, spec.extraction_schema, spec.max_extraction_tasks
        ),
    )


def _duration(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"


class StageClock:
    """Prints each stage header, and how long the previous stage took.

    A full run is several minutes of throttled requests. The stage timings say where
    those minutes went, and the closing line says the run is over -- rather than leaving
    the last stage's output to be mistaken for a run still in progress.
    """

    def __init__(self) -> None:
        self.started = self.lap = time.monotonic()

    def _close_stage(self) -> None:
        now = time.monotonic()
        print(f"  ({_duration(now - self.lap)})")
        self.lap = now

    def stage(self, header: str, first: bool = False) -> None:
        if not first:
            self._close_stage()
            print()
        print(header)

    def finish(self, outcome: str) -> None:
        self._close_stage()
        print(f"\nDone in {_duration(time.monotonic() - self.started)} -- {outcome}")


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

    clock = StageClock()
    clock.stage("[1/8] retrieve", first=True)
    corpus = retrieve.run(fetcher, cfg)
    fetcher.save_cache()  # flush before each long stage, so an interrupt costs nothing

    clock.stage("[2/8] snowball")
    rounds = snowball.expand(fetcher, corpus, cfg)
    fetcher.save_cache()

    clock.stage("[3/8] validate (the gate)")
    client = IndexClient(cache_path=cfg.cache_path, mailto=cfg.mailto, offline=cfg.offline)
    passed, verdicts = validate_all(corpus.works, client)
    fetcher.save_cache()
    print(f"  {len(passed)} verified, {len(verdicts) - len(passed)} quarantined")

    clock.stage("[4/8] known-item check")
    known = report.known_item_results(corpus, cfg.known_items)
    for row in known:
        mark = "OK  " if row["found"] else "MISS"
        print(f"  [{mark}] {row['wanted'][:62]} (similarity {row['similarity']})")
    if not known:
        print("  known items: (none configured)")
    if spec.gold_set:
        print("  gold set: checked after screening, so each paper's verdict is its current one")

    clock.stage("[5/8] screen")
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

    # The gold set is reported here, after the verdicts are applied, not with the known
    # items: its status column is the screening verdict, and read before screening it said
    # "unscreened" for every paper -- hiding a gold paper the screener had excluded.
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
        if gold_result["found_but_screened_out"]:
            print(
                f"  [WARN] gold paper(s) found but not screened in: "
                f"{', '.join(gold_result['found_but_screened_out'])} -- check the criteria against them"
            )

    # The works that will be rendered into refs.bib, and the keys they will carry there.
    # The overview and extraction are given those keys so that both can be joined to the
    # bibliography -- the output contract requires every cite_key to name a real entry, and
    # tasks used to be handed placeholders like work007 instead.
    included = select_for_extraction(corpus)
    bib_works = included or passed
    cite_keys = export.cite_keys_for(bib_works)
    included_keys = cite_keys if included else {}

    clock.stage("[6/8] overview (abstract level)")
    run_overview(
        cfg.out_dir,
        spec,
        included,
        included_keys,
        counts["unscreened"],
        review_queue,
        len(verdicts) - len(passed),
    )

    clock.stage("[7/8] extract (full text, in waves)")
    extract_dir = cfg.out_dir / "extract"
    rows = extract.load_rows(extract_dir / "rows.jsonl")
    extraction = plan_extraction(included, included_keys, counts["unscreened"], spec, extract_dir, rows)
    coverage = prioritize.write_priority(
        cfg.out_dir / "priority.md",
        extraction.ranked,
        extraction.waves,
        extraction.answered,
        extraction.skipped,
        spec.extraction_schema,
        extraction.blockers,
        role_points=spec.priority_role_points,
        terms=set(spec.priority_terms) if spec.priority_terms is not None else None,
    )
    print(
        f"  {len(included)} work(s) screened in and verified; waves of {spec.extraction_wave_size}, "
        f"{spec.extraction_waves} authorised, at most {spec.max_extraction_tasks} papers in total"
    )
    for key in extraction.ignored:
        print(f"  [WARN] selection.txt names {key}, which is not a screened-in, citable work -- ignored")
    if extraction.blockers:
        extract.clear_tasks(extract_dir)
        tasks = []
        print("  [BLOCKED] no extraction tasks written:")
        for reason in extraction.blockers:
            print(f"    - {reason}")
    else:
        prioritize.save_waves(extract_dir / "waves.json", extraction.waves)
        position_of = {key: position for position, key in included_keys.items()}
        tasks = extract.prepare_tasks(
            [included[position_of[key]] for key in extraction.pending],
            extract_dir,
            schema=spec.extraction_schema,
            cite_keys=dict(enumerate(extraction.pending)),
        )
        for number, wave in enumerate(extraction.waves, start=1):
            read = sum(key in extraction.answered for key in wave)
            print(f"  wave {number}: {len(wave)} paper(s), {read} read, {len(wave) - read} pending")
        print(
            f"  {len(tasks)} task(s) x {len(spec.extraction_schema)} field(s) written; "
            f"each is one extractor reading one full paper"
        )
        if not tasks and extraction.queued:
            print(
                f"  every issued wave is read; {len(extraction.queued)} paper(s) still queued -- set "
                f"extraction_waves={len(extraction.waves) + 1} to issue the next wave"
            )
    print(f"  read in full: {coverage['answered']} of {coverage['included']} screened-in paper(s), see priority.md")
    accepted, complaints = extract.validate_rows(rows, schema=spec.extraction_schema)
    print(f"  {len(accepted)}/{len(rows)} rows accepted")
    if complaints:
        print(f"  {len(complaints)} row(s) flagged for review (kept, but the quote is weak):")
    for complaint in complaints[:5]:
        print(f"    [flag] {complaint}")
    if tasks and not rows:
        print(f"  no rows yet -- answer the tasks into {extract_dir / 'rows.jsonl'}, then re-run")

    clock.stage("[8/8] write outputs")
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
        clock.finish(f"{len(missed)} known item(s) missed")
        return 1
    clock.finish(f"outputs in {cfg.out_dir}")
    return 0
