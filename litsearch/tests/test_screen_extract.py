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
        seen.extend(w["i"] for w in payload["works"])
    assert seen == list(range(7)), "indices must be global and cover the corpus exactly"


def test_batches_expose_only_title_and_abstract(tmp_path):
    corpus = corpus_of(1)
    corpus.works[0].oa_pdf_url = "https://example.org/secret.pdf"
    payload = json.loads(screen.prepare_batches(corpus, "a", "b", tmp_path)[0].read_text(encoding="utf-8"))
    assert set(payload["works"][0]) <= {"i", "t", "y", "a", "c"}, "index, title, checksum, year, abstract"


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
    assert counts == {"include": 1, "exclude": 0, "unsure": 0, "unscreened": 2, "by_rule": 0,
                      "misaligned": 0, "unverified": 1, "realigned": 0}, "a verdict with no checksum is counted"
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


def test_batch_indices_are_corpus_positions_not_subset_positions(tmp_path):
    """The bug this guards against silently applied every verdict to the wrong paper.

    prepare_batches is given the survivors of triage, but apply_verdicts looks up
    corpus.works[index]. Numbering the filtered subset from zero made a verdict about
    subset item 0 land on corpus item 0 -- a different paper entirely, and the output
    looked completely plausible.
    """
    corpus = corpus_of(6)
    subset = [corpus.works[2], corpus.works[5]]
    payload = json.loads(
        screen.prepare_batches(corpus, "a", "b", tmp_path, works=subset)[0].read_text(encoding="utf-8")
    )
    assert [w["i"] for w in payload["works"]] == [2, 5], "indices must be corpus positions"

    screen.apply_verdicts(corpus, {2: {"verdict": "include", "reason": "ok"}})
    assert [w.title for w in screen.included(corpus)] == ["Paper number 2"]


def test_a_work_outside_the_corpus_is_refused(tmp_path):
    corpus = corpus_of(2)
    stranger = Work(title="Not in the corpus", doi="10.1/x")
    try:
        screen.prepare_batches(corpus, "a", "b", tmp_path, works=[stranger])
    except ValueError as exc:
        assert "not in the corpus" in str(exc)
    else:
        raise AssertionError("must refuse works whose verdicts could not be applied back")


# ------------------------------------------------------- the verdict-alignment checksum


def test_a_verdict_never_lands_on_the_paper_its_stale_index_points_at():
    """The bug that motivated this: misaligned verdicts produced a clean-looking,
    fully validated bibliography of papers nobody had actually screened.

    The checksum names paper 2, so the verdict moves there. What must never happen is
    paper 0 -- the one the stale index points at -- being included.
    """
    corpus = corpus_of(3)
    counts = screen.apply_verdicts(
        corpus,
        {0: {"verdict": "include", "reason": "x", "t": "Paper number 2"}},
    )
    assert [w.title for w in screen.included(corpus)] == ["Paper number 2"]
    assert counts["realigned"] == 1


def test_a_verdict_for_a_paper_not_in_the_corpus_is_refused():
    """No index match and no checksum match: there is nothing to apply it to."""
    corpus = corpus_of(3)
    counts = screen.apply_verdicts(
        corpus,
        {0: {"verdict": "include", "reason": "x", "t": "A paper from another search"}},
    )
    assert counts["misaligned"] == 1
    assert screen.included(corpus) == [], "a verdict naming an absent paper must not apply"


def test_a_matching_echo_is_accepted():
    corpus = corpus_of(3)
    counts = screen.apply_verdicts(
        corpus,
        {1: {"verdict": "include", "reason": "x", "t": "Paper number 1"}},
    )
    assert counts["misaligned"] == 0
    assert [w.title for w in screen.included(corpus)] == ["Paper number 1"]


def test_a_partial_echo_is_accepted():
    """The echo is a short prefix, not the whole title."""
    corpus = Corpus()
    corpus.add(Work(title="High-On-Off-Ratio Beam-Splitter Interaction for Gates", doi="10.1/a"))
    counts = screen.apply_verdicts(
        corpus, {0: {"verdict": "include", "reason": "x", "t": "High-On-Off-Ratio Beam-Splitter"}}
    )
    assert counts["misaligned"] == 0 and len(screen.included(corpus)) == 1


def test_a_verdict_without_an_echo_still_applies():
    """Backward compatible: an older verdicts file carries no checksum to verify."""
    corpus = corpus_of(2)
    counts = screen.apply_verdicts(corpus, {0: {"verdict": "include", "reason": "x", "t": ""}})
    assert counts["misaligned"] == 0 and len(screen.included(corpus)) == 1


def test_batches_carry_the_title_for_the_checksum(tmp_path):
    payload = json.loads(
        screen.prepare_batches(corpus_of(2), "a", "b", tmp_path)[0].read_text(encoding="utf-8")
    )
    assert payload["works"][0]["t"] == "Paper number 0"
    assert "checksum" in payload["instructions"]


def test_titles_differing_in_one_token_are_distinguished():
    """A whole-string ratio scores these above 0.9 while they name different papers."""
    corpus = Corpus()
    corpus.add(Work(title="Coherence in transmon qubits Part I", doi="10.1/a"))
    corpus.add(Work(title="Coherence in transmon qubits Part II", doi="10.1/b"))
    counts = screen.apply_verdicts(
        corpus, {0: {"verdict": "include", "reason": "x", "t": "Coherence in transmon qubits Part II"}}
    )
    assert counts["misaligned"] == 1


def test_the_checksum_is_precomputed_in_the_batch(tmp_path):
    """Asking a screener to derive 'the first four words' was ambiguous for titles like
    '1 / f noise:' and for titles shorter than four words, and invited retyping."""
    corpus = Corpus()
    corpus.add(Work(title="1 / f noise: Implications for solid-state quantum information", doi="10.1/a"))
    corpus.add(Work(title="Quantum simulation", doi="10.1/b"))
    payload = json.loads(screen.prepare_batches(corpus, "a", "b", tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["works"][0]["c"] == "1 f noise implications"
    assert payload["works"][1]["c"] == "quantum simulation", "a short title must still yield a checksum"

    # And the value it ships is exactly what apply_verdicts will accept.
    counts = screen.apply_verdicts(
        corpus, {1: {"verdict": "include", "reason": "x", "t": payload["works"][1]["c"]}}
    )
    assert counts["misaligned"] == 0 and len(screen.included(corpus)) == 1


def test_task_offers_the_arxiv_pdf_as_well(tmp_path):
    """Every publisher PDF url failed on the first real extraction run: APS 403, Nature
    redirected into auth. The arXiv preprint of the same paper was open."""
    work = Work(title="A paper", doi="10.1103/x", arxiv_id="2303.00959",
                oa_pdf_url="http://link.aps.org/pdf/10.1103/x")
    payload = json.loads(extract.prepare_tasks([work], tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["arxiv_pdf_url"] == "https://arxiv.org/pdf/2303.00959"
    assert payload["pdf_url"].startswith("http://link.aps.org")


def test_a_work_with_only_an_arxiv_id_counts_as_reachable(tmp_path):
    work = Work(title="A preprint", arxiv_id="2101.00001")
    payload = json.loads(extract.prepare_tasks([work], tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["has_open_access_pdf"] is True
    assert payload["arxiv_pdf_url"].endswith("2101.00001")


def test_a_work_with_neither_is_marked_unreachable(tmp_path):
    payload = json.loads(
        extract.prepare_tasks([Work(title="Paywalled", doi="10.1/b")], tmp_path)[0].read_text(encoding="utf-8")
    )
    assert payload["has_open_access_pdf"] is False
    assert payload["arxiv_pdf_url"] == ""


def test_descriptive_fields_are_not_digit_checked():
    """'two 3D cavities' contains a digit incidentally; demanding the quote repeat it
    produced a stream of false alarms on the first real extraction run."""
    rows = [{"cite_key": "X", "modes": "two 3D cavities", "platform": "superconducting, 3D cavity",
             "gate_type": "beam splitter",
             "source_quote": "A SNAIL-based coupler exchanges photons between two cavity modes."}]
    _, complaints = extract.validate_rows(rows, schema=("modes", "platform", "gate_type"))
    assert complaints == [], f"descriptive text must not be digit-checked: {complaints}"


def test_numeric_fields_are_still_checked():
    rows = [{"cite_key": "X", "fidelity_pct": 99.9, "source_quote": "The device was cooled to 10 mK."}]
    _, complaints = extract.validate_rows(rows, schema=("fidelity_pct",))
    assert len(complaints) == 1 and "fidelity_pct" in complaints[0]


def test_is_numeric_distinguishes_measurements_from_prose():
    for value in (125, 99.92, "125", "99.92", " 1.3 ", "95.5%", "2e3"):
        assert extract.is_numeric(value), f"should be numeric: {value!r}"
    for value in ("two 3D cavities", "superconducting, 3D cavity", "beam splitter",
                  "three-wave", "", None):
        assert not extract.is_numeric(value), f"should not be numeric: {value!r}"


def test_a_flagged_row_is_kept_not_dropped():
    """A weak quote is a prompt for a human, not grounds for discarding a measurement."""
    rows = [{"cite_key": "X", "fidelity_pct": 99.9, "source_quote": "Cooled to 10 mK."}]
    accepted, complaints = extract.validate_rows(rows, schema=("fidelity_pct",))
    assert len(accepted) == 1 and len(complaints) == 1


def test_a_verdict_without_a_checksum_is_counted_as_unverified():
    """Corpus positions shift whenever dedup or triage changes, and an unverifiable
    verdict silently follows the shift. 64 stale verdicts once landed on the wrong works
    with nothing to catch them, because they predated the checksum."""
    corpus = corpus_of(2)
    counts = screen.apply_verdicts(corpus, {0: {"verdict": "include", "reason": "x"}})
    assert counts["unverified"] == 1
    assert len(screen.included(corpus)) == 1, "still applied, for backward compatibility"


def test_a_checksummed_verdict_is_not_counted_as_unverified():
    corpus = corpus_of(2)
    counts = screen.apply_verdicts(
        corpus, {0: {"verdict": "include", "reason": "x", "t": "paper number 0"}}
    )
    assert counts["unverified"] == 0


def test_a_verdict_is_relocated_by_checksum_when_the_corpus_shifts():
    """Merging one duplicate renumbers every work after it. Re-screening a whole corpus
    because one record moved would be absurd, so the checksum is the identity and the
    index only a hint."""
    corpus = Corpus()
    corpus.add(Work(title="Alpha paper about qubits", doi="10.1/a"))
    corpus.add(Work(title="Beta paper about cavities", doi="10.1/b"))
    # A verdict written when "Beta" sat at index 0, before "Alpha" was inserted ahead of it.
    counts = screen.apply_verdicts(
        corpus, {0: {"verdict": "include", "reason": "x", "t": "beta paper about cavities"}}
    )
    assert counts["realigned"] == 1
    assert [w.title for w in screen.included(corpus)] == ["Beta paper about cavities"]


def test_an_ambiguous_checksum_is_refused_not_guessed():
    corpus = Corpus()
    corpus.add(Work(title="Coherence in transmon qubits alpha", doi="10.1/a"))
    corpus.add(Work(title="Coherence in transmon qubits beta", doi="10.1/b"))
    # Both share the first four words, so the checksum cannot single one out.
    counts = screen.apply_verdicts(
        corpus, {5: {"verdict": "include", "reason": "x", "t": "coherence in transmon qubits"}}
    )
    assert counts["realigned"] == 0
    assert screen.included(corpus) == []


def test_a_relocated_verdict_is_not_also_counted_as_misaligned():
    """The stale entry must be dropped, not left pointing at the wrong work."""
    corpus = corpus_of(3)
    counts = screen.apply_verdicts(
        corpus, {0: {"verdict": "include", "reason": "x", "t": "Paper number 2"}}
    )
    assert counts["realigned"] == 1
    assert counts["misaligned"] == 0, "one verdict must not be counted twice"
    assert counts["unscreened"] == 2, "papers 0 and 1 are simply unscreened"


# ------------------------------------------------------------------ gold-set recall


def test_gold_recall_matches_on_doi_not_title():
    """DOIs are exact. Title matching would need a threshold to argue about."""
    from litsearch import report

    corpus = Corpus()
    corpus.add(Work(title="A completely different title", doi="10.1/found"))
    gold = [{"key": "a", "doi": "https://doi.org/10.1/FOUND", "title": "Whatever"},
            {"key": "b", "doi": "10.1/absent", "title": "Missing paper"}]
    result = report.gold_recall(corpus, gold)
    assert result["found"] == 1 and result["missed"] == ["b"]
    assert result["recall_pct"] == 50.0


def test_gold_recall_separates_not_found_from_screened_out():
    """A paper found but screened out is a different failure from one never retrieved,
    and conflating them hides which half of the pipeline needs work."""
    from litsearch import report

    corpus = Corpus()
    corpus.add(Work(title="Found but rejected", doi="10.1/a"))
    screen.apply_verdicts(corpus, {0: {"verdict": "exclude", "reason": "x", "t": "found but rejected"}})
    result = report.gold_recall(corpus, [{"key": "a", "doi": "10.1/a"}])
    assert result["found"] == 1
    assert result["missed"] == []
    assert result["found_but_screened_out"] == ["a"]
