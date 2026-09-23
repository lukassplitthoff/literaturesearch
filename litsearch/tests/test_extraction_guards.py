"""Stage 6 fails closed: no screening, no schema or too many papers means no tasks."""

from __future__ import annotations

import pytest

from litsearch import extract, screen
from litsearch.corpus import Corpus
from litsearch.pipeline import SearchSpec, select_for_extraction
from litsearch.sources.base import Work

SCHEMA = ("T1_us", "material")


def verified_corpus(count: int) -> Corpus:
    corpus = Corpus()
    corpus.add_all([Work(title=f"Paper number {i}", doi=f"10.1/{i}", abstract=f"Abstract {i}") for i in range(count)])
    for work in corpus.works:
        work.validation = "verified"
    return corpus


def spec_with(schema=SCHEMA, max_tasks=extract.DEFAULT_MAX_TASKS) -> SearchSpec:
    return SearchSpec(name="t", question="q", queries=["q"], extraction_schema=schema, max_extraction_tasks=max_tasks)


def screen_all(corpus: Corpus, verdict: str) -> dict[str, int]:
    verdicts = {
        i: {"verdict": verdict, "reason": "r", "t": screen.checksum(work.title), "role": ""}
        for i, work in enumerate(corpus.works)
    }
    return screen.apply_verdicts(corpus, verdicts)


# --------------------------------------------------------------------------- the incident


def test_no_verdicts_means_no_extraction_not_every_validated_work():
    """The removed fallback: 300 validated, unscreened works once became 300 full reads."""
    corpus = verified_corpus(300)
    counts = screen.apply_verdicts(corpus, {})
    candidates, blockers = select_for_extraction(corpus, counts, spec_with())
    assert candidates == []
    assert any("300 work(s) have no screening verdict" in reason for reason in blockers)


def test_partial_screening_blocks_extraction():
    corpus = verified_corpus(10)
    verdicts = {
        i: {"verdict": "include", "reason": "r", "t": screen.checksum(corpus.works[i].title), "role": ""}
        for i in range(4)
    }
    counts = screen.apply_verdicts(corpus, verdicts)
    _, blockers = select_for_extraction(corpus, counts, spec_with())
    assert any("6 work(s) have no screening verdict" in reason for reason in blockers)


def test_rule_excluded_works_do_not_count_as_unscreened():
    corpus = verified_corpus(3)
    corpus.works[2].screen = "exclude"
    corpus.works[2].screen_reason = "rule: forbidden phrase"
    verdicts = {
        i: {"verdict": "include", "reason": "r", "t": screen.checksum(corpus.works[i].title), "role": ""}
        for i in range(2)
    }
    counts = screen.apply_verdicts(corpus, verdicts)
    candidates, blockers = select_for_extraction(corpus, counts, spec_with())
    assert blockers == []
    assert len(candidates) == 2


# --------------------------------------------------------------------------- the happy path


def test_fully_screened_run_extracts_only_verified_includes():
    corpus = verified_corpus(5)
    corpus.works[4].validation = "quarantined"
    counts = screen_all(corpus, "include")
    candidates, blockers = select_for_extraction(corpus, counts, spec_with())
    assert blockers == []
    assert [w.title for w in candidates] == [f"Paper number {i}" for i in range(4)]


def test_unsure_and_excluded_works_are_never_candidates():
    corpus = verified_corpus(4)
    counts = screen_all(corpus, "unsure")
    candidates, blockers = select_for_extraction(corpus, counts, spec_with())
    assert (candidates, blockers) == ([], [])


# --------------------------------------------------------------------------- schema and cap


def test_empty_schema_blocks_extraction():
    corpus = verified_corpus(3)
    counts = screen_all(corpus, "include")
    _, blockers = select_for_extraction(corpus, counts, spec_with(schema=()))
    assert any("extraction_schema is empty" in reason for reason in blockers)


def test_the_template_default_schema_is_empty_and_therefore_blocks():
    assert SearchSpec(name="t", question="q", queries=["q"]).extraction_schema == ()


def test_too_many_candidates_block_extraction():
    corpus = verified_corpus(12)
    counts = screen_all(corpus, "include")
    _, blockers = select_for_extraction(corpus, counts, spec_with(max_tasks=10))
    assert any("12 works are screened in, above max_extraction_tasks=10" in reason for reason in blockers)


def test_the_cap_is_inclusive():
    corpus = verified_corpus(10)
    counts = screen_all(corpus, "include")
    _, blockers = select_for_extraction(corpus, counts, spec_with(max_tasks=10))
    assert blockers == []


def test_every_blocker_is_reported_not_just_the_first():
    blockers = extract.extraction_blockers(candidates=500, unscreened=7, schema=(), max_tasks=60)
    assert len(blockers) == 3


def test_the_default_cap_admits_the_largest_deliberate_run():
    """The worked example extracted from 46 papers; the default must not block it."""
    assert extract.DEFAULT_MAX_TASKS >= 46


def test_blocker_messages_are_ascii():
    for reason in extract.extraction_blockers(candidates=500, unscreened=7, schema=(), max_tasks=60):
        reason.encode("ascii")


# --------------------------------------------------------------------------- prepare_tasks


def test_prepare_tasks_refuses_an_empty_schema(tmp_path):
    with pytest.raises(ValueError, match="schema is empty"):
        extract.prepare_tasks([Work(title="A paper", doi="10.1/a")], tmp_path, schema=())


def test_clear_tasks_removes_stale_tasks_only(tmp_path):
    (tmp_path / "task_000.json").write_text("{}", encoding="utf-8")
    (tmp_path / "rows.jsonl").write_text("{}\n", encoding="utf-8")
    extract.clear_tasks(tmp_path)
    assert not (tmp_path / "task_000.json").exists()
    assert (tmp_path / "rows.jsonl").exists(), "answered rows are data, never cleared"
