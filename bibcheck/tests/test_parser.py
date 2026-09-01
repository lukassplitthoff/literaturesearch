"""Tests for the BibTeX reader/writer.

Run:  python -m pytest bibcheck/tests/test_parser.py -q

The point of these tests is fidelity: a value that goes in must come out byte for byte,
because the group's bibliographies carry LaTeX escapes and brace-protected proper nouns
that a normalising parser would silently destroy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibcheck.parser import BibParseError, CommentBlock, dumps, loads, read

FIXTURE = Path(__file__).parent / "fixtures" / "sample.bib"


def _values(db):
    return {entry.key: {name.lower(): value.text for name, value in entry.fields.items()} for entry in db.entries}


# --------------------------------------------------------------------- basics


def test_reads_all_entries():
    db = read(FIXTURE)
    keys = [entry.key for entry in db.entries]
    assert keys == [
        "zzz_2021_ansys",
        "aaa_2021_metal",
        "Khaneja_2005_GRAPE",
        "acharya_2025_threshold",
        "fosel2020efficient",
        "Jirlow_2025_lateRWA",
        "jirlow_2025_thesis",
    ]


def test_entry_type_case_is_recorded_and_lowered():
    db = read(FIXTURE)
    entry = next(e for e in db.entries if e.key == "aaa_2021_metal")
    assert entry.type_raw == "Software"
    assert entry.type == "software"


def test_bare_and_braced_values_keep_their_delimiter():
    db = read(FIXTURE)
    ansys = next(e for e in db.entries if e.key == "zzz_2021_ansys")
    assert ansys.get_value("year").delim == "bare"
    assert ansys.get_value("year").text == "2021"
    metal = next(e for e in db.entries if e.key == "aaa_2021_metal")
    assert metal.get_value("month").delim == "bare"
    assert metal.get_value("year").delim == "brace"


def test_nested_braces_and_latex_escapes_are_preserved_verbatim():
    db = read(FIXTURE)
    values = _values(db)
    assert values["zzz_2021_ansys"]["author"] == "{Ansys}"
    assert values["aaa_2021_metal"]["title"] == "{Qiskit Metal: An Open-Source Framework {\\&} Analysis}"
    assert values["Khaneja_2005_GRAPE"]["author"].endswith('Schulte-Herbr\\"{u}ggen, T. and Glaser, S. J.')
    assert "{\\'E}lie" in values["acharya_2025_threshold"]["author"]
    assert 'Lo{\\"i}ck' in values["acharya_2025_threshold"]["author"]


def test_trailing_comma_before_closing_brace_is_tolerated():
    db = read(FIXTURE)
    acharya = next(e for e in db.entries if e.key == "acharya_2025_threshold")
    assert set(acharya.fields) == {"author", "title", "journal", "volume", "pages", "year", "doi"}


def test_quoted_values():
    db = loads('@article{k, title = "A {quoted} title", year = 1999}')
    entry = db.entries[0]
    assert entry.get_value("title").delim == "quote"
    assert entry.get("title") == "A {quoted} title"


def test_raw_entry_types_are_preserved_verbatim():
    source = (
        "@Comment{jabref-meta: databaseType:bibtex;}\n\n@article{k, title = {T}, author = {A, B}, year = {2000}}\n"
    )
    db = loads(source)
    assert len(db.entries) == 1
    assert len(db.raw_nodes) == 1
    assert db.raw_nodes[0].text == "@Comment{jabref-meta: databaseType:bibtex;}"


def test_unbalanced_braces_raise():
    with pytest.raises(BibParseError):
        loads("@article{k, title = {unterminated }")


# ------------------------------------------------------------------ comments


def test_banner_blocks_open_sections_and_notes_stay_with_their_entry():
    db = read(FIXTURE)
    sections = db.sections()
    assert [len(section.entries) for section in sections] == [2, 5]
    assert sections[0].banner.is_banner
    acharya = next(e for e in db.entries if e.key == "acharya_2025_threshold")
    assert acharya.lead_comment is not None
    assert "surface-code paper" in acharya.lead_comment.text


def test_short_comment_is_not_a_banner():
    assert not CommentBlock("% a short note").is_banner
    assert CommentBlock("%" * 40).is_banner


# ------------------------------------------------------------------- writing


def test_round_trip_preserves_every_field_value():
    original = read(FIXTURE)
    rewritten = loads(dumps(original, sort="none"))
    assert _values(original) == _values(rewritten)
    assert [e.key for e in original.entries] == [e.key for e in rewritten.entries]


def test_writer_is_idempotent():
    once = dumps(read(FIXTURE))
    twice = dumps(loads(once))
    assert once == twice


def test_writer_lowercases_entry_types_and_drops_trailing_commas():
    text = dumps(read(FIXTURE))
    assert "@Software{" not in text
    assert "@Article{" not in text
    assert ",\n}" not in text


def test_sections_sort_preserves_banners_and_sorts_within_them(tmp_path: Path):
    db = read(FIXTURE)
    for entry in db.entries:
        entry.new_key = entry.key  # sort on the source keys
    text = dumps(db, sort="sections")
    assert text.count("% software") == 1
    assert text.count("% articles / preprints / theses") == 1
    software_block, article_block = text.split("% articles / preprints / theses")
    assert software_block.index("@software{aaa_2021_metal") < software_block.index("@software{zzz_2021_ansys")
    assert article_block.index("@article{Jirlow_2025_lateRWA") < article_block.index("@article{Khaneja_2005_GRAPE")


def test_global_sort_hoists_banners_and_flattens():
    db = read(FIXTURE)
    text = dumps(db, sort="global")
    first_entry = text.index("@")
    assert text.index("% software") < first_entry or text.index("% software") < text.index("@software")
    keys = [line for line in text.splitlines() if line.startswith("@")]
    assert keys == sorted(keys, key=lambda line: line.split("{", 1)[1].lower())


def test_write_produces_a_readable_file(tmp_path: Path):
    from bibcheck.parser import write

    out = write(read(FIXTURE), tmp_path / "out.bib")
    assert _values(read(out)) == _values(read(FIXTURE))


# ---------------------------------------------------------------- zero width


def test_zero_width_characters_never_reach_the_output():
    """A BOM pasted before an entry used to survive as a one-character comment block."""
    source = "\ufeff@Article{k, title = {T}, author = {A, B}, year = {2000}}\n\n\ufeff@article{j, title = {U}, author = {C, D}, year = {2001}}\n"
    db = loads(source)
    assert len(db.entries) == 2
    text = dumps(db)
    assert "\ufeff" not in text
    assert not any(line.strip() == "" for line in text.splitlines()[:1])


def test_the_raw_source_is_kept_so_non_ascii_is_still_reported():
    db = loads("\ufeff@article{k, title = {T}, author = {A, B}, year = {2000}}")
    assert "\ufeff" in db.source
