"""Dedup and merge. The same paper arriving from three indexes must become one record."""

from __future__ import annotations

from litsearch.corpus import Corpus, merge, title_similarity
from litsearch.sources.base import Work, clean_arxiv_id, clean_doi

TITLE = "New material platform for superconducting transmon qubits with coherence times"


def make(**kwargs) -> Work:
    base = {"title": TITLE, "year": "2021"}
    base.update(kwargs)
    return Work(**base)


def test_doi_is_the_strongest_identity():
    corpus = Corpus()
    corpus.add(make(doi="10.1038/s41467-021-22030-5", sources=["openalex"]))
    # Same DOI, a different title spelling: still one work.
    corpus.add(make(title="New Material Platform For Transmon Qubits", doi="10.1038/s41467-021-22030-5",
                    sources=["semanticscholar"]))
    assert len(corpus) == 1
    assert corpus.works[0].sources == ["openalex", "semanticscholar"]


def test_arxiv_id_dedups_when_no_doi():
    corpus = Corpus()
    corpus.add(make(arxiv_id="2003.00024", sources=["arxiv"]))
    corpus.add(make(title="A completely different wording", arxiv_id="2003.00024", sources=["inspire"]))
    assert len(corpus) == 1


def test_fuzzy_title_dedups_when_no_identifier():
    corpus = Corpus()
    corpus.add(make(sources=["inspire"]))
    corpus.add(make(title=TITLE + ".", sources=["openalex"]))
    assert len(corpus) == 1


def test_distinct_papers_stay_distinct():
    corpus = Corpus()
    corpus.add(make(doi="10.1/a"))
    corpus.add(make(title="Fluxonium qubit with millisecond coherence", doi="10.1/b"))
    assert len(corpus) == 2


def test_readding_the_same_works_creates_nothing_new():
    works = [make(doi="10.1/a"), make(title="Another paper entirely", doi="10.1/b")]
    corpus = Corpus()
    assert corpus.add_all(works) == 2
    assert corpus.add_all(works) == 0
    assert len(corpus) == 2


def test_merge_is_additive_and_never_overwrites():
    target = make(doi="10.1/a", venue="Nature", authors=["First A"], cited_by_count=10, sources=["openalex"])
    other = make(doi="10.1/a", venue="WRONG", abstract="an abstract", authors=["Other B"],
                 cited_by_count=99, sources=["inspire"])
    merge(target, other)
    assert target.venue == "Nature", "an existing value must not be overwritten"
    assert target.authors == ["First A"]
    assert target.abstract == "an abstract", "an empty field must be filled"
    assert target.cited_by_count == 99, "citation counts take the larger value"
    assert target.sources == ["openalex", "inspire"]


def test_untitled_works_are_refused():
    corpus = Corpus()
    corpus.add(Work(title="", doi="10.1/a"))
    assert len(corpus) == 0


def test_top_by_citations_ranks():
    corpus = Corpus()
    corpus.add_all([make(doi="10.1/a", cited_by_count=5, title="A paper"),
                    make(doi="10.1/b", cited_by_count=50, title="B paper"),
                    make(doi="10.1/c", cited_by_count=1, title="C paper")])
    assert [w.cited_by_count for w in corpus.top_by_citations(2)] == [50, 5]


def test_jsonl_round_trip(tmp_path):
    corpus = Corpus()
    corpus.add_all([make(doi="10.1/a", authors=["X Y"], sources=["openalex"]),
                    make(doi="10.1/b", title="Second paper", arxiv_id="2101.00001")])
    path = tmp_path / "corpus.jsonl"
    corpus.write_jsonl(path)
    restored = Corpus.read_jsonl(path)
    assert len(restored) == 2
    assert {w.doi for w in restored.works} == {"10.1/a", "10.1/b"}


def test_clean_doi_strips_url_forms():
    assert clean_doi("https://doi.org/10.1/X") == "10.1/x"
    assert clean_doi("doi:10.1/x") == "10.1/x"
    assert clean_doi("not-a-doi") is None
    assert clean_doi(None) is None


def test_clean_arxiv_id_handles_versions_and_urls():
    assert clean_arxiv_id("arXiv:2003.00024v2") == "2003.00024"
    assert clean_arxiv_id("https://arxiv.org/abs/2003.00024") == "2003.00024"
    assert clean_arxiv_id("nothing here") is None


def test_title_similarity_is_symmetric_and_bounded():
    assert title_similarity("abc", "abc") == 1.0
    assert title_similarity("", "abc") == 0.0
    assert 0.0 <= title_similarity("superconducting qubit", "superconducting qubits") <= 1.0


def test_near_identical_titles_with_different_dois_stay_separate():
    """A paper and its companion, or Part I and Part II, must not collapse into one."""
    corpus = Corpus()
    corpus.add(make(title="Coherence in transmon qubits Part I", doi="10.1/part1"))
    corpus.add(make(title="Coherence in transmon qubits Part II", doi="10.1/part2"))
    assert len(corpus) == 2


def test_near_identical_titles_with_different_arxiv_ids_stay_separate():
    corpus = Corpus()
    corpus.add(make(title="Qubit measurements v1", arxiv_id="2101.00001"))
    corpus.add(make(title="Qubit measurements v2", arxiv_id="2202.00002"))
    assert len(corpus) == 2


def test_fuzzy_match_still_applies_when_only_one_side_has_a_doi():
    """A DOI-less record from one index must still merge into the DOI-bearing one."""
    corpus = Corpus()
    corpus.add(make(doi="10.1/a", sources=["openalex"]))
    corpus.add(make(sources=["inspire"]))
    assert len(corpus) == 1
    assert corpus.works[0].doi == "10.1/a"


def test_arxiv_doi_and_published_doi_are_one_paper():
    """The preprint carries 10.48550/arXiv.*, the article carries the publisher's DOI.

    Both are the same work. Treating the arXiv DOI as a conflicting identifier let this
    pair through as two entries, which bibcheck then flagged as a duplicate.
    """
    corpus = Corpus()
    corpus.add(make(title="Disentangling losses in tantalum superconducting circuits",
                    doi="10.1103/physrevx.13.041005"))
    corpus.add(make(title="Disentangling Losses in Tantalum Superconducting Circuits",
                    doi="10.48550/arxiv.2301.07848", arxiv_id="2301.07848"))
    assert len(corpus) == 1
    assert corpus.works[0].doi == "10.1103/physrevx.13.041005", "the publisher DOI must win"
    assert corpus.works[0].arxiv_id == "2301.07848", "the arXiv id must be carried over"


def test_two_different_papers_both_arxiv_only_stay_separate():
    corpus = Corpus()
    corpus.add(make(title="Paper about tantalum films", doi="10.48550/arxiv.2301.00001", arxiv_id="2301.00001"))
    corpus.add(make(title="Paper about niobium films", doi="10.48550/arxiv.2301.00002", arxiv_id="2301.00002"))
    assert len(corpus) == 2


def test_seeds_come_from_the_query_hits_not_the_whole_corpus():
    """Seeding on overall citation count drags the search into adjacent fields.

    A famous review pulled in by snowballing outranks every on-topic paper, and expanding
    it drags in its whole neighbourhood -- which is how a 186-work corpus became 1004
    works mostly about nanocrystals and domain walls.
    """
    corpus = Corpus()
    corpus.add_all([make(title="On topic query hit", doi="10.1/hit", cited_by_count=10)], round_index=0)
    corpus.add_all([make(title="Famous unrelated review", doi="10.1/rev", cited_by_count=90000)], round_index=1)
    seeds = corpus.seed_candidates(5)
    assert [w.title for w in seeds] == ["On topic query hit"]


def test_a_seed_is_never_expanded_twice():
    corpus = Corpus()
    corpus.add_all([make(title="Paper A", doi="10.1/a", cited_by_count=50),
                    make(title="Paper B", doi="10.1/b", cited_by_count=10)], round_index=0)
    first = corpus.seed_candidates(1)
    seen = {id(w) for w in first}
    second = corpus.seed_candidates(1, seen=seen)
    assert [w.title for w in first] == ["Paper A"]
    assert [w.title for w in second] == ["Paper B"]
    assert corpus.seed_candidates(5, seen=seen | {id(w) for w in second}) == []
