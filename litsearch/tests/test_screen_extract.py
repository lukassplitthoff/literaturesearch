"""Stages 4 and 6: the batch handoff, and the quote guarantee."""

from __future__ import annotations

import json

from litsearch import extract, screen
from litsearch.corpus import Corpus
from litsearch.sources.base import Work


def corpus_of(count: int) -> Corpus:
    corpus = Corpus()
    corpus.add_all([Work(title=f"Paper number {i}", doi=f"10.1/{i}", abstract=f"Abstract {i}") for i in range(count)])
    return corpus


# --------------------------------------------------------------------------- screening


def test_batches_split_and_cover_every_work(tmp_path):
    corpus = corpus_of(7)
    paths = screen.prepare_batches(corpus, "include all", "exclude none", tmp_path, batch_size=3)
    assert len(paths) == 3
    seen = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["inclusion_criteria"] == "include all"
        seen.extend(w["index"] for w in payload["works"])
    assert seen == list(range(7)), "indices must be global and cover the corpus exactly"


def test_batches_expose_only_title_and_abstract(tmp_path):
    corpus = corpus_of(1)
    corpus.works[0].oa_pdf_url = "https://example.org/secret.pdf"
    payload = json.loads(screen.prepare_batches(corpus, "a", "b", tmp_path)[0].read_text(encoding="utf-8"))
    assert "oa_pdf_url" not in payload["works"][0]
    assert "doi" not in payload["works"][0]


def test_stale_batches_are_cleared(tmp_path):
    (tmp_path / "batch_99.json").write_text("{}", encoding="utf-8")
    screen.prepare_batches(corpus_of(2), "a", "b", tmp_path, batch_size=10)
    assert not (tmp_path / "batch_99.json").exists()


def test_verdicts_round_trip(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        '{"index": 0, "verdict": "include", "reason": "on topic"}\n'
        '{"index": 1, "verdict": "exclude", "reason": "wrong platform"}\n',
        encoding="utf-8",
    )
    verdicts = screen.load_verdicts(path)
    assert verdicts[0]["verdict"] == "include"
    assert verdicts[1]["reason"] == "wrong platform"


def test_malformed_and_unknown_verdicts_are_skipped_not_guessed(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        '{"index": 0, "verdict": "include", "reason": "ok"}\n'
        "not json at all\n"
        '{"index": 1, "verdict": "probably?", "reason": "made up verdict"}\n'
        '{"verdict": "include", "reason": "no index"}\n',
        encoding="utf-8",
    )
    verdicts = screen.load_verdicts(path)
    assert list(verdicts) == [0]


def test_missing_verdicts_file_is_not_an_error(tmp_path):
    assert screen.load_verdicts(tmp_path / "absent.jsonl") == {}


def test_unscreened_works_are_counted_and_never_included():
    corpus = corpus_of(3)
    counts = screen.apply_verdicts(corpus, {0: {"verdict": "include", "reason": "yes"}})
    assert counts == {"include": 1, "exclude": 0, "unsure": 0, "unscreened": 2}
    assert [w.title for w in screen.included(corpus)] == ["Paper number 0"]
    assert len(screen.needs_review(corpus)) == 2, "unscreened work must surface for review"


def test_unsure_is_not_included_but_does_surface():
    corpus = corpus_of(2)
    screen.apply_verdicts(
        corpus,
        {0: {"verdict": "unsure", "reason": "abstract silent"}, 1: {"verdict": "include", "reason": "yes"}},
    )
    assert len(screen.included(corpus)) == 1
    assert [w.screen for w in screen.needs_review(corpus)] == ["unsure"]


def test_review_queue_is_written(tmp_path):
    corpus = corpus_of(2)
    screen.apply_verdicts(corpus, {0: {"verdict": "unsure", "reason": "unclear"}})
    count = screen.write_review_queue(tmp_path / "review.md", screen.needs_review(corpus))
    assert count == 2
    body = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "unsure" in body and "unscreened" in body


# -------------------------------------------------------------------------- extraction


def test_tasks_carry_the_pdf_url_and_schema(tmp_path):
    work = Work(title="A paper", doi="10.1/a", oa_pdf_url="https://example.org/a.pdf", abstract="text")
    paths = extract.prepare_tasks([work], tmp_path, schema=("T1_us",), cite_keys={0: "Ann2020"})
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["cite_key"] == "Ann2020"
    assert payload["has_open_access_pdf"] is True
    assert payload["schema"] == ["T1_us"]


def test_task_marks_papers_without_an_open_access_pdf(tmp_path):
    paths = extract.prepare_tasks([Work(title="Paywalled", doi="10.1/b")], tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["has_open_access_pdf"] is False
    assert payload["pdf_url"] == ""


def test_row_without_a_quote_is_dropped():
    rows = [
        {"cite_key": "A", "T1_us": 360, "source_quote": "We measure T1 of 360 us."},
        {"cite_key": "B", "T1_us": 99999, "source_quote": ""},
        {"cite_key": "C", "T1_us": 88888},
    ]
    accepted, complaints = extract.validate_rows(rows, schema=("T1_us",))
    assert [r["cite_key"] for r in accepted] == ["A"]
    assert len(complaints) == 2
    assert all("no source_quote" in c for c in complaints)


def test_quote_that_does_not_contain_the_number_is_flagged():
    rows = [{"cite_key": "X", "T1_us": 500, "source_quote": "The device was cooled to 10 mK."}]
    accepted, complaints = extract.validate_rows(rows, schema=("T1_us",))
    assert len(complaints) == 1
    assert "does not contain T1_us" in complaints[0]


def test_unit_conversion_is_tolerated():
    """0.36 ms in the paper, 360 us in the table: the digits still match."""
    rows = [{"cite_key": "X", "T1_us": 360, "source_quote": "an average T1 of 0.36 ms"}]
    _, complaints = extract.validate_rows(rows, schema=("T1_us",))
    assert complaints == []


def test_null_fields_never_complain():
    rows = [{"cite_key": "X", "T1_us": None, "T2_echo_us": "", "source_quote": "No values reported."}]
    accepted, complaints = extract.validate_rows(rows, schema=("T1_us", "T2_echo_us"))
    assert len(accepted) == 1
    assert complaints == []


def test_load_rows_skips_malformed_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"cite_key": "A"}\nbroken\n\n{"cite_key": "B"}\n', encoding="utf-8")
    assert [r["cite_key"] for r in extract.load_rows(path)] == ["A", "B"]


def test_load_rows_on_missing_file(tmp_path):
    assert extract.load_rows(tmp_path / "nope.jsonl") == []


# ---------------------------------------------------------------- output location safety


def test_out_root_honours_the_environment(monkeypatch, tmp_path):
    from litsearch import config

    monkeypatch.setenv(config.OUT_DIR_ENV, str(tmp_path / "elsewhere"))
    assert config.out_root() == tmp_path / "elsewhere"
    assert config.run_dir("myrun") == tmp_path / "elsewhere" / "myrun"


def test_out_root_defaults_outside_the_repository(monkeypatch):
    """Run outputs are data and must never be committed, so the default is under $HOME."""
    from pathlib import Path

    from litsearch import config

    monkeypatch.delenv(config.OUT_DIR_ENV, raising=False)
    root = config.out_root()
    assert root == Path.home() / "litsearch-runs"
    assert config.warn_if_inside_repo(root) == "", "the default must not sit inside the repo"


def test_a_path_inside_the_repository_is_flagged():
    from pathlib import Path

    from litsearch import config

    inside = Path(config.__file__).resolve().parent / "some_run_output"
    warning = config.warn_if_inside_repo(inside)
    assert "must never be committed" in warning
