"""Source response parsing, against recorded payload shapes. No socket is opened."""

from __future__ import annotations

from litsearch.sources import ads, inspire, openalex, semanticscholar

OPENALEX_ITEM = {
    "id": "https://openalex.org/W3126423854",
    "doi": "https://doi.org/10.1038/s41467-021-22030-5",
    "display_name": "New material platform for superconducting transmon qubits",
    "publication_year": 2021,
    "authorships": [
        {"author": {"display_name": "Alexander P. Place"}},
        {"author": {"display_name": "Lila V. H. Rodgers"}},
    ],
    "primary_location": {
        "source": {"display_name": "Nature Communications"},
        "landing_page_url": "https://www.nature.com/articles/s41467-021-22030-5",
    },
    "abstract_inverted_index": {"Transmon": [0], "qubits": [1], "improve": [2]},
    "cited_by_count": 541,
    "best_oa_location": {"pdf_url": "https://www.nature.com/articles/s41467-021-22030-5.pdf"},
    "referenced_works": ["https://openalex.org/W111", "https://openalex.org/W222"],
}


def test_openalex_maps_every_field():
    work = openalex.to_work(OPENALEX_ITEM)
    assert work.title.startswith("New material platform")
    assert work.doi == "10.1038/s41467-021-22030-5", "the doi.org prefix must be stripped"
    assert work.year == "2021"
    assert work.authors == ["Alexander P. Place", "Lila V. H. Rodgers"]
    assert work.venue == "Nature Communications"
    assert work.cited_by_count == 541
    assert work.oa_pdf_url.endswith(".pdf")
    assert work.sources == ["openalex"]
    assert work.source_ids["openalex"] == "W3126423854"
    assert work.references == ["W111", "W222"], "reference ids must be shortened too"


def test_openalex_reconstructs_the_inverted_abstract():
    work = openalex.to_work(OPENALEX_ITEM)
    assert work.abstract == "Transmon qubits improve"


def test_openalex_abstract_reassembles_out_of_order_positions():
    item = dict(OPENALEX_ITEM, abstract_inverted_index={"world": [1], "hello": [0]})
    assert openalex.to_work(item).abstract == "hello world"


def test_openalex_tolerates_a_sparse_record():
    work = openalex.to_work({"id": "https://openalex.org/W1", "display_name": "Bare"})
    assert work.title == "Bare"
    assert work.doi is None
    assert work.venue == ""
    assert work.cited_by_count == 0


S2_ITEM = {
    "paperId": "abc123",
    "title": "Millisecond coherence in a superconducting qubit",
    "year": 2023,
    "abstract": "We report long coherence.",
    "authors": [{"name": "H. Somoroff"}],
    "externalIds": {"DOI": "10.1103/PhysRevLett.130.267001", "ArXiv": "2103.08578"},
    "venue": "Physical Review Letters",
    "citationCount": 300,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/2103.08578"},
}


def test_semanticscholar_maps_external_ids():
    work = semanticscholar.to_work(S2_ITEM)
    assert work.doi == "10.1103/physrevlett.130.267001"
    assert work.arxiv_id == "2103.08578"
    assert work.authors == ["H. Somoroff"]
    assert work.cited_by_count == 300
    assert work.sources == ["semanticscholar"]


def test_semanticscholar_tolerates_missing_external_ids():
    work = semanticscholar.to_work({"paperId": "x", "title": "No ids"})
    assert work.doi is None
    assert work.arxiv_id is None


INSPIRE_ITEM = {
    "metadata": {
        "control_number": 1234567,
        "titles": [{"title": "Coherent quantum dynamics"}],
        "authors": [{"full_name": "Doe, Jane"}],
        "dois": [{"value": "10.1103/PhysRevA.1.012345"}],
        "arxiv_eprints": [{"value": "2201.01234"}],
        "publication_info": [{"year": 2022, "journal_title": "Phys. Rev. A"}],
        "citation_count": 42,
        "abstracts": [{"value": "An abstract."}],
    }
}


def test_inspire_unwraps_metadata():
    work = inspire.to_work(INSPIRE_ITEM)
    assert work.title == "Coherent quantum dynamics"
    assert work.doi == "10.1103/physreva.1.012345"
    assert work.arxiv_id == "2201.01234"
    assert work.year == "2022"
    assert work.venue == "Phys. Rev. A"
    assert work.cited_by_count == 42
    assert work.source_ids["inspire"] == "1234567"


def test_inspire_tolerates_a_bare_record():
    work = inspire.to_work({"metadata": {"titles": [{"title": "Bare"}]}})
    assert work.title == "Bare"
    assert work.year == ""


def test_ads_is_deferred_and_never_raises(monkeypatch):
    """The stub must be safe to enable before a token exists."""
    monkeypatch.delenv(ads.TOKEN_ENV, raising=False)
    assert ads.available() is False
    assert ads.search(None, "any query") == []


def test_ads_reports_available_once_a_token_is_set(monkeypatch):
    monkeypatch.setenv(ads.TOKEN_ENV, "fake-token")
    assert ads.available() is True
    # Still unimplemented, but must degrade rather than raise.
    assert ads.search(None, "any query") == []


def test_a_single_page_article_is_not_rendered_as_a_range():
    """OpenAlex gives first_page == last_page for an article number. '045014--045014' is
    not a page range, and Crossref deposits it as '045014' -- bibcheck flagged the
    difference on a real bibliography."""
    item = dict(OPENALEX_ITEM, biblio={"volume": "4", "first_page": "045014", "last_page": "045014"})
    assert openalex.to_work(item).pages == "045014"


def test_a_real_page_range_is_preserved():
    item = dict(OPENALEX_ITEM, biblio={"volume": "12", "first_page": "1779", "last_page": "1786"})
    assert openalex.to_work(item).pages == "1779--1786"


def test_a_missing_last_page_falls_back_to_the_first():
    item = dict(OPENALEX_ITEM, biblio={"first_page": "42", "last_page": None})
    assert openalex.to_work(item).pages == "42"
