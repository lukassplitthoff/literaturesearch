"""Stage 4b: overview packets, the draft rules, and the published file."""

from __future__ import annotations

import json

import pytest

from litsearch import overview
from litsearch.sources.base import Work

ABSTRACTS = {
    "Place2021": "We report a tantalum transmon with T1 exceeding 0.3 ms. Coherence is limited by TLS.",
    "Wang2022": "We measure an average T1 of 503 us across devices on sapphire.",
}


def works_and_keys():
    works = [
        Work(title="Tantalum transmon", abstract=ABSTRACTS["Place2021"], role="primary", year="2021"),
        Work(title="Long T1 on sapphire", abstract=ABSTRACTS["Wang2022"], role="primary", year="2022"),
        Work(title="A review", abstract="", role="review", year="2019"),
    ]
    return works, {0: "Place2021", 1: "Wang2022", 2: "Rev2019"}


# --------------------------------------------------------------------------- packets


def test_packets_carry_the_full_abstract_and_the_key(tmp_path):
    works, keys = works_and_keys()
    works[0].abstract = "x" * 2000
    paths, no_abstract = overview.prepare_packets(works, keys, "q", tmp_path, focus="materials")
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["focus"] == "materials"
    by_key = {entry["key"]: entry for entry in payload["works"]}
    assert len(by_key["Place2021"]["abstract"]) == 2000, "not the 600-character screening cut"
    assert no_abstract == ["Rev2019"]


def test_packets_are_cut_to_size_and_stale_ones_cleared(tmp_path):
    (tmp_path / "packet_09.json").write_text("{}", encoding="utf-8")
    works = [Work(title=f"P{i}", abstract="a", role="primary") for i in range(7)]
    paths, _ = overview.prepare_packets(works, {i: f"K{i}" for i in range(7)}, "q", tmp_path, packet_size=3)
    assert len(paths) == 3
    assert not (tmp_path / "packet_09.json").exists()


def test_role_grouping_puts_reviews_first(tmp_path):
    works = [Work(title="B", abstract="a", role="primary"), Work(title="A", abstract="a", role="review")]
    paths, _ = overview.prepare_packets(works, {0: "P", 1: "R"}, "q", tmp_path)
    assert [e["key"] for e in json.loads(paths[0].read_text(encoding="utf-8"))["works"]] == ["R", "P"]


def test_unknown_grouping_is_refused(tmp_path):
    with pytest.raises(ValueError, match="summary_group_by"):
        overview.prepare_packets([], {}, "q", tmp_path, group_by="colour")


# --------------------------------------------------------------------------- the draft rules


def test_a_clean_draft_passes():
    draft = (
        "## Materials\n\n"
        "Tantalum is the recurring material [@Place2021; @Wang2022].\n\n"
        '- One device reports "T1 exceeding 0.3 ms" [@Place2021].\n'
        '- Another reports "an average T1 of 503 us" [@Wang2022], on sapphire.\n'
    )
    assert overview.check_draft(draft, ABSTRACTS) == []


def test_an_uncited_paragraph_is_a_problem():
    problems = overview.check_draft("Tantalum is popular.\n", ABSTRACTS)
    assert problems == ["uncited paragraph: Tantalum is popular."]


def test_an_unknown_key_is_a_problem():
    problems = overview.check_draft("Tantalum is popular [@Smith1999].\n", ABSTRACTS)
    assert any("unknown key [@Smith1999]" in p for p in problems)


def test_a_quote_not_in_the_abstract_is_a_problem():
    problems = overview.check_draft('It reports "T1 exceeding 1 ms" [@Place2021].\n', ABSTRACTS)
    assert any("quote not found in the abstract of Place2021" in p for p in problems)


def test_a_bare_number_is_a_problem():
    problems = overview.check_draft("T1 reaches 0.3 ms [@Place2021].\n", ABSTRACTS)
    assert any("number 0.3 has no abstract quote" in p for p in problems)


def test_a_number_carried_by_a_quote_in_the_same_bullet_passes():
    draft = 'T1 reaches 0.3 ms, "T1 exceeding 0.3 ms" [@Place2021].\n'
    assert overview.check_draft(draft, ABSTRACTS) == []


def test_a_quote_in_another_bullet_does_not_carry_a_number():
    draft = '- "T1 exceeding 0.3 ms" [@Place2021].\n- Another reaches 503 us [@Wang2022].\n'
    assert any("number 503" in p for p in overview.check_draft(draft, ABSTRACTS))


def test_names_with_digits_and_years_are_not_numbers():
    draft = "T1 and T2 in 3D cavities, since 2021 [@Place2021].\n"
    assert overview.check_draft(draft, ABSTRACTS) == []


def test_curly_quotes_are_read_as_quotes():
    draft = "It reports \u201cT1 exceeding 0.3 ms\u201d [@Place2021].\n"
    assert overview.check_draft(draft, ABSTRACTS) == []


def test_quote_matching_ignores_case_and_whitespace():
    draft = 'It reports "t1   exceeding\n0.3 ms" [@Place2021].\n'
    assert overview.check_draft(draft, ABSTRACTS) == []


def test_tables_are_refused():
    assert any("table" in p for p in overview.check_draft("| a | b |\n", ABSTRACTS))


def test_an_empty_draft_is_refused():
    assert overview.check_draft("  \n", ABSTRACTS) == ["the draft is empty"]


def test_problem_messages_are_ascii():
    problems = overview.check_draft("\u03c7/2\u03c0 is large.\n", ABSTRACTS)
    for problem in problems:
        problem.encode("ascii")


# --------------------------------------------------------------------------- the published file


def test_published_overview_has_the_fixed_header_and_footer(tmp_path):
    draft = "Tantalum recurs [@Place2021].\n"
    not_discussed = overview.write_overview(
        tmp_path / "overview.md",
        draft,
        "question?",
        "materials",
        ["Place2021", "Wang2022", "Rev2019"],
        ["Rev2019"],
        review_queue=4,
        quarantined=2,
    )
    text = (tmp_path / "overview.md").read_text(encoding="utf-8")
    assert not_discussed == ["Wang2022"]
    assert "**Built from abstracts only**" in text
    assert "Focus: materials" in text
    assert "Not discussed above (1):** [@Wang2022]" in text
    assert "No abstract on the index record (1):** [@Rev2019]" in text
    assert "4 work(s) are undecided" in text and "2 work(s) failed the validation gate" in text


def test_cited_keys_reads_grouped_citations():
    assert overview.cited_keys("a [@A; @B] b [@C]") == {"A", "B", "C"}
