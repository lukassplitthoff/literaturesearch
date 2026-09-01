"""Stage 7: turn validated works into a bibliography that bibcheck itself accepts.

The output is not merely written -- it is round-tripped through bibcheck's own parser,
key assignment and completeness rules before it lands on disk. If bibcheck cannot parse
what we generated, that is a bug here and the caller is told, rather than a broken .bib
being handed over.
"""

from __future__ import annotations

import csv
from pathlib import Path

from bibcheck.keys import assign_keys
from bibcheck.parser import dumps, loads
from bibcheck.rules import check_database, normalize_entry
from litsearch.sources.base import Work

# Braces and backslashes would unbalance an entry, so they go. The rest folds Unicode
# punctuation that the indexes emit -- OpenAlex titles routinely carry U+2010 hyphens and
# curly quotes -- into the ASCII BibTeX spells the same idea with. Accented *letters* are
# left alone: they are real bibliographic data, and bibcheck's --ascii converts them to
# LaTeX escapes if the user wants that.
_CHAR_TABLE = str.maketrans(
    {
        "{": "",
        "}": "",
        "\\": "",
        " ": " ",  # no-break space
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "--",  # figure dash
        "–": "--",  # en dash
        "—": "---",  # em dash
        "−": "-",  # minus sign
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
    }
)


def _clean(value: str) -> str:
    """Make a value safe to sit inside {...} and free of Unicode punctuation."""
    return " ".join(str(value or "").translate(_CHAR_TABLE).split())


def _provisional_key(work: Work, index: int) -> str:
    """A unique placeholder key; bibcheck reassigns these to LastnameYEAR."""
    surname = ""
    if work.authors:
        surname = _clean(work.authors[0]).split()[-1] if _clean(work.authors[0]) else ""
    surname = "".join(ch for ch in surname if ch.isalpha()) or "Anon"
    return f"{surname}{work.year or '0000'}x{index}"


def work_to_entry_text(work: Work, index: int) -> str:
    """Render one Work as BibTeX source."""
    is_preprint = bool(work.arxiv_id) and not work.venue
    entry_type = "misc" if is_preprint else "article"

    fields: list[tuple[str, str]] = []
    if work.authors:
        fields.append(("author", " and ".join(_clean(a) for a in work.authors if a)))
    if work.title:
        fields.append(("title", _clean(work.title)))
    if work.venue and not is_preprint:
        fields.append(("journal", _clean(work.venue)))
    if work.volume and not is_preprint:
        fields.append(("volume", _clean(work.volume)))
    if work.pages and not is_preprint:
        fields.append(("pages", _clean(work.pages)))
    if work.year:
        fields.append(("year", _clean(work.year)))
    if work.doi:
        fields.append(("doi", _clean(work.doi)))
    if work.arxiv_id:
        fields.append(("eprint", _clean(work.arxiv_id)))
        fields.append(("archivePrefix", "arXiv"))
    if work.oa_pdf_url and not work.doi:
        fields.append(("url", str(work.oa_pdf_url).strip()))

    lines = [f"@{entry_type}{{{_provisional_key(work, index)},"]
    for name, value in fields:
        lines.append(f"    {name} = {{{value}}},")
    lines.append("}")
    return "\n".join(lines)


def build_bibtex(works: list[Work], ascii_only: bool = True) -> str:
    """Render every work, then let bibcheck normalise, re-key and sort the result.

    ``ascii_only`` rewrites accented characters as LaTeX escapes ({'e} and friends). It
    defaults on because a .bib full of raw UTF-8 trips cp1252 tooling on Windows, which is
    what bibcheck's non-ascii warning is for -- 165 of them on the first real run.
    """
    blocks = [work_to_entry_text(work, index) for index, work in enumerate(works, start=1)]
    database = loads("\n\n".join(blocks) + "\n")
    for entry in database.entries:
        normalize_entry(entry, ascii_only=ascii_only)
    assign_keys(database.entries)
    for entry in database.entries:
        if entry.new_key:
            entry.key = entry.new_key
            entry.new_key = None
    return dumps(database, sort="global")


def write_bibtex(path: Path, works: list[Work], ascii_only: bool = True) -> tuple[int, list]:
    """Write refs.bib. Returns (entry count, bibcheck findings on the result)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_bibtex(works, ascii_only=ascii_only)
    path.write_text(text, encoding="utf-8")

    # Re-read what we just wrote and check it the way a user would. check_database
    # returns (per-entry reports, file-level findings); both matter here.
    database = loads(text, path=path)
    reports, file_findings = check_database(database, ascii_only=ascii_only)
    findings = list(file_findings)
    for report in reports:
        findings.extend(report.findings)
    return len(database.entries), findings


EVIDENCE_COLUMNS = (
    "cite_key",
    "doi",
    "year",
    "qubit_type",
    "material",
    "substrate",
    "T1_us",
    "T2_star_us",
    "T2_echo_us",
    "temperature_mK",
    "source_quote",
    "confidence",
)


def write_evidence_csv(path: Path, rows: list[dict], columns: tuple[str, ...] = EVIDENCE_COLUMNS) -> int:
    """Write the extraction table. Rows without a source_quote are refused.

    A value nobody can quote is not evidence. Dropping those here means the guarantee
    holds no matter how stage 6 behaved.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [row for row in rows if str(row.get("source_quote", "")).strip()]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in kept:
            writer.writerow(row)
    return len(kept)
