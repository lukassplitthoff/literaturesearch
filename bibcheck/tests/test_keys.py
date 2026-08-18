"""Tests for LastnameYEAR key generation and ASCII folding.

Run:  python -m pytest lib/utils/bibcheck/tests/test_keys.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.utils.bibcheck.keys import (
    assign_keys,
    entry_year,
    first_author_surname,
    fold_latex_accents,
    latexify,
    make_key,
    normalize_title,
    split_authors,
    strip_latex,
)
from lib.utils.bibcheck.parser import loads, read

FIXTURE = Path(__file__).parent / "fixtures" / "sample.bib"


def _entry(fields: str, entry_type: str = "article"):
    return loads("@%s{k, %s}" % (entry_type, fields)).entries[0]


# ------------------------------------------------------------------- folding


@pytest.mark.parametrize(
    "text, expected",
    [
        (r"F\"osel", "Fosel"),
        (r"Schulte-Herbr\"{u}ggen", "SchulteHerbruggen"),
        (r"{\'E}lie", "Elie"),
        (r"Lo{\"i}ck", "Loick"),
        (r"Mandr{\`a}", "Mandra"),
        (r"Wei{\ss}", "Weiss"),
        (r"Gr{\o}nbech", "Gronbech"),
        (r"{\v S}tefan", "Stefan"),
        ("Cahill", "Cahill"),
    ],
)
def test_fold_latex_accents(text, expected):
    assert fold_latex_accents(text) == expected


def test_folding_handles_real_unicode_the_same_way():
    assert fold_latex_accents("Fösel") == "Fosel"
    assert fold_latex_accents("Mandrà") == "Mandra"


def test_strip_latex_keeps_words_and_drops_markup():
    assert strip_latex(r"Effective {Hamiltonian} for an off-resonantly driven qubit--cavity system") == (
        "Effective Hamiltonian for an off-resonantly driven qubit--cavity system"
    )


def test_normalize_title_is_comparison_ready():
    assert normalize_title(r"Floquet-engineered fast {SNAP} gates in circuit-{QED}") == (
        "floquet engineered fast snap gates in circuit qed"
    )


def test_ligature_does_not_eat_a_longer_command():
    # \l must not match the start of \ldots and leave a stray 'ldots' in the text.
    assert strip_latex(r"A\ldots B") == "A B"


# ------------------------------------------------------------------ surnames


@pytest.mark.parametrize(
    "author, expected",
    [
        ("Cahill, K. E. and Glauber, R. J.", "Cahill"),
        ("Thomas Fosel and Florian Marquardt", "Fosel"),
        (r"F\"osel, Thomas", "Fosel"),
        ("{Ansys}", "Ansys"),
        ("{Sonnet Software}", "Sonnet"),
        ("{CSC}", "CSC"),
        ("{GDSfactory}", "GDSfactory"),
        (r"Le Guevel, Lo{\"i}ck", "LeGuevel"),
        ("Anne van der Sar and Someone Else", "vanderSar"),
        ("others", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_first_author_surname(author, expected):
    assert first_author_surname(author) == expected


def test_split_authors_respects_brace_depth():
    assert split_authors("{Smith and Sons} and Doe, J.") == ["{Smith and Sons}", "Doe, J."]


def test_split_authors_handles_wrapped_lists():
    db = read(FIXTURE)
    jirlow = next(e for e in db.entries if e.key == "Jirlow_2025_lateRWA")
    assert len(split_authors(jirlow.get("author"))) == 5


# ----------------------------------------------------------------- key build


def test_make_key_basic():
    entry = _entry("author = {Cahill, K. E.}, title = {T}, year = {1969}")
    assert make_key(entry) == ("Cahill1969", "")


def test_make_key_corporate_author_keeps_its_capitalisation():
    entry = _entry("author = {{GDSfactory}}, title = {T}, year = 2025", "software")
    assert make_key(entry)[0] == "GDSfactory2025"


def test_make_key_reports_missing_year():
    entry = _entry("author = {Cahill, K. E.}, title = {T}")
    key, problem = make_key(entry)
    assert key == ""
    assert "year" in problem


def test_make_key_reports_unparseable_author():
    entry = _entry("title = {T}, year = {1969}")
    key, problem = make_key(entry)
    assert key == ""
    assert "surname" in problem


def test_entry_year_tolerates_decoration():
    assert entry_year(_entry("year = {2021 (published 2022)}")) == "2021"
    assert entry_year(_entry("year = {n.d.}")) == ""


# -------------------------------------------------------------- collisions


def test_every_member_of_a_collision_group_is_suffixed():
    db = read(FIXTURE)
    assign_keys(db.entries)
    keys = {entry.key: entry.new_key for entry in db.entries}
    assert keys["Jirlow_2025_lateRWA"] in ("Jirlow2025a", "Jirlow2025b")
    assert keys["jirlow_2025_thesis"] in ("Jirlow2025a", "Jirlow2025b")
    assert keys["Jirlow_2025_lateRWA"] != keys["jirlow_2025_thesis"]
    assert "Jirlow2025" not in keys.values()


def test_non_colliding_keys_have_no_suffix():
    db = read(FIXTURE)
    assign_keys(db.entries)
    keys = {entry.key: entry.new_key for entry in db.entries}
    assert keys["Khaneja_2005_GRAPE"] == "Khaneja2005"
    assert keys["acharya_2025_threshold"] == "Acharya2025"
    assert keys["zzz_2021_ansys"] == "Ansys2021"
    assert keys["fosel2020efficient"] == "Fosel2020"


def test_collision_suffixes_are_independent_of_source_order():
    db_forward = read(FIXTURE)
    db_reverse = read(FIXTURE)
    db_reverse.nodes = list(reversed(db_reverse.nodes))
    assign_keys(db_forward.entries)
    assign_keys(db_reverse.entries)
    forward = {entry.key: entry.new_key for entry in db_forward.entries}
    reverse = {entry.key: entry.new_key for entry in db_reverse.entries}
    assert forward == reverse


def test_rename_map_omits_unchanged_keys_and_problems_are_returned():
    db = loads("@article{Cahill1969, author = {Cahill, K.}, title = {T}, year = {1969}}\n@misc{x, title = {T2}}")
    rename_map, problems = assign_keys(db.entries)
    assert rename_map == {}
    assert len(problems) == 1
    assert problems[0][0].key == "x"
    assert db.entries[1].new_key == "x"


# ------------------------------------------------------------------ latexify


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Th\u00e9o S\u00e9pulcre", r"Th{\'e}o S{\'e}pulcre"),
        ("ETH Z\u00fcrich", r"ETH Z{\"u}rich"),
        ("Fern\u00e1ndez-Pend\u00e1s", r"Fern{\'a}ndez-Pend{\'a}s"),
        ("Janzs\u00f3, D\u00e1vid", r"Janzs{\'o}, D{\'a}vid"),
        ("Myll\u00e4ri", r"Myll{\"a}ri"),
        ("Wei\u00df", r"Wei{\ss}"),
        ("Gr\u00f8nbech", r"Gr{\o}nbech"),
        ("\u0160tefan", r"{\v{S}}tefan"),  # a letter command must brace its argument
        ("plain ascii", "plain ascii"),
        ("1882\u20131902", "1882--1902"),
    ],
)
def test_latexify(text, expected):
    assert latexify(text) == expected


def test_latexify_output_is_ascii_and_folds_back_to_the_original_letters():
    for text in ("Th\u00e9o S\u00e9pulcre", "ETH Z\u00fcrich", "Gr\u00f8nbech", "Wei\u00df"):
        converted = latexify(text)
        assert converted.isascii()
        assert fold_latex_accents(converted) == fold_latex_accents(text)


def test_latexify_leaves_unknown_characters_alone_rather_than_dropping_them():
    assert "\u4e2d" in latexify("a \u4e2d b")
