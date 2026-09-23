"""Stage 6: evidence extraction.

Same shape as screening: this package writes one task file per paper and reads rows back,
so the model stays outside the deterministic code. The difference is the contract --
every returned value must carry the sentence it came from.

What this module can check on the way in is narrower than that contract: a quote is
present, and it contains the digits of the value it claims. A row whose ``source_quote``
is empty is dropped and counted; a row whose quote lacks the value's digits is flagged.
It cannot prove the quote is about that quantity -- "operated at 20 mK" passes for a
T1 of 20 -- nor that a unit conversion was done right. That still needs a reader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from litsearch.fulltext import FullText, quote_found
from litsearch.sources.base import Work

INSTRUCTIONS = (
    "Read the paper's text at 'text_path' (relative to the extract directory) -- the pipeline "
    "has already fetched and converted it; do not fetch anything. If 'text_path' is empty, no "
    "full text could be obtained ('text_note' says why): extract from 'abstract' and set "
    "'confidence' to 'abstract_only'; otherwise 'full_text'. Fill the 'columns' as each "
    "definition says; a 'choice' column takes exactly one of its listed values, a 'number' "
    "column a bare number. Emit one JSON object per distinct result -- several devices or "
    "regimes yield several rows. Every value MUST be accompanied by 'source_quote', a sentence "
    "copied from the text exactly as it appears there; the pipeline checks it against the same "
    "text and refuses a row whose quote is not found. Copy line breaks as spaces and leave "
    "hyphenation and symbols as they are -- the check ignores whitespace and hyphens, not "
    "edits. If you cannot quote it, set the field to null and say why in 'note'. Never supply "
    "a value from memory. Always write at least one row: a paper with nothing to report gets "
    "one row with every field null, an empty 'source_quote', and the reason in 'note'. Write "
    "all rows once, to 'rows_file'."
)


@dataclass(frozen=True)
class Column:
    """One extraction column: its name, what kind of value it holds, and what it means.

    A bare name is still accepted in a schema and becomes a free-text column. Types and
    definitions exist because the first real extraction showed what happens without them:
    a yes/no column came back as "True", "yes -- predicted..." and "partly: ...", and one
    "critical photon number" column held three different quantities from three papers.
    """

    name: str
    kind: str = "text"  # "text", "number" or "choice"
    definition: str = ""
    choices: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        spec = {"name": self.name, "type": self.kind, "definition": self.definition}
        if self.choices:
            spec["choices"] = list(self.choices)
        return spec


def as_columns(schema) -> tuple[Column, ...]:
    """A schema of names and/or Columns, as Columns."""
    return tuple(item if isinstance(item, Column) else Column(str(item)) for item in schema)


def column_names(schema) -> tuple[str, ...]:
    """Just the names, for the code that only needs those."""
    return tuple(column.name for column in as_columns(schema))


DEFAULT_SCHEMA = (
    "qubit_type",
    "material",
    "substrate",
    "T1_us",
    "T2_star_us",
    "T2_echo_us",
    "temperature_mK",
)

# Stage 6 is the one expensive stage: every task is an extractor reading one whole paper.
# Past this many tasks the run refuses to write them. The largest deliberate extraction so
# far was 46 papers; a count in the hundreds has only ever meant that screening had not
# happened. A search that genuinely needs more raises max_extraction_tasks on its SearchSpec.
DEFAULT_MAX_TASKS = 60

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def extraction_blockers(planned: int, unscreened: int, schema: tuple[str, ...], max_tasks: int) -> list[str]:
    """Why stage 6 must not write tasks yet. An empty list means it may.

    Extraction reads the papers screening has already chosen; it is never the step that
    discovers which papers are worth reading. So it fails closed: an incomplete screen, an
    empty schema or an implausibly large task count each stop it, rather than letting the
    expensive stage run on whatever happens to be lying around.

    Args:
        planned: papers issued for extraction across every wave, including a new one.
        unscreened: works that needed a model verdict and have none.
        schema: the columns to extract.
        max_tasks: the largest number of tasks allowed without raising the limit.
    """
    blockers = []
    if unscreened:
        blockers.append(
            f"{unscreened} work(s) have no screening verdict. Extraction only reads works screening "
            f"has included -- finish screening first"
        )
    if not schema:
        blockers.append(
            "extraction_schema is empty. Extraction fills declared columns; it does not read or "
            "summarise papers without them"
        )
    if planned > max_tasks:
        blockers.append(
            f"{planned} papers would be issued for extraction, above max_extraction_tasks={max_tasks}. "
            f"Lower extraction_waves, or raise the limit on the SearchSpec if this many is intended"
        )
    return blockers


def clear_tasks(out_dir: Path) -> None:
    """Delete task files from a previous run, so a stale task is never answered."""
    for stale in Path(out_dir).glob("task_*.json"):
        stale.unlink()


# Each paper's rows go in their own file. A shared rows.jsonl can only be appended to by
# reading and rewriting it whole, so extractors running in parallel overwrite each other's
# rows; one file per paper has one writer. rows.jsonl is still read, for older runs.
ROWS_DIR = "rows"


def rows_file(cite_key: str) -> str:
    """Where one paper's rows go, relative to the extract directory."""
    return f"{ROWS_DIR}/{cite_key}.jsonl"


def task_for(work: Work, cite_key: str, schema, fulltext: FullText | None = None) -> dict:
    """One extraction task: the paper, the text to read, and the columns to fill.

    The task carries a path to text the pipeline already fetched and converted, not a URL:
    the extractor reads, it does not fetch. No text -- no preprint, no open PDF -- means an
    empty ``text_path`` and a ``text_note`` saying why, and the extractor works from the
    abstract.
    """
    fulltext = fulltext or FullText(cite_key, note="full text was not fetched")
    columns = as_columns(schema)
    return {
        "instructions": INSTRUCTIONS,
        "cite_key": cite_key,
        "title": work.title,
        "doi": work.doi,
        "arxiv_id": work.arxiv_id,
        "text_path": fulltext.text_path,
        "text_source": fulltext.source,
        "text_note": fulltext.note,
        "abstract": work.abstract or "",
        "columns": [column.as_dict() for column in columns],
        "schema": [column.name for column in columns],
        "rows_file": rows_file(cite_key),
    }


def prepare_tasks(
    works: list[Work],
    out_dir: Path,
    schema=DEFAULT_SCHEMA,
    cite_keys: dict[int, str] | None = None,
    texts: dict[str, FullText] | None = None,
) -> list[Path]:
    """Write one task file per work. Returns the paths written.

    Raises:
        ValueError: if ``schema`` is empty -- a task with no columns asks for a full read
            that yields nothing checkable.
    """
    if not schema:
        raise ValueError("extraction schema is empty; declare the columns to extract")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_tasks(out_dir)

    paths = []
    for index, work in enumerate(works):
        cite_key = (cite_keys or {}).get(index) or f"work{index:03d}"
        path = out_dir / f"task_{index:03d}.json"
        path.write_text(
            json.dumps(task_for(work, cite_key, schema, (texts or {}).get(cite_key)), indent=2, ensure_ascii=False)
            + "\n",
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


def quote_supports(value, quote: str) -> bool:
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


def _check_type(column: Column, value) -> str:
    """'' when the value fits the column's type, else what is wrong with it."""
    if value in (None, "", []) or column.kind == "text":
        return ""
    if column.kind == "number" and not is_numeric(value):
        return f"{column.name}={value!r} is not a number"
    if column.kind == "choice" and str(value).strip().lower() not in {c.lower() for c in column.choices}:
        return f"{column.name}={value!r} is not one of {', '.join(column.choices)}"
    return ""


def validate_rows(
    rows: list[dict],
    schema=DEFAULT_SCHEMA,
    texts: dict[str, str] | None = None,
    abstracts: dict[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Check each row against its quote, its paper and its column types.

    Returns (accepted rows, complaints). Outcomes, strictest first:

    - no quote and no value: the extractor saying it read the paper and found nothing.
      Neither evidence nor a complaint.
    - no quote but a value: dropped. A value nobody can quote is not evidence.
    - a quote that does not occur in the paper's text: dropped. The text is the file the
      extractor was given (``texts``), or the abstract for an abstract-only row, and the
      comparison ignores only case, whitespace and hyphenation -- so a quote that fails
      was edited, stitched together or invented. With neither on disk -- rows from a run
      that predates Python-side fetching -- the quote cannot be checked, and says so.
    - a value of the wrong type (a word in a number column, an unlisted choice): that
      value is set to null and the row kept, since its other values may stand.
    - a quote without the value's digits: flagged and kept. That check is a heuristic over
      units and formatting, and would silently discard real measurements if it dropped.
    """
    columns = as_columns(schema)
    names = tuple(column.name for column in columns)
    texts = texts or {}
    abstracts = abstracts or {}
    accepted = []
    complaints = []
    for position, original in enumerate(rows):
        row = dict(original)
        quote = str(row.get("source_quote", "")).strip()
        key = str(row.get("cite_key", f"row{position}"))
        if not quote and not any(row.get(name) not in (None, "", []) for name in names):
            continue
        if not quote:
            complaints.append(f"{key}: dropped, no source_quote")
            continue
        source = abstracts.get(key, "") if row.get("confidence") == "abstract_only" else texts.get(key, "")
        if source:
            if not quote_found(quote, source):
                complaints.append(f"{key}: dropped, quote not found in the paper's text")
                continue
        elif texts or abstracts:
            complaints.append(f"{key}: quote unverifiable, no text on disk for this paper")
        for column in columns:
            problem = _check_type(column, row.get(column.name))
            if problem:
                complaints.append(f"{key}: {problem}; set to null")
                row[column.name] = None
        unsupported = [
            name for name in names if row.get(name) not in (None, "", []) and not quote_supports(row.get(name), quote)
        ]
        if unsupported:
            complaints.append(f"{key}: quote does not contain {', '.join(unsupported)}")
        accepted.append(row)
    return accepted, complaints


def load_all_rows(extract_dir: Path) -> list[dict]:
    """Every extracted row of a run: the legacy rows.jsonl, then one file per paper."""
    extract_dir = Path(extract_dir)
    rows = load_rows(extract_dir / "rows.jsonl")
    for path in sorted((extract_dir / ROWS_DIR).glob("*.jsonl")):
        rows.extend(load_rows(path))
    return rows


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
