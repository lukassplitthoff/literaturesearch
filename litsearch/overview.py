"""Stage 4b: an abstract-level overview of every screened-in paper.

The screener has already read these abstracts to decide relevance; this stage collects
them into something a person can read before any full paper is opened. It is cheap -- a
few hundred tokens an abstract -- and it answers "what is in here" for the whole included
set, where full-text extraction answers "what are the numbers" for a chosen few.

Same shape as screening and extraction: this module writes packets for a model to read and
checks what comes back, and never calls a model itself. The ``lit-summarizer`` agent reads
the packets and writes ``overview/draft.md``; ``check_draft`` then holds the draft to the
rules below, and only a clean draft is published as ``overview.md``, between a header and
footer this module writes -- so the disclaimer and the coverage statement cannot be left
out or softened by the model.

The rules, all checked here:

* every paragraph and bullet cites at least one work, as ``[@Key]``, and every key names a
  work in the overview set;
* a quote attributed to a work, ``"..." [@Key]``, occurs verbatim in that work's abstract;
* a number in the prose is carried by such a quote in the same paragraph. Abstracts state
  headline numbers, and a number is only allowed through with the sentence it came from --
  the same contract evidence.csv has, checked more strictly here because the abstract text
  is on disk to compare against.

What it cannot check is whether a sentence fairly represents the papers it cites. That is
why the header says the overview is abstract-level and unverified against the full text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from litsearch.extract import quote_supports
from litsearch.screen import ROLES
from litsearch.sources.base import Work

DEFAULT_PACKET_SIZE = 25
GROUP_BY = ("role", "year", "theme")

INSTRUCTIONS = (
    "Summarise what these screened-in papers report, from their abstracts only, as notes for "
    "an overview of the whole set. Cite every paragraph and bullet as [@Key], using only the "
    "keys given here. You may state a number only together with the sentence it came from, "
    'quoted verbatim from that paper\'s abstract, as "quoted text" [@Key] in the same '
    "paragraph. Write counts of papers in words, not digits. No tables. Report what the "
    "abstracts claim; do not judge whether it is correct, and add nothing from memory."
)

_CITATION = re.compile(r"\[@([^\]]+)\]")
_KEY = re.compile(r"@?([A-Za-z0-9_:.+\-]+)")
_QUOTE_THEN_KEY = re.compile(r'"([^"]+)"\s*\[@([A-Za-z0-9_:.+\-]+)\]')
_QUOTED = re.compile(r'"[^"]*"')
# A number standing on its own: not the digit in "T1", "3D" or "2x", and not a year.
_BARE_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)?(?![A-Za-z0-9_])")
_YEAR = re.compile(r"^(19|20)\d\d$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Curly quotes, written as code points so this file stays ASCII.
_TYPOGRAPHIC = str.maketrans({chr(0x201C): '"', chr(0x201D): '"', chr(0x2018): "'", chr(0x2019): "'"})


def _normalise(text: str) -> str:
    return " ".join((text or "").translate(_TYPOGRAPHIC).lower().split())


def _order(works: list[Work], group_by: str) -> list[int]:
    """Positions in the order packets are cut, so each packet is roughly one group."""
    role_rank = {role: rank for rank, role in enumerate(ROLES)}
    if group_by == "year":
        return sorted(range(len(works)), key=lambda i: (str(works[i].year or "9999"), works[i].title))
    return sorted(
        range(len(works)),
        key=lambda i: (role_rank.get(works[i].role, len(ROLES)), str(works[i].year or "9999"), works[i].title),
    )


def prepare_packets(
    works: list[Work],
    cite_keys: dict[int, str],
    question: str,
    out_dir: Path,
    focus: str = "",
    group_by: str = "role",
    packet_size: int = DEFAULT_PACKET_SIZE,
) -> tuple[list[Path], list[str]]:
    """Write overview packets. Returns (paths written, keys of works with no abstract).

    The full abstract is sent, not the screening cut: screening needs the opening claim,
    a summary needs the result sentence, which is often the last one. A work without an
    abstract or without a cite key cannot be summarised or cited; the former is returned
    so the published overview can name it.
    """
    if group_by not in GROUP_BY:
        raise ValueError(f"summary_group_by must be one of {', '.join(GROUP_BY)}, not {group_by!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("packet_*.json"):
        stale.unlink()

    entries, no_abstract = [], []
    for position in _order(works, group_by):
        key = cite_keys.get(position)
        if not key:
            continue
        work = works[position]
        if not (work.abstract or "").strip():
            no_abstract.append(key)
            continue
        entries.append(
            {
                "key": key,
                "title": work.title,
                "year": work.year,
                "role": work.role or "unclassified",
                "abstract": work.abstract.strip(),
            }
        )

    chunks = [entries[start : start + packet_size] for start in range(0, len(entries), packet_size)]
    paths = []
    for number, chunk in enumerate(chunks):
        payload = {
            "instructions": INSTRUCTIONS,
            "question": question,
            "focus": focus,
            "group_by": group_by,
            "packet": number,
            "of": len(chunks),
            "works": chunk,
        }
        path = out_dir / f"packet_{number:02d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        paths.append(path)
    return paths, no_abstract


def _blocks(text: str) -> list[str]:
    """Paragraphs and list items -- the units that must each carry a citation."""
    blocks, current = [], []
    for line in text.translate(_TYPOGRAPHIC).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                blocks.append(" ".join(current))
            current = []
            continue
        if _LIST_ITEM.match(line) and current:
            blocks.append(" ".join(current))
            current = []
        current.append(stripped)
    if current:
        blocks.append(" ".join(current))
    return blocks


def cited_keys(text: str) -> set[str]:
    """Every key the text cites."""
    keys = set()
    for group in _CITATION.findall(text.translate(_TYPOGRAPHIC)):
        for part in group.split(";"):
            match = _KEY.match(part.strip())
            if match:
                keys.add(match.group(1))
    return keys


def check_draft(text: str, abstracts: dict[str, str]) -> list[str]:
    """Hold a draft to the overview rules. Returns problems; an empty list means publishable.

    ``abstracts`` maps every key the overview may cite to that work's abstract.
    """
    problems = []
    if not text.strip():
        return ["the draft is empty"]
    normalised = {key: _normalise(abstract) for key, abstract in abstracts.items()}
    for block in _blocks(text):
        preview = block[:70]
        if block.lstrip().startswith("|"):
            problems.append(f"table rows are not allowed, use bullets: {preview}")
            continue
        keys = cited_keys(block)
        if not keys:
            problems.append(f"uncited paragraph: {preview}")
        for key in sorted(keys - set(abstracts)):
            problems.append(f"unknown key [@{key}]: {preview}")
        quotes = _QUOTE_THEN_KEY.findall(block)
        for quote, key in quotes:
            if key in normalised and _normalise(quote) not in normalised[key]:
                problems.append(f'quote not found in the abstract of {key}: "{quote[:60]}"')
        prose = _CITATION.sub(" ", _QUOTED.sub(" ", block))
        carried = " ".join(quote for quote, _ in quotes)
        for number in _BARE_NUMBER.findall(prose):
            if _YEAR.match(number):
                continue
            if not carried or not quote_supports(number, carried):
                problems.append(f"number {number} has no abstract quote carrying it: {preview}")
    # Problems quote the draft, which quotes abstracts -- Greek letters and all -- and they
    # are printed to consoles that cannot encode them.
    return [problem.encode("ascii", "replace").decode("ascii") for problem in problems]


def write_overview(
    path: Path,
    draft: str,
    question: str,
    focus: str,
    keys: list[str],
    no_abstract: list[str],
    review_queue: int = 0,
    quarantined: int = 0,
) -> list[str]:
    """Publish a checked draft as overview.md. Returns the keys the text never cites.

    The header and footer are written here, not by the model, so the statement of what the
    overview is and is not built from travels with it unchanged.
    """
    path = Path(path)
    not_discussed = [key for key in keys if key not in cited_keys(draft) and key not in no_abstract]
    lines = [
        "# Overview -- abstract level",
        "",
        f"> {question}",
        "",
    ]
    if focus:
        lines += [f"Focus: {focus}", ""]
    lines += [
        f"**Built from abstracts only**, for the {len(keys)} screened-in, validated works. An",
        "abstract reports a paper's headline result -- usually its best device under its best",
        "conditions. Every number below is quoted from an abstract and has not been checked",
        "against the full text; evidence.csv holds the full-text values, and priority.md says",
        "which papers have been read in full.",
        "",
        draft.strip(),
        "",
        "## What this overview does not cover",
        "",
    ]
    if not_discussed:
        lines.append(
            f"- **Not discussed above ({len(not_discussed)}):** "
            + "; ".join(f"[@{key}]" for key in not_discussed)
            + ". They are in the included set; the summary did not draw on them."
        )
    if no_abstract:
        lines.append(
            f"- **No abstract on the index record ({len(no_abstract)}):** "
            + "; ".join(f"[@{key}]" for key in no_abstract)
            + ". Nothing could be summarised for them."
        )
    if review_queue:
        lines.append(f"- **{review_queue} work(s) are undecided or unscreened** -- see needs_review.md.")
    if quarantined:
        lines.append(f"- **{quarantined} work(s) failed the validation gate** -- see quarantine.md.")
    lines.append("- **Anything outside the search** -- the year window, sources and misses are in run.json.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return not_discussed
