"""Candidate contradictions in the evidence table.

``docs/OUTPUT_FORMATS.md`` requires ``review.md`` to say where papers disagree and cite
both sides. Nothing helped find the disagreement, so that section got written from
impression -- which is the failure mode the rest of the pipeline exists to prevent.

This module finds the candidates deterministically. It groups extracted rows by the
conditions they were measured under, and flags any group where papers report values that
differ by more than a set ratio. Every flagged group carries both extreme rows and their
quotes, so the reader judges the disagreement from the papers' own words.

**A flag is a question, not a verdict.** A wide spread across two papers usually has a
dull explanation -- different devices, a different definition of the quantity, a unit the
extractor read as microseconds and the paper meant milliseconds. Those are exactly the
things worth looking at, and they are indistinguishable from a real contradiction until
someone reads the quotes. Nothing here decides which it was.
"""

from __future__ import annotations

import re
from pathlib import Path

from litsearch.extract import is_numeric

# How far apart two reported values must be before the group is worth a look. Physical
# quantities vary by a factor of two between devices as a matter of course, and flagging
# that would bury the interesting cases; a threefold spread is uncommon enough to deserve
# a glance and common enough that the file is not empty on every run.
DISAGREEMENT_RATIO = 3.0

# A single paper reporting several devices is not disagreeing with anyone. A flag needs at
# least this many distinct papers, or the whole exercise re-flags normal device spread.
MIN_DISTINCT_PAPERS = 2

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:\s*[eE][-+]?\d+)?")


def numeric_value(value) -> float | None:
    """The number a cell states, or None if it does not state one.

    A comma is read as a decimal point, matching ``extract.quote_supports``. That is wrong
    for a thousands separator, but indexes and papers in this domain write 1,5 far more
    often than 1,500, and the alternative -- guessing per value -- is worse.
    """
    if isinstance(value, bool) or value in (None, "", []):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not is_numeric(value):
        return None
    match = _NUMBER.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def quantity_columns(rows: list[dict], schema: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the schema into (quantities, conditions), from the data rather than config.

    A column is a quantity when every value anyone extracted into it is a number, and a
    condition otherwise. Deriving it here means a new search does not have to declare which
    of its columns are measurements -- the rows say so.
    """
    quantities, conditions = [], []
    for field in schema:
        values = [row.get(field) for row in rows if row.get(field) not in (None, "", [])]
        if values and all(numeric_value(value) is not None for value in values):
            quantities.append(field)
        else:
            conditions.append(field)
    return tuple(quantities), tuple(conditions)


def _condition_key(row: dict, conditions: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """The conditions a row was measured under, normalised for grouping.

    A missing condition becomes its own group rather than a wildcard. Comparing a row that
    names no material against one that names tantalum would manufacture a disagreement out
    of an incomplete record.
    """
    return tuple((field, " ".join(str(row.get(field) or "").lower().split()) or "(unstated)") for field in conditions)


def find_conflicts(
    rows: list[dict],
    schema: tuple[str, ...],
    ratio: float = DISAGREEMENT_RATIO,
    min_papers: int = MIN_DISTINCT_PAPERS,
) -> list[dict]:
    """Groups of rows whose reported values disagree by more than ``ratio``.

    Returns one record per (quantity, conditions) group that qualifies, widest spread
    first, each carrying the lowest and highest rows with their quotes.
    """
    quantities, conditions = quantity_columns(rows, schema)
    conflicts = []

    for quantity in quantities:
        groups: dict[tuple, list[dict]] = {}
        for row in rows:
            value = numeric_value(row.get(quantity))
            # Zero and negative values are dropped rather than compared: the spread is a
            # ratio, and it is undefined at zero and meaningless across a sign change.
            if value is None or value <= 0:
                continue
            groups.setdefault(_condition_key(row, conditions), []).append(
                {
                    "cite_key": str(row.get("cite_key", "")),
                    "value": value,
                    "as_written": row.get(quantity),
                    "quote": str(row.get("source_quote", "")),
                    "confidence": str(row.get("confidence", "")),
                }
            )

        for key, members in groups.items():
            if len({member["cite_key"] for member in members}) < min_papers:
                continue
            ordered = sorted(members, key=lambda member: member["value"])
            low, high = ordered[0], ordered[-1]
            if low["cite_key"] == high["cite_key"]:
                continue
            spread = high["value"] / low["value"]
            if spread < ratio:
                continue
            conflicts.append(
                {
                    "field": quantity,
                    "conditions": dict(key),
                    "ratio": round(spread, 1),
                    "rows": len(members),
                    "papers": len({member["cite_key"] for member in members}),
                    "low": low,
                    "high": high,
                }
            )

    conflicts.sort(key=lambda conflict: conflict["ratio"], reverse=True)
    return conflicts


def write_conflicts(path: Path, conflicts: list[dict], row_count: int, ratio: float = DISAGREEMENT_RATIO) -> int:
    """Write conflicts.md. Returns how many groups were flagged."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Candidate contradictions", ""]
    if not conflicts:
        lines += [
            f"No group of comparable rows in the {row_count} extracted disagrees by more than",
            f"{ratio:g}x. That is not evidence the literature agrees -- it is the absence of a",
            "disagreement this check can see. Rows measured under different stated conditions",
            "are never compared, so a field split cleanly along conditions produces nothing",
            "here however much its numbers differ.",
        ]
    else:
        lines += [
            f"{len(conflicts)} group(s) of rows report values differing by more than {ratio:g}x",
            "under conditions the extraction recorded as the same.",
            "",
            "**Each of these is a question, not a finding.** The usual explanations are dull:",
            "different devices, a different definition of the quantity, or a unit the paper",
            "stated and the extraction converted. Read both quotes before writing either paper",
            "up as contradicting the other, and if the conditions differ in a way the schema",
            "does not capture, the answer is a new column rather than a contradiction.",
        ]
        for conflict in conflicts:
            stated = ", ".join(f"{name}={value}" for name, value in conflict["conditions"].items())
            lines += [
                "",
                f"## {conflict['field']} -- {stated or 'no stated conditions'}",
                "",
                f"{conflict['rows']} row(s) from {conflict['papers']} paper(s) span "
                f"{conflict['low']['value']:g} to {conflict['high']['value']:g} ({conflict['ratio']:g}x).",
                "",
                "| | Cite key | Value | Read from | Quote |",
                "| --- | --- | --- | --- | --- |",
            ]
            for label, member in (("low", conflict["low"]), ("high", conflict["high"])):
                quote = member["quote"].replace("|", "/").replace("\n", " ")[:200]
                lines.append(
                    f"| {label} | {member['cite_key']} | {member['as_written']} | "
                    f"{member['confidence'] or '-'} | {quote} |"
                )

    lines += [
        "",
        "## What this check does not cover",
        "",
        "- Only numeric columns are compared, and only within identical stated conditions.",
        "- A disagreement between a paper in the corpus and one the search never retrieved",
        "  cannot appear here. Check the known-item and gold-set results in run.json.",
        f"- The threshold is {ratio:g}x and is arbitrary. A real contradiction smaller than it",
        "  is not flagged, and a harmless spread larger than it is.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(conflicts)
