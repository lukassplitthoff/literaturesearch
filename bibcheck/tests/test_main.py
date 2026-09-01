"""End-to-end tests for the CLI.

Run:  python -m pytest bibcheck/tests/test_main.py -q

These run entirely offline: --verify is never passed, so no socket is opened.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from bibcheck.main import main
from bibcheck.parser import read

FIXTURE = Path(__file__).parent / "fixtures" / "sample.bib"


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "refs.bib"
    shutil.copy(FIXTURE, target)
    return target


def test_no_write_leaves_the_directory_untouched(tmp_path: Path, capsys):
    source = _copy_fixture(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())
    code = main([str(source), "--no-write"])
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert code in (1, 2)  # the fixture deliberately contains warnings
    assert "keys renamed" in capsys.readouterr().out


def test_writes_all_four_outputs(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    main([str(source)])
    for name in (
        "refs_checked.bib",
        "refs_bibcheck_report.md",
        "refs_bibcheck_report.json",
        "refs_refs_plaintext.txt",
    ):
        assert (tmp_path / name).is_file(), name
    assert source.read_text(encoding="utf-8") == FIXTURE.read_text(encoding="utf-8")


def test_refuses_to_overwrite_the_input(tmp_path: Path, capsys):
    source = _copy_fixture(tmp_path)
    code = main([str(source), "--out", str(source)])
    assert code == 2
    assert "refusing to overwrite the input" in capsys.readouterr().out
    assert source.read_text(encoding="utf-8") == FIXTURE.read_text(encoding="utf-8")


def test_refuses_to_clobber_existing_outputs_without_force(tmp_path: Path, capsys):
    source = _copy_fixture(tmp_path)
    assert main([str(source)]) in (1, 2)
    marker = tmp_path / "refs_checked.bib"
    marker.write_text("hand edited\n", encoding="utf-8")
    assert main([str(source)]) == 2
    assert "pass --force" in capsys.readouterr().out
    assert marker.read_text(encoding="utf-8") == "hand edited\n"
    main([str(source), "--force"])
    assert marker.read_text(encoding="utf-8") != "hand edited\n"


def test_output_is_rekeyed_sorted_and_normalised(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    main([str(source), "--out-dir", str(tmp_path / "out")])
    db = read(tmp_path / "out" / "refs_checked.bib")
    keys = [entry.key for entry in db.entries]
    assert keys == ["Ansys2021", "Minev2021", "Acharya2025", "Fosel2020", "Jirlow2025a", "Jirlow2025b", "Khaneja2005"]
    fosel = next(entry for entry in db.entries if entry.key == "Fosel2020")
    assert fosel.get("eprint") == "2004.14256"
    assert fosel.get("journal") is None
    assert "@Article{" not in (tmp_path / "out" / "refs_checked.bib").read_text(encoding="utf-8")


def test_running_the_tool_on_its_own_output_is_a_no_op(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    main([str(source), "--out-dir", str(tmp_path / "first")])
    first = tmp_path / "first" / "refs_checked.bib"
    main([str(first), "--out-dir", str(tmp_path / "second")])
    second = tmp_path / "second" / "refs_checked_checked.bib"
    assert second.read_text(encoding="utf-8") == first.read_text(encoding="utf-8")


def test_json_report_carries_the_rename_map(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    main([str(source)])
    payload = json.loads((tmp_path / "refs_bibcheck_report.json").read_text(encoding="utf-8"))
    assert payload["rename_map"]["Khaneja_2005_GRAPE"] == "Khaneja2005"
    assert payload["entry_count"] == 7
    assert payload["counts"]["error"] == 0


def test_plaintext_export_is_ascii_and_numbered(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    main([str(source)])
    text = (tmp_path / "refs_refs_plaintext.txt").read_text(encoding="ascii")
    assert text.startswith("[1] ")
    assert "Acharya, Rajeev et al. Quantum error correction below the surface code threshold." in text
    assert "Nature 638, 920-926 (2025)." in text
    assert "https://doi.org/10.1038/s41586-024-08449-y" in text
    assert "arXiv:2004.14256" in text
    assert ".." not in text


def test_strict_promotes_warnings_to_a_failing_exit_code(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lenient = main([str(source), "--no-write"])
    strict = main([str(source), "--no-write", "--strict"])
    assert lenient == 1
    assert strict == 2


def test_missing_input_is_reported(tmp_path: Path, capsys):
    assert main([str(tmp_path / "nope.bib")]) == 2
    assert "no such file" in capsys.readouterr().out


def test_global_sort_flattens_the_sections(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    main([str(source), "--sort", "global", "--out-dir", str(tmp_path / "flat")])
    text = (tmp_path / "flat" / "refs_checked.bib").read_text(encoding="utf-8")
    heads = [line for line in text.splitlines() if line.startswith("@")]
    assert heads == sorted(heads, key=lambda line: line.split("{", 1)[1].lower())


def test_fix_from_index_without_verify_is_a_no_op_with_a_note(tmp_path: Path, capsys):
    source = _copy_fixture(tmp_path)
    main([str(source), "--no-write", "--fix-from-index"])
    assert "has no effect without --verify" in capsys.readouterr().out
