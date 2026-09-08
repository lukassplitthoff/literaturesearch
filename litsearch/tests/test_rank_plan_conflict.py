"""Ranking signals, the reading plan, role screening and contradiction candidates.

No socket is opened here: every input is a Work built in the test, and none of the modules
under test makes a request or a model call.
"""

from __future__ import annotations

import json

from bibcheck.parser import loads
from litsearch import conflict, export, plan, rank, screen
from litsearch.corpus import Corpus
from litsearch.sources.base import Work

NOW = 2026


def work(title, year="2020", cited=0, oid="", refs=(), role="", authors=("Ada Lovelace",)):
    return Work(
        title=title,
        year=year,
        authors=list(authors),
        cited_by_count=cited,
        role=role,
        source_ids={"openalex": oid} if oid else {},
        references=list(refs),
    )


# ------------------------------------------------------------------ rank


def test_in_corpus_citations_counts_only_corpus_members():
    landmark = work("Landmark", oid="W1")
    citer = work("Citer", oid="W2", refs=["W1", "W999"])  # W999 is outside the corpus
    assert rank.in_corpus_citations([landmark, citer]) == [1, 0]


def test_a_self_citation_does_not_count():
    solo = work("Solo", oid="W1", refs=["W1"])
    assert rank.in_corpus_citations([solo]) == [0]


def test_a_repeated_reference_counts_once():
    landmark = work("Landmark", oid="W1")
    citer = work("Citer", oid="W2", refs=["W1", "W1", "W1"])
    assert rank.in_corpus_citations([landmark, citer]) == [1, 0]


def test_recent_work_is_not_buried_by_an_older_paper_with_more_citations():
    """The whole point of the rate: raw counts sort by age, which hides the current state."""
    old = work("Old and much cited", year="2010", cited=340)  # 340 / 17 = 20.0
    new = work("New and rising", year="2025", cited=120)  # 120 / 2  = 60.0
    ordered = [pair[0].title for pair in rank.rank([old, new], now_year=NOW)]
    assert ordered == ["New and rising", "Old and much cited"]


def test_a_work_with_no_year_sorts_last_but_is_never_dropped():
    dated = work("Dated", year="2024", cited=10)
    undated = work("Undated", year="", cited=9999)
    ordered = [pair[0].title for pair in rank.rank([dated, undated], now_year=NOW)]
    assert ordered == ["Dated", "Undated"]
    assert len(ordered) == 2, "a missing year must not remove a work from the ranking"


def test_a_paper_published_this_year_is_one_year_old_not_zero():
    fresh = work("Fresh", year=str(NOW), cited=5)
    assert rank.citations_per_year(fresh, NOW) == 5.0


# ------------------------------------------------------------------ roles


def corpus_of(*works):
    corpus = Corpus()
    for item in works:
        corpus.add(item)
    return corpus


def test_the_batch_ships_the_role_definitions(tmp_path):
    corpus = corpus_of(work("Paper one", oid="W1"))
    paths = screen.prepare_batches(corpus, "include", "exclude", tmp_path)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert set(payload["roles"]) == set(screen.ROLES)
    assert payload["roles"]["review"], "each role must travel with its definition"


def test_a_role_is_read_back_and_stamped_on_the_work(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        json.dumps({"index": 0, "t": "paper one", "verdict": "include", "role": "REVIEW", "reason": "r"}) + "\n",
        encoding="utf-8",
    )
    corpus = corpus_of(work("Paper one"))
    counts = screen.apply_verdicts(corpus, screen.load_verdicts(path))
    assert corpus.works[0].role == "review", "the role is normalised to lower case"
    assert counts["roled"] == 1


def test_a_role_outside_the_vocabulary_is_discarded(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        json.dumps({"index": 0, "t": "paper one", "verdict": "include", "role": "seminal", "reason": "r"}) + "\n",
        encoding="utf-8",
    )
    corpus = corpus_of(work("Paper one"))
    counts = screen.apply_verdicts(corpus, screen.load_verdicts(path))
    assert corpus.works[0].role == "", "an invented label must not create a category of one"
    assert counts["include"] == 1, "an unusable role must not cost the verdict"
    assert counts["roled"] == 0


def test_a_verdict_without_a_role_still_applies(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        json.dumps({"index": 0, "t": "paper one", "verdict": "include", "reason": "r"}) + "\n",
        encoding="utf-8",
    )
    corpus = corpus_of(work("Paper one"))
    counts = screen.apply_verdicts(corpus, screen.load_verdicts(path))
    assert counts["include"] == 1 and corpus.works[0].role == ""


def test_the_role_survives_a_corpus_round_trip(tmp_path):
    corpus = corpus_of(work("Paper one", role="theory"))
    path = tmp_path / "corpus.jsonl"
    corpus.write_jsonl(path)
    restored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert restored["role"] == "theory"
    assert Work.from_dict(restored).role == "theory"


# ------------------------------------------------------------------ reading plan


def plan_corpus():
    return [
        work("A survey of the field", year="2018", cited=200, oid="W1", role="review"),
        work("The landmark measurement", year="2015", cited=400, oid="W2", role="primary"),
        work("Cites the landmark", year="2016", cited=10, oid="W3", refs=["W2"], role="primary"),
        work("Also cites the landmark", year="2017", cited=10, oid="W4", refs=["W2"], role="primary"),
        work("Established result", year="2019", cited=90, oid="W5", role="primary"),
        work("Brand new result", year="2025", cited=8, oid="W6", role="primary"),
        work("Undated record", year="", cited=3, oid="W7", role="primary"),
    ]


def test_every_work_lands_in_exactly_one_phase():
    works = plan_corpus()
    phases = plan.build_phases(works, now_year=NOW)
    placed = [pair[0].title for pair in phases["foundation"] + phases["core"] + phases["frontier"]]
    assert sorted(placed) == sorted(w.title for w in works)
    assert len(placed) == len(set(placed)), "a paper must not appear in two phases"


def test_reviews_and_locally_cited_papers_are_the_foundation():
    phases = plan.build_phases(plan_corpus(), now_year=NOW)
    foundation = {pair[0].title for pair in phases["foundation"]}
    assert "A survey of the field" in foundation
    assert "The landmark measurement" in foundation, "two corpus members cite it"


def test_a_singly_cited_paper_is_not_a_landmark():
    works = [
        work("Cited once", year="2015", oid="W1"),
        work("The citer", year="2016", oid="W2", refs=["W1"]),
    ]
    phases = plan.build_phases(works, now_year=NOW)
    assert phases["landmarks"] == [], "one citation is noise, not a pattern"


def test_recent_work_goes_to_the_frontier_and_undated_work_does_not():
    phases = plan.build_phases(plan_corpus(), now_year=NOW)
    assert [pair[0].title for pair in phases["frontier"]] == ["Brand new result"]
    core = {pair[0].title for pair in phases["core"]}
    assert "Undated record" in core, "an unknown year cannot be shown to be recent"


def test_the_plan_names_the_keys_the_bibliography_will_use(tmp_path):
    works = plan_corpus()
    cite_keys = export.cite_keys_for(works)
    path = tmp_path / "reading_plan.md"
    plan.write_reading_plan(path, works, "how long is a piece of string", cite_keys=cite_keys)
    text = path.read_text(encoding="utf-8")
    for key in cite_keys.values():
        assert key in text, f"{key} is in refs.bib but not in the plan"
    assert "What this plan does not tell you" in text, "every format states its limits"


def test_the_plan_says_so_when_nothing_was_classified(tmp_path):
    works = [work("Unlabelled", year="2020")]
    path = tmp_path / "reading_plan.md"
    plan.write_reading_plan(path, works, "q")
    assert "Every work is unclassified" in path.read_text(encoding="utf-8")


def test_role_counts_include_the_unclassified():
    counts = plan.role_counts([work("a", role="review"), work("b"), work("c", role="theory")])
    assert counts["review"] == 1 and counts["theory"] == 1 and counts["unclassified"] == 1


# ------------------------------------------------------------------ cite keys


def test_cite_keys_match_what_write_bibtex_actually_emits(tmp_path):
    """The contract: every cite_key in evidence.csv names an entry in refs.bib."""
    works = [
        work("First paper", year="2021", authors=("Alexander P. Place",)),
        work("Second paper", year="2021", authors=("Helin Somoroff",)),
        work("No author anywhere", year="2020", authors=()),
    ]
    path = tmp_path / "refs.bib"
    export.write_bibtex(path, works)
    in_file = {entry.key for entry in loads(path.read_text(encoding="utf-8")).entries}
    mapping = export.cite_keys_for(works)
    assert set(mapping.values()) == in_file
    assert 2 not in mapping, "an uncitable work is absent from refs.bib and from the mapping"


# ------------------------------------------------------------------ conflicts


SCHEMA = ("qubit_type", "T1_us")


def row(key, qubit, t1, quote="we measure it"):
    return {"cite_key": key, "qubit_type": qubit, "T1_us": t1, "source_quote": quote, "confidence": "full_text"}


def test_the_quantity_and_condition_columns_are_derived_from_the_rows():
    rows = [row("A2021", "transmon", 100)]
    quantities, conditions = conflict.quantity_columns(rows, SCHEMA)
    assert quantities == ("T1_us",) and conditions == ("qubit_type",)


def test_a_wide_spread_between_two_papers_is_flagged():
    rows = [row("A2021", "transmon", 30), row("B2022", "transmon", 300)]
    found = conflict.find_conflicts(rows, SCHEMA)
    assert len(found) == 1
    assert found[0]["field"] == "T1_us" and found[0]["ratio"] == 10.0
    assert found[0]["low"]["cite_key"] == "A2021" and found[0]["high"]["cite_key"] == "B2022"


def test_one_paper_reporting_several_devices_is_not_a_contradiction():
    rows = [row("A2021", "transmon", 30), row("A2021", "transmon", 300)]
    assert conflict.find_conflicts(rows, SCHEMA) == [], "a paper cannot disagree with itself"


def test_rows_measured_under_different_conditions_are_not_compared():
    rows = [row("A2021", "transmon", 30), row("B2022", "fluxonium", 300)]
    assert conflict.find_conflicts(rows, SCHEMA) == []


def test_a_spread_below_the_threshold_is_left_alone():
    rows = [row("A2021", "transmon", 100), row("B2022", "transmon", 200)]
    assert conflict.find_conflicts(rows, SCHEMA) == [], "a 2x device spread is normal"


def test_values_written_as_text_are_still_compared():
    rows = [row("A2021", "transmon", "~30"), row("B2022", "transmon", "300.0")]
    assert len(conflict.find_conflicts(rows, SCHEMA)) == 1


def test_a_missing_condition_is_its_own_group():
    rows = [row("A2021", "", 30), row("B2022", "transmon", 300)]
    assert conflict.find_conflicts(rows, SCHEMA) == [], "an incomplete record is not a disagreement"


def test_the_written_report_carries_both_quotes(tmp_path):
    rows = [
        row("A2021", "transmon", 30, quote="a T1 of 30 us was measured"),
        row("B2022", "transmon", 300, quote="we observe T1 = 300 us"),
    ]
    path = tmp_path / "conflicts.md"
    count = conflict.write_conflicts(path, conflict.find_conflicts(rows, SCHEMA), len(rows))
    text = path.read_text(encoding="utf-8")
    assert count == 1
    assert "a T1 of 30 us was measured" in text and "we observe T1 = 300 us" in text
    assert "question, not a finding" in text, "a flag must not read as a verdict"


def test_the_empty_report_does_not_claim_agreement(tmp_path):
    path = tmp_path / "conflicts.md"
    assert conflict.write_conflicts(path, [], 0) == 0
    text = path.read_text(encoding="utf-8")
    assert "not evidence the literature agrees" in text
