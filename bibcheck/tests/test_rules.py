"""Tests for the normalisation and completeness rules.

Run:  python -m pytest lib/utils/bibcheck/tests/test_rules.py -q
"""

from __future__ import annotations

from pathlib import Path

from lib.utils.bibcheck.parser import loads, read
from lib.utils.bibcheck.rules import (
    check_database,
    check_duplicate_entries,
    check_duplicate_keys,
    check_entry,
    check_non_ascii,
    normalize_entry,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.bib"


def _entry(fields: str, entry_type: str = "article"):
    return loads("@%s{k, %s}" % (entry_type, fields)).entries[0]


def _codes(findings):
    return {finding.code for finding in findings}


# ------------------------------------------------------------- normalisation


def test_arxiv_id_is_moved_out_of_journal():
    entry = _entry("author = {A, B}, title = {T}, year = {2020}, journal = {arXiv:2004.14256}")
    findings = normalize_entry(entry)
    assert entry.get("eprint") == "2004.14256"
    assert entry.get("archivePrefix") == "arXiv"
    assert entry.get("journal") is None
    assert "arxiv-relocated" in _codes(findings)


def test_arxiv_id_is_moved_out_of_note_with_its_category():
    entry = _entry("author = {A, B}, title = {T}, year = {2025}, note = {arXiv:2509.03375 [quant-ph]}")
    normalize_entry(entry)
    assert entry.get("eprint") == "2509.03375"
    assert entry.get("primaryClass") == "quant-ph"
    assert entry.get("note") is None


def test_note_keeps_its_extra_prose_when_an_arxiv_id_is_copied_out():
    entry = _entry("author = {A, B}, title = {T}, year = {2025}, note = {Submitted; arXiv:2509.03375}")
    normalize_entry(entry)
    assert entry.get("eprint") == "2509.03375"
    assert entry.get("note") == "Submitted; arXiv:2509.03375"


def test_old_style_arxiv_identifiers_are_recognised():
    entry = _entry("author = {A, B}, title = {T}, year = {2005}, note = {arXiv:quant-ph/0510027}")
    normalize_entry(entry)
    assert entry.get("eprint") == "quant-ph/0510027"


def test_existing_eprint_gets_an_archive_prefix_and_loses_its_version():
    entry = _entry("author = {A, B}, title = {T}, year = {2025}, eprint = {arXiv:2508.18027v2}")
    normalize_entry(entry)
    assert entry.get("eprint") == "2508.18027"
    assert entry.get("archivePrefix") == "arXiv"


def test_doi_resolver_prefix_is_stripped():
    entry = _entry("author = {A, B}, title = {T}, year = {2005}, doi = {https://doi.org/10.1016/j.jmr.2004.11.004}")
    findings = normalize_entry(entry)
    assert entry.get("doi") == "10.1016/j.jmr.2004.11.004"
    assert "doi-prefix" in _codes(findings)


def test_doi_is_recovered_from_a_doi_org_url():
    entry = _entry("author = {A, B}, title = {T}, year = {2021}, url = {https://doi.org/10.5281/zenodo.4618153}")
    normalize_entry(entry)
    assert entry.get("doi") == "10.5281/zenodo.4618153"


def test_page_range_dash_is_normalised():
    entry = _entry("author = {A, B}, title = {T}, year = {2005}, pages = {296-305}")
    normalize_entry(entry)
    assert entry.get("pages") == "296--305"


def test_correct_page_range_is_left_alone():
    entry = _entry("author = {A, B}, title = {T}, year = {2005}, pages = {920--926}")
    assert normalize_entry(entry) == [] or entry.get("pages") == "920--926"


def test_entry_type_case_is_reported():
    entry = loads("@Article{k, author = {A, B}, title = {T}, year = {2000}}").entries[0]
    assert "entry-type-case" in _codes(normalize_entry(entry))


def test_wrapped_values_are_unwrapped():
    entry = _entry("author = {Jirlow, A. and\n   Abad, G.}, title = {T}, year = {2025}")
    normalize_entry(entry)
    assert "\n" not in entry.get("author")


# ---------------------------------------------------------------- completeness


def test_article_missing_required_field_is_an_error():
    report = check_entry(_entry("title = {T}, year = {2000}"))
    errors = [f for f in report.findings if f.level == "error"]
    assert any("author" in f.message for f in errors)


def test_article_missing_recommended_fields_are_warnings():
    report = check_entry(_entry("author = {A, B}, title = {T}, year = {2000}"))
    warnings = {f.message for f in report.findings if f.level == "warning"}
    assert any("journal" in message for message in warnings)
    assert any("doi" in message for message in warnings)


def test_preprint_is_not_warned_about_a_missing_journal():
    entry = _entry("author = {A, B}, title = {T}, year = {2025}, eprint = {2509.03375}")
    normalize_entry(entry)
    report = check_entry(entry)
    assert report.is_preprint
    missing = {f.message for f in report.findings if f.code == "missing-recommended"}
    assert missing == {"@article has no doi"}  # journal/volume/pages are not expected of a preprint
    assert "preprint" in _codes(report.findings)


def test_software_needs_a_locator():
    report = check_entry(_entry("author = {{Ansys}}, title = {T}, year = {2021}", "software"))
    assert "missing-field" in _codes(report.findings)
    ok = check_entry(_entry("author = {{Ansys}}, title = {T}, year = {2021}, url = {https://x}", "software"))
    assert "missing-field" not in _codes(ok.findings)


def test_phdthesis_requires_a_school():
    report = check_entry(_entry("author = {A, B}, title = {T}, year = {2025}", "phdthesis"))
    assert any("school" in f.message for f in report.findings if f.level == "error")


def test_a_thesis_with_a_doi_is_not_asked_for_a_url():
    """A repository DOI makes a thesis findable just as well as a link does."""
    with_doi = check_entry(
        _entry("author = {A, B}, title = {T}, school = {S}, year = {2016}, doi = {10.3929/ethz-a-1}", "phdthesis")
    )
    assert "missing-recommended" not in _codes(with_doi.findings)
    without = check_entry(_entry("author = {A, B}, title = {T}, school = {S}, year = {2016}", "phdthesis"))
    assert "missing-recommended" in _codes(without.findings)


def test_unknown_entry_type_is_flagged_but_not_checked():
    report = check_entry(_entry("title = {T}", "weirdtype"))
    assert _codes(report.findings) == {"unknown-type"}


def test_truncated_author_list_is_flagged():
    report = check_entry(_entry("author = {A, B and others}, title = {T}, year = {2000}, journal = {J}"))
    assert "truncated-authors" in _codes(report.findings)


def test_malformed_doi_is_an_error():
    report = check_entry(_entry("author = {A, B}, title = {T}, year = {2000}, doi = {not-a-doi}"))
    assert "bad-doi" in _codes(report.findings)


def test_non_numeric_year_is_reported():
    report = check_entry(_entry("author = {A, B}, title = {T}, year = {in press}"))
    assert "bad-year" in _codes(report.findings)


def test_editing_placeholder_is_an_error():
    report = check_entry(
        _entry("author = {{TO VERIFY: author list not confirmed}}, title = {T}, year = {2025}, journal = {J}")
    )
    placeholders = [f for f in report.findings if f.code == "placeholder"]
    assert len(placeholders) == 1
    assert placeholders[0].level == "error"
    assert "author" in placeholders[0].message


def test_placeholder_author_does_not_produce_a_nonsense_key():
    from lib.utils.bibcheck.keys import make_key

    entry = _entry("author = {{TO VERIFY: author list not confirmed}}, title = {T}, year = {2025}")
    key, problem = make_key(entry)
    assert key == ""  # would otherwise become 'TO2025'
    assert problem


def test_placeholder_does_not_also_raise_bad_author():
    report = check_entry(_entry("author = {{TBD}}, title = {T}, year = {2025}, journal = {J}"))
    assert "bad-author" not in _codes(report.findings)
    assert "placeholder" in _codes(report.findings)


def test_unbraced_organisation_author_is_flagged():
    """'Sonnet Software' unbraced makes BibTeX treat 'Software' as the surname."""
    report = check_entry(
        _entry("author = {Sonnet Software}, title = {Sonnet}, year = {2025}, url = {https://x}", "software")
    )
    assert "unbraced-organisation" in _codes(report.findings)


def test_braced_organisation_author_is_not_flagged():
    report = check_entry(
        _entry("author = {{Sonnet Software}}, title = {Sonnet}, year = {2025}, url = {https://x}", "software")
    )
    assert "unbraced-organisation" not in _codes(report.findings)


def test_ordinary_two_word_name_is_not_flagged_as_an_organisation():
    report = check_entry(_entry("author = {Thomas Fosel}, title = {T}, year = {2020}, journal = {J}"))
    assert "unbraced-organisation" not in _codes(report.findings)


def test_url_is_exempt_from_placeholder_scanning():
    report = check_entry(
        _entry("author = {A, B}, title = {T}, year = {2000}, journal = {J}, url = {https://x.org/?id=xxx}")
    )
    assert "placeholder" not in _codes(report.findings)


def test_redundant_url_is_informational():
    report = check_entry(
        _entry("author = {A, B}, title = {T}, year = {2000}, doi = {10.1/x}, url = {https://doi.org/10.1/x}")
    )
    assert "redundant-url" in _codes(report.findings)


# -------------------------------------------------------------- file level


def test_non_ascii_is_reported_with_a_code_point():
    db = loads("@article{k, author = {Fösel, T.}, title = {T}, year = {2020}}")
    findings = check_non_ascii(db)
    assert len(findings) == 1
    assert "U+00F6" in findings[0].message


def test_duplicate_keys_are_reported():
    db = loads("@article{k, title = {A}, year = {2000}}\n@article{k, title = {B}, year = {2001}}")
    assert len(check_duplicate_keys(db.entries)) == 1


def test_duplicate_entries_are_found_by_doi():
    db = loads(
        "@article{a, author = {X, Y}, title = {A}, year = {2000}, doi = {10.1/x}}\n"
        "@article{b, author = {X, Y}, title = {Different title}, year = {2000}, doi = {10.1/X}}"
    )
    findings = check_duplicate_entries(db.entries)
    assert len(findings) == 1
    assert "same doi" in findings[0].message


def test_duplicate_entries_are_found_by_author_year_title():
    db = loads(
        "@article{a, author = {Cahill, K.}, title = {Density Operators}, year = {1969}}\n"
        "@article{b, author = {Cahill, K. E.}, title = {Density {Operators}}, year = {1969}}"
    )
    findings = check_duplicate_entries(db.entries)
    assert len(findings) == 1
    assert "author/year/title" in findings[0].message


def test_distinct_works_are_not_reported_as_duplicates():
    db = read(FIXTURE)
    assert check_duplicate_entries(db.entries) == []
    assert check_duplicate_keys(db.entries) == []


def test_check_database_normalises_and_checks_the_fixture():
    db = read(FIXTURE)
    reports, file_findings = check_database(db)
    assert len(reports) == 7
    by_key = {report.entry.key: report for report in reports}
    assert by_key["fosel2020efficient"].entry.get("eprint") == "2004.14256"
    assert by_key["Khaneja_2005_GRAPE"].entry.get("doi") == "10.1016/j.jmr.2004.11.004"
    assert by_key["Khaneja_2005_GRAPE"].entry.get("pages") == "296--305"
    assert by_key["aaa_2021_metal"].entry.get("doi") == "10.5281/zenodo.4618153"
    assert "truncated-authors" in _codes(by_key["acharya_2025_threshold"].findings)
    assert file_findings == []


# ----------------------------------------------------------------- ascii mode


def test_ascii_mode_rewrites_field_values_as_latex_escapes():
    entry = _entry(
        "author = {S\u00e9pulcre, Th\u00e9o}, title = {T}, year = {2025}, school = {ETH Z\u00fcrich}", "phdthesis"
    )
    findings = normalize_entry(entry, ascii_only=True)
    assert entry.get("author") == r"S{\'e}pulcre, Th{\'e}o"
    assert entry.get("school") == r"ETH Z{\"u}rich"
    assert "ascii" in _codes(findings)


def test_ascii_mode_is_off_by_default():
    entry = _entry("author = {S\u00e9pulcre, Th\u00e9o}, title = {T}, year = {2025}")
    normalize_entry(entry)
    assert entry.get("author") == "S\u00e9pulcre, Th\u00e9o"


def test_ascii_mode_clears_the_non_ascii_warning_end_to_end():
    db = loads("@article{k, author = {S\u00e9pulcre, Th\u00e9o}, title = {T}, journal = {J}, year = {2025}}")
    check_database(db, ascii_only=True)
    from lib.utils.bibcheck.parser import dumps

    assert dumps(db).isascii()


def test_misc_without_a_locator_is_a_warning_not_an_error():
    """A private communication has nothing to link to; BibTeX's @misc requires nothing."""
    report = check_entry(_entry("author = {S, T}, title = {Private communication}, year = {2025}", "misc"))
    assert "missing-field" not in _codes(report.findings)
    advisory = [f for f in report.findings if f.code == "missing-recommended"]
    assert advisory and all(f.level == "warning" for f in advisory)


def test_software_without_a_locator_is_still_an_error():
    report = check_entry(_entry("author = {{Ansys}}, title = {T}, year = {2021}", "software"))
    assert "missing-field" in _codes(report.findings)


# ------------------------------------------------------------------ eprint misuse


def test_publisher_pdf_link_in_eprint_is_moved_to_url():
    """A pasted PDF link in 'eprint' made the entry look like a preprint."""
    entry = _entry(
        "author = {A, B}, title = {T}, journal = {J}, year = {2017}, "
        "eprint = {https://pubs.aip.org/aip/apl/article-pdf/doi/10.1063/1.4984142/x.pdf}"
    )
    findings = normalize_entry(entry)
    assert entry.get("eprint") is None
    assert entry.get("archivePrefix") is None
    assert entry.get("url").startswith("https://pubs.aip.org/")
    assert "bad-eprint" in _codes(findings)


def test_publisher_pdf_link_in_eprint_is_dropped_when_a_doi_exists():
    entry = _entry(
        "author = {A, B}, title = {T}, journal = {J}, year = {2017}, doi = {10.1063/1.4984142}, "
        "eprint = {https://pubs.aip.org/aip/apl/article-pdf/doi/10.1063/1.4984142/x.pdf}"
    )
    normalize_entry(entry)
    assert entry.get("eprint") is None
    assert entry.get("url") is None


def test_such_an_entry_is_no_longer_treated_as_a_preprint():
    entry = _entry(
        "author = {A, B}, title = {T}, journal = {J}, volume = {1}, pages = {1}, year = {2017}, "
        "doi = {10.1063/1.4984142}, eprint = {https://www.pnas.org/doi/pdf/10.1073/pnas.2221736120}"
    )
    normalize_entry(entry)
    report = check_entry(entry)
    assert not report.is_preprint
    assert "preprint" not in _codes(report.findings)


def test_non_url_junk_in_eprint_is_reported_but_left_alone():
    entry = _entry("author = {A, B}, title = {T}, journal = {J}, year = {2017}, eprint = {see the website}")
    findings = normalize_entry(entry)
    assert entry.get("eprint") == "see the website"
    assert any(f.code == "bad-eprint" and f.level == "warning" for f in findings)


# ------------------------------------------------------------- non-ascii levels


def test_zero_width_characters_are_reported_as_removed():
    db = loads("\ufeff@article{k, author = {A, B}, title = {T}, journal = {J}, year = {2000}}")
    findings = check_non_ascii(db)
    assert [f.code for f in findings] == ["zero-width"]
    assert findings[0].level == "info"


def test_non_ascii_is_a_warning_without_ascii_mode_and_info_with_it():
    db = loads("@article{k, author = {F\u00f6sel, T.}, title = {T}, journal = {J}, year = {2000}}")
    assert check_non_ascii(db)[0].level == "warning"
    assert check_non_ascii(db, ascii_only=True)[0].level == "info"
