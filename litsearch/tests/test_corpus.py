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
