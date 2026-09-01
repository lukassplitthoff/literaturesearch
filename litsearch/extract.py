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


def task_for(work: Work, cite_key: str, schema: tuple[str, ...]) -> dict:
    """One extraction task: the paper, where to read it, and the columns to fill."""
    return {
        "instructions": INSTRUCTIONS,
        "cite_key": cite_key,
        "title": work.title,
        "doi": work.doi,
        "arxiv_id": work.arxiv_id,
        "pdf_url": work.oa_pdf_url or "",
        "has_open_access_pdf": bool(work.oa_pdf_url),
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


def _quote_supports(value, quote: str) -> bool:
    """Does the quote plausibly contain the claimed number?

    Deliberately forgiving about formatting -- a paper may write 0.36 ms where the schema
    wants 360 us -- so this only flags a value whose digits appear nowhere in its quote.
    It catches invented numbers, not unit conversions.
    """
    if value in (None, "", []):
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
    """Enforce the quote guarantee. Returns (accepted rows, complaints)."""
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
