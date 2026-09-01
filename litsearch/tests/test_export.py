"""Export: the generated .bib must be something bibcheck itself can parse and accept."""

from __future__ import annotations

from bibcheck.parser import loads
from litsearch.export import build_bibtex, write_bibtex, write_evidence_csv
from litsearch.sources.base import Work

PAPER = Work(
    title="New material platform for superconducting transmon qubits",
    doi="10.1038/s41467-021-22030-5",
    year="2021",
    authors=["Alexander P. Place", "Lila V. H. Rodgers"],
    venue="Nature Communications",
    cited_by_count=541,
)

PREPRINT = Work(
    title="Millisecond coherence in a fluxonium qubit",
    arxiv_id="2103.08578",
    year="2021",
    authors=["Helin Somoroff"],
)


def test_generated_bibtex_parses_back():
    text = build_bibtex([PAPER, PREPRINT])
    database = loads(text)
    assert len(database.entries) == 2


def test_keys_are_rewritten_to_lastname_year():
    text = build_bibtex([PAPER])
    keys = [entry.key for entry in loads(text).entries]
    assert keys == ["Place2021"], f"expected bibcheck to re-key, got {keys}"


def test_preprint_becomes_an_eprint_entry():
    text = build_bibtex([PREPRINT])
    entry = loads(text).entries[0]
    assert entry.get("eprint") == "2103.08578"
    assert (entry.get("archivePrefix") or "").lower() == "arxiv"
    assert entry.get("journal") is None


def test_article_carries_journal_and_doi():
    entry = loads(build_bibtex([PAPER])).entries[0]
    assert entry.get("journal") == "Nature Communications"
    assert entry.get("doi") == "10.1038/s41467-021-22030-5"


def test_braces_in_a_title_cannot_unbalance_the_entry():
    nasty = Work(title="A title with {unbalanced braces and a \\command", doi="10.1/x",
                 year="2020", authors=["Ann Author"])
    database = loads(build_bibtex([nasty]))
    assert len(database.entries) == 1
    assert "unbalanced" in (database.entries[0].get("title") or "")


def test_colliding_authors_and_years_get_distinct_keys():
    a = Work(title="First paper on qubits", doi="10.1/a", year="2021", authors=["Ann Author"])
    b = Work(title="Second paper on qubits", doi="10.1/b", year="2021", authors=["Ann Author"])
    keys = [entry.key for entry in loads(build_bibtex([a, b])).entries]
    assert len(set(keys)) == 2, f"keys must not collide, got {keys}"


def test_write_bibtex_reports_findings(tmp_path):
    path = tmp_path / "refs.bib"
    count, findings = write_bibtex(path, [PAPER, PREPRINT])
    assert count == 2
    assert path.exists()
    assert isinstance(findings, list)
    # PAPER is complete, so it must not raise an error-level finding.
    errors = [f for f in findings if getattr(f, "level", "") == "error"]
    assert errors == [], f"complete entries should not error: {errors}"


def test_empty_corpus_produces_an_empty_bibliography(tmp_path):
    count, _ = write_bibtex(tmp_path / "refs.bib", [])
    assert count == 0


def test_evidence_rows_without_a_quote_are_refused(tmp_path):
    path = tmp_path / "evidence.csv"
    rows = [
        {"cite_key": "Place2021", "T1_us": "360", "source_quote": "T1 of 360 us was measured"},
        {"cite_key": "Ghost2021", "T1_us": "99999", "source_quote": ""},
        {"cite_key": "Ghost2022", "T1_us": "88888"},
    ]
    kept = write_evidence_csv(path, rows)
    assert kept == 1, "a value nobody can quote is not evidence"
    body = path.read_text(encoding="utf-8")
    assert "Place2021" in body
    assert "Ghost" not in body


def test_unicode_punctuation_is_folded_to_ascii():
    """Indexes emit U+2010 hyphens and curly quotes; BibTeX output must be ASCII."""
    work = Work(title="Qubit\u2010based \u201cdesign\u201d \u2013 an approach\u2026",
                doi="10.1/x", year="2020", authors=["Ann Author"])
    text = build_bibtex([work])
    offenders = sorted({ch for ch in text if ord(ch) > 127})
    assert offenders == [], f"non-ASCII punctuation leaked into the .bib: {offenders}"


def test_accents_become_latex_escapes_by_default():
    """Accents are real data, so they are converted rather than stripped.

    The default is ASCII output: a .bib full of raw UTF-8 trips cp1252 tooling on Windows.
    The accent survives as a LaTeX escape, which is what BibTeX wants anyway.
    """
    work = Work(title="A paper", doi="10.1/y", year="2020", authors=["Jos\u00e9 Garc\u00eda"])
    text = build_bibtex([work])
    assert sorted({ch for ch in text if ord(ch) > 127}) == [], "output must be pure ASCII"
    author = loads(text).entries[0].get("author") or ""
    assert "{\\'e}" in author and "{\\'i}" in author, f"accents must survive as escapes: {author}"


def test_ascii_only_can_be_turned_off():
    work = Work(title="A paper", doi="10.1/y", year="2020", authors=["Jos\u00e9 Garc\u00eda"])
    author = loads(build_bibtex([work], ascii_only=False)).entries[0].get("author") or ""
    assert "Jos\u00e9" in author


def test_volume_and_pages_reach_the_entry():
    work = Work(title="A paper", doi="10.1/z", year="2020", authors=["Ann Author"],
                venue="Nature", volume="12", pages="1779--1786")
    entry = loads(build_bibtex([work])).entries[0]
    assert entry.get("volume") == "12"
    assert entry.get("pages") == "1779--1786"


def test_preprints_get_no_volume_or_pages():
    work = Work(title="A preprint", arxiv_id="2101.00001", year="2021",
                authors=["Ann Author"], volume="12", pages="1--2")
    entry = loads(build_bibtex([work])).entries[0]
    assert entry.get("volume") is None
    assert entry.get("pages") is None
