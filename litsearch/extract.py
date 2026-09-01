"""Stage 6: evidence extraction.

Same shape as screening: this package writes one task file per paper and reads rows back,
so the model stays outside the deterministic code. The difference is the guarantee --
every returned value must carry the sentence it came from, and this module enforces that
on the way in rather than trusting the extractor to have obeyed.

A row whose ``source_quote`` is empty is dropped and counted. A row whose quote does not
actually contain the value it claims is flagged. Neither is silently accepted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from litsearch.sources.base import Work

INSTRUCTIONS = (
    "Read the paper, preferring 'arxiv_pdf_url' if present -- publisher PDF links are "
    "usually paywalled or blocked, while the arXiv preprint of the same work is open. "
    "Reading the preprint counts as full_text; say which version you read in 'note'. "
    "Extract the requested fields from this paper. Emit one JSON object per distinct "
    "measurement -- a paper reporting several devices or several qubits yields several "
    "rows. Every value MUST be accompanied by 'source_quote', the sentence from the paper "
    "containing it, quoted verbatim. If you cannot quote it, set the field to null and say "
    "why in 'note'. Never supply a number from memory. Set 'confidence' to 'full_text' only "
    "if you actually read the PDF, otherwise 'abstract_only'."
)

DEFAULT_SCHEMA = (
    "qubit_type",
    "material",
    "substrate",
    "T1_us",
    "T2_star_us",
    "T2_echo_us",
    "temperature_mK",
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def arxiv_pdf_url(arxiv_id: str | None) -> str:
    """The arXiv PDF for an id, or '' when there is none."""
    return f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""


def task_for(work: Work, cite_key: str, schema: tuple[str, ...]) -> dict:
    """One extraction task: the paper, where to read it, and the columns to fill.

    Two PDF routes are offered, because the publisher one usually fails. On the first real
    extraction run every publisher URL was unfetchable -- APS returned 403 and Nature
    redirected into an auth flow -- while the arXiv preprint of the same paper was open.
    ``has_open_access_pdf`` is true in the OA-status sense and still useless operationally,
    so it is no longer the only thing an extractor is given.
    """
    return {
        "instructions": INSTRUCTIONS,
        "cite_key": cite_key,
        "title": work.title,
        "doi": work.doi,
        "arxiv_id": work.arxiv_id,
        "pdf_url": work.oa_pdf_url or "",
        "arxiv_pdf_url": arxiv_pdf_url(work.arxiv_id),
        "has_open_access_pdf": bool(work.oa_pdf_url or work.arxiv_id),
        "abstract": work.abstract or "",
        "schema": list(schema),
    }


def prepare_tasks(
    works: list[Work],
    out_dir: Path,
    schema: tuple[str, ...] = DEFAULT_SCHEMA,
    cite_keys: dict[int, str] | None = None,
) -> list[Path]:
    """Write one task file per work. Returns the paths written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("task_*.json"):
        stale.unlink()

    paths = []
    for index, work in enumerate(works):
        cite_key = (cite_keys or {}).get(index) or f"work{index:03d}"
        path = out_dir / f"task_{index:03d}.json"
        path.write_text(
            json.dumps(task_for(work, cite_key, schema), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return paths


_NUMERIC_VALUE = re.compile(r"^[<>~=\s]*[-+]?\d+(?:[.,]\d+)?(?:\s*[eE][-+]?\d+)?[\s%]*$")


def is_numeric(value) -> bool:
    """Is this a measured number, rather than descriptive text that happens to contain one?

    The digit check below only makes sense for numbers. A field like
    ``modes: "two 3D cavities"`` or ``platform: "superconducting, 3D cavity"`` contains a
    digit incidentally, and demanding the quote repeat it produced a stream of false
    alarms on the first real extraction.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return bool(_NUMERIC_VALUE.match(str(value))) if value not in (None, "", []) else False


def _quote_supports(value, quote: str) -> bool:
    """Does the quote plausibly contain the claimed number?

    Deliberately forgiving about formatting -- a paper may write 0.36 ms where the schema
    wants 360 us -- so this only flags a value whose digits appear nowhere in its quote.
    It catches invented numbers, not unit conversions.
    """
    if value in (None, "", []) or not is_numeric(value):
        return True
    text = str(value)
    digits = _NUMBER.findall(text)
    if not digits:
        return True
    quote_digits = set(_NUMBER.findall(quote))
    if not quote_digits:
        return False
    for digit in digits:
        stripped = digit.replace(",", ".").rstrip("0").rstrip(".")
        for candidate in quote_digits:
            candidate_stripped = candidate.replace(",", ".").rstrip("0").rstrip(".")
            if stripped and (stripped in candidate_stripped or candidate_stripped in stripped):
                return True
    return False


def validate_rows(rows: list[dict], schema: tuple[str, ...] = DEFAULT_SCHEMA) -> tuple[list[dict], list[str]]:
    """Enforce the quote guarantee. Returns (accepted rows, complaints).

    Two different outcomes, deliberately: a row with NO quote is dropped, because a value
    nobody can quote is not evidence. A row whose quote does not obviously contain the
    number it claims is *flagged and kept* -- the check is a heuristic over units and
    formatting, and silently discarding real measurements over it would be worse than
    surfacing them for a human to glance at.
    """
    accepted = []
    complaints = []
    for position, row in enumerate(rows):
        quote = str(row.get("source_quote", "")).strip()
        key = row.get("cite_key", f"row{position}")
        if not quote:
            complaints.append(f"{key}: dropped, no source_quote")
            continue
        unsupported = [
            field
            for field in schema
            if row.get(field) not in (None, "", [])
            and not _quote_supports(row.get(field), quote)
        ]
        if unsupported:
            complaints.append(f"{key}: quote does not contain {', '.join(unsupported)}")
        accepted.append(row)
    return accepted, complaints


def load_rows(path: Path) -> list[dict]:
    """Read an extraction JSONL file, skipping malformed lines rather than guessing."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
