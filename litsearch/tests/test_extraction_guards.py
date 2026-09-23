"""Stage 6 fails closed: no screening, no schema or too many papers means no tasks."""

from __future__ import annotations

import pytest

from litsearch import extract, prioritize, screen
from litsearch.corpus import Corpus
from litsearch.pipeline import SearchSpec, plan_extraction, select_for_extraction
from litsearch.sources.base import Work

SCHEMA = ("T1_us", "material")


def verified_corpus(count: int) -> Corpus:
    corpus = Corpus()
    corpus.add_all([Work(title=f"Paper number {i}", doi=f"10.1/{i}", abstract=f"Abstract {i}") for i in range(count)])
    for work in corpus.works:
        work.validation = "verified"
    return corpus


def spec_with(schema=SCHEMA, max_tasks=extract.DEFAULT_MAX_TASKS, wave_size=20, waves=1) -> SearchSpec:
    return SearchSpec(
        name="t",
        question="q",
        queries=["q"],
        extraction_schema=schema,
        max_extraction_tasks=max_tasks,
        extraction_wave_size=wave_size,
        extraction_waves=waves,
    )


def verdict_for(work: Work, verdict: str) -> dict:
    return {"verdict": verdict, "reason": "r", "t": screen.checksum(work.title), "role": ""}


def screen_all(corpus: Corpus, verdict: str) -> dict[str, int]:
    return screen.apply_verdicts(corpus, {i: verdict_for(work, verdict) for i, work in enumerate(corpus.works)})


def plan_for(corpus, counts, spec, tmp_path, rows=()):
    included = select_for_extraction(corpus)
    keys = {position: f"Key{position:03d}" for position in range(len(included))}
    return plan_extraction(included, keys, counts["unscreened"], spec, tmp_path, list(rows))


# --------------------------------------------------------------------------- the incident


def test_no_verdicts_means_no_extraction_not_every_validated_work(tmp_path):
    """The removed fallback: 300 validated, unscreened works once became 300 full reads."""
    corpus = verified_corpus(300)
    counts = screen.apply_verdicts(corpus, {})
    assert select_for_extraction(corpus) == []
    plan = plan_for(corpus, counts, spec_with(), tmp_path)
    assert plan.pending == []
    assert any("300 work(s) have no screening verdict" in reason for reason in plan.blockers)


def test_partial_screening_blocks_extraction(tmp_path):
    corpus = verified_corpus(10)
    counts = screen.apply_verdicts(corpus, {i: verdict_for(corpus.works[i], "include") for i in range(4)})
    plan = plan_for(corpus, counts, spec_with(), tmp_path)
    assert any("6 work(s) have no screening verdict" in reason for reason in plan.blockers)


def test_rule_excluded_works_do_not_count_as_unscreened(tmp_path):
    corpus = verified_corpus(3)
    corpus.works[2].screen = "exclude"
    corpus.works[2].screen_reason = "rule: forbidden phrase"
    counts = screen.apply_verdicts(corpus, {i: verdict_for(corpus.works[i], "include") for i in range(2)})
    plan = plan_for(corpus, counts, spec_with(), tmp_path)
    assert plan.blockers == []
    assert len(plan.pending) == 2


# --------------------------------------------------------------------------- the happy path


def test_fully_screened_run_extracts_only_verified_includes():
    corpus = verified_corpus(5)
    corpus.works[4].validation = "quarantined"
    screen_all(corpus, "include")
    assert [w.title for w in select_for_extraction(corpus)] == [f"Paper number {i}" for i in range(4)]


def test_unsure_and_excluded_works_are_never_candidates(tmp_path):
    corpus = verified_corpus(4)
    counts = screen_all(corpus, "unsure")
    plan = plan_for(corpus, counts, spec_with(), tmp_path)
    assert (plan.pending, plan.blockers) == ([], [])


# --------------------------------------------------------------------------- schema and cap


def test_empty_schema_blocks_extraction(tmp_path):
    corpus = verified_corpus(3)
    counts = screen_all(corpus, "include")
    plan = plan_for(corpus, counts, spec_with(schema=()), tmp_path)
    assert any("extraction_schema is empty" in reason for reason in plan.blockers)


def test_the_template_default_schema_is_empty_and_therefore_blocks():
    assert SearchSpec(name="t", question="q", queries=["q"]).extraction_schema == ()


def test_the_cap_counts_issued_papers_not_included_ones(tmp_path):
    """300 included with one wave of 20 is 20 reads, well inside a cap of 60."""
    corpus = verified_corpus(300)
    counts = screen_all(corpus, "include")
    plan = plan_for(corpus, counts, spec_with(max_tasks=60), tmp_path)
    assert plan.blockers == []
    assert len(plan.pending) == 20
    assert len(plan.queued) == 280


def test_too_many_waves_block_extraction(tmp_path):
    corpus = verified_corpus(100)
    counts = screen_all(corpus, "include")
    plan = plan_for(corpus, counts, spec_with(max_tasks=60, waves=4), tmp_path)
    assert any("80 papers would be issued for extraction, above max_extraction_tasks=60" in r for r in plan.blockers)


def test_the_cap_is_inclusive(tmp_path):
    corpus = verified_corpus(60)
    counts = screen_all(corpus, "include")
    plan = plan_for(corpus, counts, spec_with(max_tasks=60, waves=3), tmp_path)
    assert plan.blockers == []


def test_every_blocker_is_reported_not_just_the_first():
    blockers = extract.extraction_blockers(planned=500, unscreened=7, schema=(), max_tasks=60)
    assert len(blockers) == 3


def test_the_default_cap_admits_the_largest_deliberate_run():
    """The worked example extracted from 46 papers; the default must not block it."""
    assert extract.DEFAULT_MAX_TASKS >= 46


def test_blocker_messages_are_ascii():
    for reason in extract.extraction_blockers(planned=500, unscreened=7, schema=(), max_tasks=60):
        reason.encode("ascii")


# --------------------------------------------------------------------------- waves


def test_answered_papers_are_not_reissued(tmp_path):
    corpus = verified_corpus(30)
    counts = screen_all(corpus, "include")
    first = plan_for(corpus, counts, spec_with(), tmp_path)
    prioritize.save_waves(tmp_path / "waves.json", first.waves)
    rows = [{"cite_key": key, "T1_us": None, "source_quote": ""} for key in first.pending[:15]]
    second = plan_for(corpus, counts, spec_with(), tmp_path, rows=rows)
    assert second.pending == first.pending[15:]


def test_the_next_wave_needs_extraction_waves_raised(tmp_path):
    corpus = verified_corpus(30)
    counts = screen_all(corpus, "include")
    first = plan_for(corpus, counts, spec_with(), tmp_path)
    prioritize.save_waves(tmp_path / "waves.json", first.waves)
    rows = [{"cite_key": key} for key in first.pending]
    done = plan_for(corpus, counts, spec_with(), tmp_path, rows=rows)
    assert done.pending == [] and len(done.queued) == 10
    more = plan_for(corpus, counts, spec_with(waves=2), tmp_path, rows=rows)
    assert len(more.pending) == 10
    assert set(more.pending).isdisjoint(first.pending)


def test_selection_skips_and_pins(tmp_path):
    corpus = verified_corpus(30)
    counts = screen_all(corpus, "include")
    (tmp_path / "selection.txt").write_text("+Key029  # must read\n-Key000\n", encoding="utf-8")
    plan = plan_for(corpus, counts, spec_with(), tmp_path)
    assert plan.pending[0] == "Key029"
    assert "Key000" not in plan.pending and "Key000" not in plan.queued


def test_a_pin_cannot_pull_in_an_unscreened_or_excluded_paper(tmp_path):
    corpus = verified_corpus(5)
    counts = screen_all(corpus, "include")
    (tmp_path / "selection.txt").write_text("+Smith1999\n", encoding="utf-8")
    plan = plan_for(corpus, counts, spec_with(), tmp_path)
    assert plan.ignored == ["Smith1999"]
    assert "Smith1999" not in plan.pending


# --------------------------------------------------------------------------- prepare_tasks and rows


def test_prepare_tasks_refuses_an_empty_schema(tmp_path):
    with pytest.raises(ValueError, match="schema is empty"):
        extract.prepare_tasks([Work(title="A paper", doi="10.1/a")], tmp_path, schema=())


def test_clear_tasks_removes_stale_tasks_only(tmp_path):
    (tmp_path / "task_000.json").write_text("{}", encoding="utf-8")
    (tmp_path / "rows.jsonl").write_text("{}\n", encoding="utf-8")
    extract.clear_tasks(tmp_path)
    assert not (tmp_path / "task_000.json").exists()
    assert (tmp_path / "rows.jsonl").exists(), "answered rows are data, never cleared"


def test_a_nothing_found_row_is_neither_evidence_nor_a_complaint():
    rows = [{"cite_key": "A", "T1_us": None, "material": None, "source_quote": "", "note": "no T1 reported"}]
    accepted, complaints = extract.validate_rows(rows, schema=SCHEMA)
    assert (accepted, complaints) == ([], [])


def test_the_extractor_is_told_to_always_write_a_row():
    assert "Always write at least one row" in extract.INSTRUCTIONS
