"""Stage 6 ordering: the priority score, the selection file and the waves."""

from __future__ import annotations

import pytest

from litsearch import prioritize
from litsearch.sources.base import Work


def work(title, abstract="", role="", year="2020", cites=0) -> Work:
    return Work(title=title, abstract=abstract, role=role, year=year, cited_by_count=cites, doi=f"10.1/{title}")


def keys_for(works):
    return {position: f"K{position}" for position in range(len(works))}


# --------------------------------------------------------------------------- schema terms


def test_schema_terms_drop_units_and_keep_digit_names():
    assert prioritize.schema_terms(("T1_us", "T2_echo_us", "fidelity_pct", "temperature_mK")) == {
        "t1",
        "t2",
        "echo",
        "fidelity",
        "temperature",
    }


def test_schema_terms_of_an_empty_schema():
    assert prioritize.schema_terms(()) == set()


# --------------------------------------------------------------------------- the score


def test_a_primary_paper_naming_the_columns_ranks_first():
    works = [
        work("A review", "We review T1 in many qubits.", role="review"),
        work("A measurement", "We measure T1 and T2 echo in a tantalum transmon.", role="primary"),
        work("A theory", "We model T1.", role="theory"),
    ]
    rows = prioritize.score_works(works, keys_for(works), ("T1_us", "T2_echo_us"))
    assert [row["key"] for row in rows] == ["K1", "K0", "K2"]
    assert rows[0]["role_points"] == 3 and rows[0]["terms"] == ["echo", "t1", "t2"]


def test_a_methods_question_can_put_theory_first():
    works = [
        work("A measurement", "We measure T1.", role="primary"),
        work("A Floquet theory", "A Floquet-Markov treatment of the driven transmon.", role="theory"),
    ]
    rows = prioritize.score_works(
        works,
        keys_for(works),
        ("T1_us",),
        role_points={"theory": 3, "primary": 1},
        terms={"floquet", "markov"},
    )
    assert [row["key"] for row in rows] == ["K1", "K0"]
    assert rows[0]["terms"] == ["floquet", "markov"]


def test_the_overrides_are_stated_in_priority_md(tmp_path):
    prioritize.write_priority(
        tmp_path / "p.md", [], [], set(), set(), ("T1_us",), role_points={"theory": 3}, terms={"floquet"}
    )
    text = (tmp_path / "p.md").read_text(encoding="utf-8")
    assert "role points (theory 3)" in text and "Terms searched for: floquet." in text


def test_term_points_are_capped():
    works = [work("Everything", "t1 t2 echo fidelity temperature gate", role="review")]
    rows = prioritize.score_works(works, keys_for(works), ("T1_us", "T2_us", "echo", "fidelity", "temperature_mK"))
    assert rows[0]["term_points"] == prioritize.MAX_TERM_POINTS


def test_ties_break_on_citations_per_year():
    works = [work("Old", role="primary", year="2000", cites=100), work("Hot", role="primary", year="2020", cites=100)]
    rows = prioritize.score_works(works, keys_for(works), ("T1_us",), now_year=2025)
    assert [row["key"] for row in rows] == ["K1", "K0"]


def test_pins_come_first_in_file_order():
    works = [work(f"P{i}", "T1", role="primary") for i in range(4)]
    rows = prioritize.score_works(works, keys_for(works), ("T1_us",), pinned=["K3", "K2"])
    assert [row["key"] for row in rows][:2] == ["K3", "K2"]
    assert rows[0]["pinned"]


def test_uncitable_works_are_not_ranked():
    works = [work("Keyed"), work("Not keyed")]
    rows = prioritize.score_works(works, {0: "K0"}, ("T1_us",))
    assert [row["key"] for row in rows] == ["K0"]


# --------------------------------------------------------------------------- selection


def test_selection_file(tmp_path):
    path = tmp_path / "selection.txt"
    path.write_text("# comment\n+A\n+B  # reason\n-C\n+C\nnonsense\n\n-D\n", encoding="utf-8")
    pinned, skipped = prioritize.load_selection(path)
    assert pinned == ["A", "B"], "a key both pinned and skipped is skipped"
    assert skipped == {"C", "D"}


def test_missing_selection_file_is_empty(tmp_path):
    assert prioritize.load_selection(tmp_path / "none.txt") == ([], set())


# --------------------------------------------------------------------------- waves


def test_waves_are_cut_from_the_ranking():
    waves = prioritize.plan_waves([f"K{i}" for i in range(45)], [], wave_size=20, waves_allowed=2)
    assert [len(w) for w in waves] == [20, 20]
    assert waves[0][0] == "K0"


def test_an_issued_wave_does_not_move_when_the_ranking_does():
    issued = [["K5", "K6"]]
    waves = prioritize.plan_waves(["K0", "K1", "K5", "K6"], issued, wave_size=2, waves_allowed=2)
    assert waves == [["K5", "K6"], ["K0", "K1"]]


def test_a_paper_no_longer_included_leaves_its_wave():
    waves = prioritize.plan_waves(["K1"], [["K0", "K1"]], wave_size=2, waves_allowed=1)
    assert waves == [["K1"]]


def test_lowering_waves_allowed_never_unissues():
    waves = prioritize.plan_waves(["K0", "K1", "K2"], [["K0"], ["K1"]], wave_size=1, waves_allowed=1)
    assert waves == [["K0"], ["K1"]]


def test_skipped_papers_are_never_waved():
    waves = prioritize.plan_waves(["K0", "K1", "K2"], [["K0"]], wave_size=5, waves_allowed=2, skipped={"K0", "K2"})
    assert waves == [["K1"]]


def test_wave_size_must_be_positive():
    with pytest.raises(ValueError):
        prioritize.plan_waves(["K0"], [], wave_size=0, waves_allowed=1)


def test_waves_round_trip(tmp_path):
    prioritize.save_waves(tmp_path / "waves.json", [["A", "B"], ["C"]])
    assert prioritize.load_waves(tmp_path / "waves.json") == [["A", "B"], ["C"]]
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert prioritize.load_waves(tmp_path / "bad.json") == []


# --------------------------------------------------------------------------- priority.md


def test_priority_file_states_coverage_and_status(tmp_path):
    works = [work(f"P{i}", "T1", role="primary") for i in range(4)]
    rows = prioritize.score_works(works, keys_for(works), ("T1_us",))
    coverage = prioritize.write_priority(
        tmp_path / "priority.md", rows, [["K0", "K1"]], answered={"K0"}, skipped={"K3"}, schema=("T1_us",)
    )
    text = (tmp_path / "priority.md").read_text(encoding="utf-8")
    assert coverage == {"included": 4, "issued": 2, "answered": 1}
    assert "Read in full: 1 of 4 screened-in papers." in text
    assert "| read |" in text and "| pending |" in text and "| queued |" in text and "| skipped |" in text


def test_priority_file_shows_blockers(tmp_path):
    prioritize.write_priority(tmp_path / "p.md", [], [], set(), set(), (), blockers=["schema is empty"])
    assert "Extraction is blocked" in (tmp_path / "p.md").read_text(encoding="utf-8")
