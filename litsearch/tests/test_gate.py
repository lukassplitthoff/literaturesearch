"""The validation gate.

The point of these tests is the guarantee in the README: a fabricated reference cannot
reach an output. The client runs with offline=True over a controlled cache, so no socket
is opened and every lookup outcome is deliberate.
"""

from __future__ import annotations

import json

import pytest

from bibcheck.verify import IndexClient
from litsearch.gate import QUARANTINED, VERIFIED, validate, validate_all
from litsearch.sources.base import Work

REAL_TITLE = "New material platform for superconducting transmon qubits"


@pytest.fixture
def offline_client(tmp_path):
    """An IndexClient that can only ever answer from the cache we hand it.

    The on-disk shape mirrors what IndexClient.save_cache writes: cache key -> either
    None (a recorded miss) or {"payload": <the API response>}.
    """

    def build(cache: dict) -> IndexClient:
        path = tmp_path / "cache.json"
        wrapped = {key: (None if value is None else {"payload": value}) for key, value in cache.items()}
        path.write_text(json.dumps(wrapped), encoding="utf-8")
        return IndexClient(cache_path=path, offline=True)

    return build


def crossref_key(doi: str) -> str:
    return f"crossref:doi:{doi.strip().lower()}"


def crossref_payload(title: str, doi: str) -> dict:
    return {
        "message": {
            "DOI": doi,
            "title": [title],
            "author": [{"family": "Place", "given": "A"}],
            "issued": {"date-parts": [[2021]]},
            "container-title": ["Nature Communications"],
        }
    }


def test_invented_doi_is_quarantined(offline_client):
    """The central guarantee: a made-up reference does not pass."""
    client = offline_client({})
    work = Work(title="A Paper That Does Not Exist", doi="10.9999/fabricated.12345")
    verdict = validate(work, client)
    assert verdict.status == QUARANTINED
    assert "not found" in verdict.reason.lower()


def test_invented_arxiv_id_is_quarantined(offline_client):
    client = offline_client({})
    work = Work(title="Also Not Real", arxiv_id="9999.99999")
    verdict = validate(work, client)
    assert verdict.status == QUARANTINED
    assert "does not resolve" in verdict.reason


def test_work_with_no_identifier_and_no_title_match_is_quarantined(offline_client):
    client = offline_client({})
    verdict = validate(Work(title="Unfindable Title"), client)
    assert verdict.status == QUARANTINED
    assert "no DOI" in verdict.reason


def test_empty_work_is_quarantined(offline_client):
    client = offline_client({})
    verdict = validate(Work(), client)
    assert verdict.status == QUARANTINED


def test_real_doi_verifies(offline_client):
    doi = "10.1038/s41467-021-22030-5"
    client = offline_client({crossref_key(doi): crossref_payload(REAL_TITLE, doi)})
    verdict = validate(Work(title=REAL_TITLE, doi=doi), client)
    assert verdict.status == VERIFIED
    assert verdict.index == "crossref"


def test_index_hit_with_a_different_paper_is_quarantined(offline_client):
    """A DOI that resolves is not enough -- it has to be the same paper."""
    doi = "10.1038/s41467-021-22030-5"
    client = offline_client({crossref_key(doi): crossref_payload("An Unrelated Paper About Birds", doi)})
    verdict = validate(Work(title=REAL_TITLE, doi=doi), client)
    assert verdict.status == QUARANTINED
    assert "title disagrees" in verdict.reason


def test_validate_all_partitions_and_stamps(offline_client):
    doi = "10.1038/s41467-021-22030-5"
    client = offline_client({crossref_key(doi): crossref_payload(REAL_TITLE, doi)})
    good = Work(title=REAL_TITLE, doi=doi)
    bad = Work(title="Fabricated", doi="10.9999/nope")
    passed, verdicts = validate_all([good, bad], client)

    assert [w.title for w in passed] == [REAL_TITLE]
    assert len(verdicts) == 2
    assert good.validation == VERIFIED and good.validation_source == "crossref"
    assert bad.validation == QUARANTINED


def test_nothing_unverified_ever_reaches_the_passed_list(offline_client):
    client = offline_client({})
    works = [Work(title=f"Invented paper {i}", doi=f"10.9999/x{i}") for i in range(5)]
    passed, verdicts = validate_all(works, client)
    assert passed == []
    assert all(v.status == QUARANTINED for v in verdicts)
