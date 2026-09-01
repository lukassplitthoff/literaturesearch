"""Tests for index verification.

Run:  python -m pytest bibcheck/tests/test_verify.py -q

No test here opens a socket. The IndexClient is constructed with ``offline=True`` and a
pre-seeded cache, which is exactly the code path ``--offline`` uses, so the response
parsers and the comparison logic are exercised against recorded payloads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bibcheck.parser import loads
from bibcheck.rules import check_entry, normalize_entry
from bibcheck.verify import (
    VERDICT_MISMATCHED,
    VERDICT_NOT_FOUND,
    VERDICT_VERIFIED,
    IndexClient,
    _record_from_arxiv,
    _record_from_crossref,
    _record_from_openalex,
    _same_journal,
    apply_suggestions,
    verify_entry,
)

FIXTURES = Path(__file__).parent / "fixtures"

CROSSREF_PAYLOAD = {
    "message": {
        "DOI": "10.1038/s41567-022-01776-9",
        "title": ["Fast universal control of an oscillator with weak dispersive coupling to a qubit"],
        "author": [{"family": "Eickbusch", "given": "Alec"}, {"family": "Devoret", "given": "Michel H."}],
        "container-title": ["Nature Physics"],
        "volume": "18",
        "page": "1464-1469",
        "issued": {"date-parts": [[2022, 10, 3]]},
        "URL": "https://doi.org/10.1038/s41567-022-01776-9",
    }
}

ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2503.10623v2</id>
    <published>2025-03-13T17:59:00Z</published>
    <title>Fast Sideband Control of a Weakly Coupled Multimode Bosonic Memory</title>
    <author><name>Kaiwen Huang</name></author>
    <author><name>Andy Ding</name></author>
    <arxiv:doi>10.1103/PhysRevX.15.021017</arxiv:doi>
    <arxiv:journal_ref>Phys. Rev. X 15, 021017 (2025)</arxiv:journal_ref>
  </entry>
</feed>
"""

ARXIV_ATOM_UNPUBLISHED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2509.03375v1</id>
    <published>2025-09-03T12:00:00Z</published>
    <title>Effective Hamiltonian for an off-resonantly driven qubit-cavity system</title>
    <author><name>A. Jirlow</name></author>
  </entry>
</feed>
"""

OPENALEX_PAYLOAD = {
    "results": [
        {
            "id": "https://openalex.org/W123",
            "display_name": "Density Operators and Quasiprobability Distributions",
            "doi": "https://doi.org/10.1103/physrev.177.1882",
            "publication_year": 1969,
            "authorships": [
                {"author": {"display_name": "K. E. Cahill"}},
                {"author": {"display_name": "R. J. Glauber"}},
            ],
            "primary_location": {"source": {"display_name": "Physical Review"}},
            "biblio": {"volume": "177", "first_page": "1882", "last_page": "1902"},
        }
    ]
}


def _client(cache: dict, tmp_path: Path) -> IndexClient:
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({key: {"payload": value} for key, value in cache.items()}), encoding="utf-8")
    return IndexClient(cache_path=path, offline=True)


def _report(fields: str, entry_type: str = "article"):
    entry = loads("@%s{k, %s}" % (entry_type, fields)).entries[0]
    normalize_entry(entry)
    return check_entry(entry)


# --------------------------------------------------------------- normalisers


def test_crossref_record_normalisation():
    record = _record_from_crossref(CROSSREF_PAYLOAD["message"])
    assert record.source == "crossref"
    assert record.first_surname == "Eickbusch"
    assert record.year == "2022"
    assert record.journal == "Nature Physics"
    assert record.volume == "18"
    assert record.pages == "1464-1469"


def test_arxiv_record_reports_the_published_doi():
    import xml.etree.ElementTree as ET

    from bibcheck.verify import ATOM_NS

    node = ET.fromstring(ARXIV_ATOM).find("atom:entry", ATOM_NS)
    record = _record_from_arxiv(node)
    assert record.first_surname == "Huang"
    assert record.year == "2025"
    assert record.published_doi == "10.1103/PhysRevX.15.021017"
    assert record.published_journal == "Phys. Rev. X 15, 021017 (2025)"


def test_openalex_record_normalisation():
    record = _record_from_openalex(OPENALEX_PAYLOAD["results"][0])
    assert record.doi == "10.1103/physrev.177.1882"
    assert record.first_surname == "Cahill"
    assert record.pages == "1882-1902"


@pytest.mark.parametrize(
    "left, right, expected",
    [
        ("Phys. Rev. Lett.", "Physical Review Letters", True),
        ("Physical Review", "Physical Review", True),
        ("Nature Phys.", "Nature Physics", True),
        ("Nature Physics", "Science", False),
        ("", "Nature", False),
    ],
)
def test_journal_names_compare_tolerantly(left, right, expected):
    assert _same_journal(left, right) is expected


# ------------------------------------------------------------------- client


def test_offline_client_serves_the_cache_and_never_connects(tmp_path: Path):
    client = _client({"crossref:doi:10.1038/s41567-022-01776-9": CROSSREF_PAYLOAD}, tmp_path)
    record = client.crossref_by_doi("10.1038/s41567-022-01776-9")
    assert record is not None and record.journal == "Nature Physics"
    assert client.crossref_by_doi("10.9999/absent") is None
    assert client.network_errors == []


def test_cache_round_trips_through_disk(tmp_path: Path):
    client = _client({"arxiv:2503.10623": ARXIV_ATOM}, tmp_path)
    client.save_cache()
    reopened = IndexClient(cache_path=tmp_path / "cache.json", offline=True)
    assert reopened.arxiv_by_id("2503.10623").published_doi == "10.1103/PhysRevX.15.021017"


# ------------------------------------------------------------- verification


def test_matching_entry_verifies_clean(tmp_path: Path):
    report = _report(
        "author = {Eickbusch, A. and Devoret, M. H.}, "
        "title = {Fast universal control of an oscillator with weak dispersive coupling to a qubit}, "
        "journal = {Nature Physics}, volume = {18}, pages = {1464--1469}, year = {2022}, "
        "doi = {10.1038/s41567-022-01776-9}"
    )
    client = _client({"crossref:doi:10.1038/s41567-022-01776-9": CROSSREF_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert verification.verdict == VERDICT_VERIFIED
    assert verification.mismatched_fields == []


def test_wrong_volume_is_reported_as_a_field_mismatch(tmp_path: Path):
    report = _report(
        "author = {Eickbusch, A.}, "
        "title = {Fast universal control of an oscillator with weak dispersive coupling to a qubit}, "
        "journal = {Nature Physics}, volume = {17}, year = {2022}, doi = {10.1038/s41567-022-01776-9}"
    )
    client = _client({"crossref:doi:10.1038/s41567-022-01776-9": CROSSREF_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert verification.verdict == VERDICT_MISMATCHED
    assert verification.mismatched_fields == ["volume"]
    assert any(f.code == "field-mismatch" for f in verification.findings)


def test_page_dash_style_does_not_count_as_a_mismatch(tmp_path: Path):
    report = _report(
        "author = {Eickbusch, A.}, "
        "title = {Fast universal control of an oscillator with weak dispersive coupling to a qubit}, "
        "journal = {Nature Physics}, pages = {1464--1469}, year = {2022}, doi = {10.1038/s41567-022-01776-9}"
    )
    client = _client({"crossref:doi:10.1038/s41567-022-01776-9": CROSSREF_PAYLOAD}, tmp_path)
    assert verify_entry(report, client).verdict == VERDICT_VERIFIED


def test_published_preprint_is_flagged_for_upgrade(tmp_path: Path):
    report = _report(
        "author = {Huang, K. and others}, "
        "title = {Fast Sideband Control of a Weakly Coupled Multimode Bosonic Memory}, "
        "year = {2025}, note = {arXiv:2503.10623}"
    )
    assert report.is_preprint
    client = _client(
        {
            "arxiv:2503.10623": ARXIV_ATOM,
            "crossref:doi:10.1103/physrevx.15.021017": {
                "message": {
                    "DOI": "10.1103/PhysRevX.15.021017",
                    "title": ["Fast Sideband Control of a Weakly Coupled Multimode Bosonic Memory"],
                    "author": [{"family": "Huang", "given": "Kaiwen"}, {"family": "Ding", "given": "Andy"}],
                    "container-title": ["Physical Review X"],
                    "volume": "15",
                    "page": "021017",
                    "issued": {"date-parts": [[2025]]},
                }
            },
        },
        tmp_path,
    )
    verification = verify_entry(report, client)
    assert any(f.code == "preprint-published" for f in verification.findings)
    assert verification.source == "crossref"
    assert verification.suggestions["doi"] == "10.1103/PhysRevX.15.021017"
    assert verification.suggestions["journal"] == "Physical Review X"


def test_unpublished_preprint_is_not_flagged_for_upgrade(tmp_path: Path):
    report = _report(
        "author = {Jirlow, A.}, "
        "title = {Effective {Hamiltonian} for an off-resonantly driven qubit--cavity system}, "
        "year = {2025}, note = {arXiv:2509.03375}"
    )
    client = _client({"arxiv:2509.03375": ARXIV_ATOM_UNPUBLISHED}, tmp_path)
    verification = verify_entry(report, client)
    assert not any(f.code == "preprint-published" for f in verification.findings)
    assert verification.verdict == VERDICT_VERIFIED


ARXIV_MINTED_DOI_PAYLOAD = {
    "results": [
        {
            "id": "https://openalex.org/W999",
            "display_name": "Effective Hamiltonian for an off-resonantly driven qubit-cavity system",
            "doi": "https://doi.org/10.48550/arxiv.2509.03375",
            "publication_year": 2025,
            "authorships": [{"author": {"display_name": "A. Jirlow"}}],
            "primary_location": {"source": {"display_name": "arXiv (Cornell University)"}},
            "biblio": {},
        }
    ]
}


def test_arxiv_minted_doi_is_not_mistaken_for_journal_publication(tmp_path: Path):
    """10.48550/arXiv.* is arXiv's own DataCite DOI, indexed by Crossref and OpenAlex.

    Finding it must not be reported as 'this preprint has been published'.
    """
    report = _report(
        "author = {Jirlow, A.}, "
        "title = {Effective {Hamiltonian} for an off-resonantly driven qubit--cavity system}, "
        "year = {2025}, note = {arXiv:2509.03375}"
    )
    client = _client(
        {
            "arxiv:2509.03375": ARXIV_ATOM_UNPUBLISHED,
            "openalex:search:effective hamiltonian for an off resonantly driven qubit cavity system": (
                ARXIV_MINTED_DOI_PAYLOAD
            ),
        },
        tmp_path,
    )
    verification = verify_entry(report, client)
    assert not any(f.code == "preprint-published" for f in verification.findings)


def test_repository_name_is_never_suggested_as_a_journal(tmp_path: Path):
    report = _report(
        "author = {Jirlow, A.}, "
        "title = {Effective {Hamiltonian} for an off-resonantly driven qubit--cavity system}, "
        "year = {2025}"
    )
    client = _client(
        {
            "openalex:search:effective hamiltonian for an off resonantly driven qubit cavity system": (
                ARXIV_MINTED_DOI_PAYLOAD
            )
        },
        tmp_path,
    )
    verification = verify_entry(report, client)
    assert "journal" not in verification.suggestions
    assert verification.suggestions["doi"] == "10.48550/arxiv.2509.03375"


def test_title_search_falls_back_to_openalex(tmp_path: Path):
    report = _report(
        "author = {Cahill, K. E. and Glauber, R. J.}, "
        "title = {Density Operators and Quasiprobability Distributions}, "
        "journal = {Physical Review}, volume = {177}, year = {1969}"
    )
    client = _client(
        {"openalex:search:density operators and quasiprobability distributions": OPENALEX_PAYLOAD},
        tmp_path,
    )
    verification = verify_entry(report, client)
    assert verification.source == "openalex"
    assert verification.verdict == VERDICT_VERIFIED
    assert verification.suggestions["pages"] == "1882--1902"


COLLABORATION_PAYLOAD = {
    "message": {
        "DOI": "10.1038/s41586-024-08449-y",
        "title": ["Quantum error correction below the surface code threshold"],
        # Crossref really does deposit the collaboration name ahead of the named authors.
        "author": [
            {"name": "Google Quantum AI and Collaborators"},
            {"family": "Acharya", "given": "Rajeev"},
            {"family": "Abanin", "given": "Dmitry A."},
        ],
        "container-title": ["Nature"],
        "volume": "638",
        "page": "920-926",
        "issued": {"date-parts": [[2025]]},
    }
}

MATHML_PAYLOAD = {
    "message": {
        "DOI": "10.1103/physrevlett.126.220502",
        "title": [
            "Realization of High-Fidelity CZ and \n"
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML" display="inline">'
            "<mml:mi>Z</mml:mi><mml:mi>Z</mml:mi></mml:math>\n-Free iSWAP Gates with a Tunable Coupler"
        ],
        "author": [{"family": "Sung", "given": "Youngkyu"}],
        "container-title": ["Physical Review X"],
        "issued": {"date-parts": [[2021]]},
    }
}

DATACITE_PAYLOAD = {
    "data": {
        "attributes": {
            "doi": "10.5281/zenodo.4618153",
            "titles": [{"title": "Qiskit Metal: An Open-Source Framework for Quantum Device Design"}],
            "creators": [{"familyName": "Minev", "givenName": "Zlatko K."}],
            "publicationYear": 2021,
            "publisher": "Zenodo",
        }
    }
}


def test_collaboration_name_does_not_shadow_the_first_author(tmp_path: Path):
    """Crossref lists 'Google Quantum AI and Collaborators' first; Acharya is author[1]."""
    report = _report(
        "author = {Acharya, Rajeev and Abanin, Dmitry A.}, "
        "title = {Quantum error correction below the surface code threshold}, "
        "journal = {Nature}, volume = {638}, pages = {920--926}, year = {2025}, "
        "doi = {10.1038/s41586-024-08449-y}"
    )
    client = _client({"crossref:doi:10.1038/s41586-024-08449-y": COLLABORATION_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert "author" not in verification.mismatched_fields
    assert verification.verdict == VERDICT_VERIFIED


def test_mathml_in_a_crossref_title_is_not_a_mismatch(tmp_path: Path):
    report = _report(
        "author = {Sung, Youngkyu}, "
        "title = {Realization of High-Fidelity CZ and $ZZ$-Free iSWAP Gates with a Tunable Coupler}, "
        "journal = {Physical Review X}, year = {2021}, doi = {10.1103/PhysRevLett.126.220502}"
    )
    client = _client({"crossref:doi:10.1103/physrevlett.126.220502": MATHML_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert "title" not in verification.mismatched_fields


RELEASE_PAYLOAD = {
    "data": {
        "attributes": {
            "doi": "10.5281/zenodo.4618153",
            # Zenodo titles a software record after the GitHub release, not the software.
            "titles": [{"title": "qiskit-community/qiskit-metal: v0.7.0 - Lite-by-default install"}],
            "creators": [{"familyName": "Heinsoo", "givenName": "Johannes"}],
            "publicationYear": 2026,
            "publisher": "Zenodo",
        }
    }
}


def test_zenodo_doi_resolves_via_datacite(tmp_path: Path):
    report = _report(
        "author = {Minev, Zlatko K.}, title = {{Qiskit Metal}: An Open-Source Framework for Quantum Device Design}, "
        "year = {2021}, doi = {10.5281/zenodo.4618153}",
        "software",
    )
    client = _client({"datacite:doi:10.5281/zenodo.4618153": DATACITE_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert verification.source == "datacite"
    assert not any(f.code == "doi-unresolved" for f in verification.findings)


def test_zenodo_release_metadata_is_not_diffed_against_a_software_entry(tmp_path: Path):
    """A Zenodo release title and contributor roster cannot match a hand-written entry."""
    report = _report(
        "author = {Minev, Zlatko K.}, title = {{Qiskit Metal}: An Open-Source Framework for Quantum Device Design}, "
        "year = {2021}, doi = {10.5281/zenodo.4618153}",
        "software",
    )
    client = _client({"datacite:doi:10.5281/zenodo.4618153": RELEASE_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert verification.verdict == VERDICT_VERIFIED
    assert verification.mismatched_fields == []
    assert any(f.code == "repository-release" and f.level == "info" for f in verification.findings)


def test_an_article_with_a_zenodo_doi_is_still_compared(tmp_path: Path):
    report = _report(
        "author = {Minev, Zlatko K.}, title = {A completely different paper}, "
        "journal = {J}, year = {2021}, doi = {10.5281/zenodo.4618153}"
    )
    client = _client({"datacite:doi:10.5281/zenodo.4618153": DATACITE_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    assert verification.verdict == VERDICT_MISMATCHED
    assert "title" in verification.mismatched_fields


def test_unresolvable_repository_doi_is_a_warning_not_an_error(tmp_path: Path):
    report = _report("author = {A, B}, title = {T}, year = {2021}, doi = {10.5281/zenodo.9999999}")
    client = _client({}, tmp_path)
    verification = verify_entry(report, client)
    unresolved = [f for f in verification.findings if f.code == "doi-unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0].level == "warning"


def test_title_match_naming_a_different_author_is_discarded(tmp_path: Path):
    """A thesis whose DOI is absent must not silently match a different paper."""
    report = _report(
        "author = {Pechal, M.}, title = {Microwave photonics in superconducting circuits}, "
        "school = {ETH Zurich}, year = {2016}",
        "phdthesis",
    )
    client = _client(
        {
            "crossref:search:microwave photonics in superconducting circuits pechal 2016": {
                "message": {
                    "items": [
                        {
                            "DOI": "10.1109/ipcon.2012.6358734",
                            "title": ["Microwave photonics in superconducting circuits"],
                            "author": [{"family": "Nakamura", "given": "Y."}],
                            "issued": {"date-parts": [[2012]]},
                        }
                    ]
                }
            }
        },
        tmp_path,
    )
    verification = verify_entry(report, client)
    assert verification.verdict == VERDICT_NOT_FOUND
    assert not any(f.code == "field-mismatch" for f in verification.findings)


def test_nothing_found_is_reported_not_fabricated(tmp_path: Path):
    report = _report("author = {Nobody, N.}, title = {An entirely invented title}, year = {1999}")
    client = _client({}, tmp_path)
    verification = verify_entry(report, client)
    assert verification.verdict == VERDICT_NOT_FOUND
    assert any(f.code == "not-found" for f in verification.findings)


def test_unresolvable_doi_is_an_error(tmp_path: Path):
    report = _report("author = {A, B}, title = {T}, year = {2000}, doi = {10.9999/nope}")
    client = _client({}, tmp_path)
    verification = verify_entry(report, client)
    assert any(f.code == "doi-unresolved" and f.level == "error" for f in verification.findings)


# ---------------------------------------------------------------- fixing


def test_apply_suggestions_fills_gaps_and_expands_authors(tmp_path: Path):
    report = _report(
        "author = {Huang, K. and others}, "
        "title = {Fast Sideband Control of a Weakly Coupled Multimode Bosonic Memory}, "
        "year = {2025}, note = {arXiv:2503.10623}"
    )
    client = _client(
        {
            "arxiv:2503.10623": ARXIV_ATOM,
            "crossref:doi:10.1103/physrevx.15.021017": {
                "message": {
                    "DOI": "10.1103/PhysRevX.15.021017",
                    "title": ["Fast Sideband Control of a Weakly Coupled Multimode Bosonic Memory"],
                    "author": [{"family": "Huang", "given": "Kaiwen"}, {"family": "Ding", "given": "Andy"}],
                    "container-title": ["Physical Review X"],
                    "volume": "15",
                    "page": "021017",
                    "issued": {"date-parts": [[2025]]},
                }
            },
        },
        tmp_path,
    )
    verification = verify_entry(report, client)
    findings = apply_suggestions(report, verification)
    entry = report.entry
    assert entry.get("doi") == "10.1103/PhysRevX.15.021017"
    assert entry.get("journal") == "Physical Review X"
    assert entry.get("author") == "Huang, Kaiwen and Ding, Andy"
    assert all(finding.level == "info" for finding in findings)


def test_apply_suggestions_never_overwrites_a_present_field(tmp_path: Path):
    report = _report(
        "author = {Eickbusch, A.}, "
        "title = {Fast universal control of an oscillator with weak dispersive coupling to a qubit}, "
        "journal = {Nature Phys.}, volume = {18}, year = {2022}, doi = {10.1038/s41567-022-01776-9}"
    )
    client = _client({"crossref:doi:10.1038/s41567-022-01776-9": CROSSREF_PAYLOAD}, tmp_path)
    verification = verify_entry(report, client)
    apply_suggestions(report, verification)
    assert report.entry.get("journal") == "Nature Phys."
    assert report.entry.get("pages") == "1464--1469"  # was empty, so it was filled


def test_a_repository_is_never_suggested_as_a_journal_for_software(tmp_path: Path):
    """DataCite reports the publisher ('Zenodo') where a journal would go."""
    report = _report(
        "author = {Cucurachi, Daniele}, title = {{KQCircuits}}, year = {2021}, doi = {10.5281/zenodo.4944796}",
        "software",
    )
    payload = {
        "data": {
            "attributes": {
                "doi": "10.5281/zenodo.4944796",
                "titles": [{"title": "KQCircuits"}],
                "creators": [{"familyName": "Cucurachi", "givenName": "Daniele"}],
                "publicationYear": 2023,
                "publisher": "Zenodo",
            }
        }
    }
    client = _client({"datacite:doi:10.5281/zenodo.4944796": payload}, tmp_path)
    verification = verify_entry(report, client)
    assert "journal" not in verification.suggestions
