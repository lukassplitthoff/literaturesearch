"""Stage 6 ordering: which screened-in papers are read in full first, and in what waves.

Full-text extraction is the expensive stage, so it runs in waves of a fixed size rather
than over every included paper at once. The first wave is the papers most likely to carry
the numbers the schema asks for; the user reads what came back and decides whether the
next wave is worth it.

Everything here is deterministic and costs no request and no token. The score is a
heuristic over what screening and retrieval already produced, and it is printed with its
parts in priority.md so a reader can disagree with a placement rather than trust it:

* **role** -- a ``primary`` paper reports its own measurement, which is exactly what
  extraction reads for. A review repeats other people's numbers; theory has none.
* **schema terms** -- how many of the extraction columns the title and abstract name. A
  paper whose abstract says "T1" and "tantalum" is likelier to state them than one that
  says neither.
* **ties** -- broken by citations per year, then by in-corpus citations (see rank.py).

A user overrides the score with ``extract/selection.txt``: ``+Key`` reads a paper in the
next wave regardless, ``-Key`` never reads it. A pin cannot pull in a paper screening did
not include -- that decision belongs to the screener, not to the queue.

Waves are recorded in ``extract/waves.json`` once issued, so a paper's wave does not move
when the corpus or the score changes later. A paper counts as answered once any row for
it is in rows.jsonl, which is why the extractor writes a row even for a paper with nothing
to report.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from litsearch import rank
from litsearch.relevance import MIN_TERM_LENGTH, STOPWORDS, TOKEN
from litsearch.sources.base import Work

DEFAULT_WAVE_SIZE = 20

ROLE_POINTS = {"primary": 3, "method": 1, "": 1, "review": 0, "theory": 0}

# The schema term count is capped so that a long abstract mentioning every column once
# cannot outweigh the role signal.
MAX_TERM_POINTS = 3

# Column-name fragments that are units or qualifiers, not subject words. "T1_us" should
# look for "t1", not for "us", which matches the pronoun in every abstract.
UNIT_TOKENS = frozenset(
    "us ns ms ps fs mk ghz mhz khz hz pct percent db dbm nm um mm kelvin ratio count num type".split()
)

_SELECTION_LINE = re.compile(r"^([+-])\s*(\S+)")


def schema_terms(schema: tuple[str, ...]) -> set[str]:
    """The subject words in the extraction column names: 'fidelity_pct' -> {'fidelity'}.

    Short tokens are kept when they carry a digit, because in physics the quantity's name
    often is one -- 'T1', 'T2'.
    """
    terms = set()
    for column in schema:
        for token in TOKEN.findall(column.lower().replace("_", " ")):
            if token in UNIT_TOKENS or token in STOPWORDS:
                continue
            if len(token) >= MIN_TERM_LENGTH or any(ch.isdigit() for ch in token):
                terms.add(token)
    return terms


def load_selection(path: Path) -> tuple[list[str], set[str]]:
    """Read extract/selection.txt. Returns (pinned keys in file order, skipped keys).

    One key per line, prefixed '+' (read in full) or '-' (never). '#' starts a comment.
    A missing file is an empty selection.
    """
    path = Path(path)
    pinned: list[str] = []
    skipped: set[str] = set()
    if not path.exists():
        return pinned, skipped
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _SELECTION_LINE.match(line.split("#", 1)[0].strip())
        if not match:
            continue
        sign, key = match.groups()
        if sign == "+" and key not in pinned:
            pinned.append(key)
        elif sign == "-":
            skipped.add(key)
    return [key for key in pinned if key not in skipped], skipped


def score_works(
    works: list[Work],
    cite_keys: dict[int, str],
    schema: tuple[str, ...],
    pinned: list[str] = (),
    now_year: int | None = None,
) -> list[dict]:
    """One row per keyed work, best first, with the parts of its score.

    Works without a cite key are uncitable and absent from refs.bib, so they are not
    ranked -- an extracted row for them could never be joined to the bibliography.
    """
    now_year = now_year or date.today().year
    terms = schema_terms(schema)
    local = rank.in_corpus_citations(works)
    pin_order = {key: position for position, key in enumerate(pinned)}
    rows = []
    for position, work in enumerate(works):
        key = cite_keys.get(position)
        if not key:
            continue
        found = sorted(set(TOKEN.findall(f"{work.title} {work.abstract}".lower())) & terms)
        role_points = ROLE_POINTS.get(work.role, 0)
        term_points = min(len(found), MAX_TERM_POINTS)
        rows.append(
            {
                "key": key,
                "position": position,
                "title": work.title,
                "year": rank.year_of(work),
                "role": work.role or "unclassified",
                "terms": found,
                "role_points": role_points,
                "term_points": term_points,
                "score": role_points + term_points,
                "pinned": key in pin_order,
                "citations_per_year": round(rank.citations_per_year(work, now_year), 2),
                "in_corpus_citations": local[position],
            }
        )
    rows.sort(
        key=lambda row: (
            not row["pinned"],
            pin_order.get(row["key"], 0),
            -row["score"],
            -row["citations_per_year"],
            -row["in_corpus_citations"],
            row["key"],
        )
    )
    return rows


def load_waves(path: Path) -> list[list[str]]:
    """The waves issued so far, from extract/waves.json. Missing or unreadable -> none."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    waves = data.get("waves", []) if isinstance(data, dict) else []
    return [[str(key) for key in wave] for wave in waves if isinstance(wave, list)]


def save_waves(path: Path, waves: list[list[str]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"waves": waves}, indent=2) + "\n", encoding="utf-8")


def plan_waves(
    ranked_keys: list[str],
    issued: list[list[str]],
    wave_size: int,
    waves_allowed: int,
    skipped: set[str] = frozenset(),
) -> list[list[str]]:
    """Waves up to ``waves_allowed``: those already issued, then new ones from the ranking.

    An issued wave keeps its papers even if the score has since moved them, because a wave
    is a decision the user has already seen. A paper that is no longer a candidate -- it
    was re-screened out, or skipped since -- leaves its wave. Emptied waves are dropped.
    Lowering ``waves_allowed`` never un-issues a wave: it only stops new ones.
    """
    if wave_size < 1:
        raise ValueError("extraction_wave_size must be at least 1")
    candidates = set(ranked_keys)
    waves = []
    for wave in issued:
        kept = [key for key in wave if key in candidates and key not in skipped]
        if kept:
            waves.append(kept)
    seen = {key for wave in waves for key in wave}
    queue = [key for key in ranked_keys if key not in seen and key not in skipped]
    while len(waves) < waves_allowed and queue:
        waves.append(queue[:wave_size])
        queue = queue[wave_size:]
    return waves


def answered_keys(rows: list[dict]) -> set[str]:
    """Cite keys with at least one row back from the extractor, accepted or not."""
    return {str(row.get("cite_key")) for row in rows if row.get("cite_key")}


def write_priority(
    path: Path,
    rows: list[dict],
    waves: list[list[str]],
    answered: set[str],
    skipped: set[str],
    schema: tuple[str, ...],
    blockers: list[str] = (),
) -> dict[str, int]:
    """Write priority.md: the queue, each paper's wave and status, and why it sits there.

    Returns the coverage counts: included, issued, answered (read in full).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wave_of = {key: number for number, wave in enumerate(waves, start=1) for key in wave}
    issued = set(wave_of)
    read = len([row for row in rows if row["key"] in answered])
    coverage = {"included": len(rows), "issued": len(issued), "answered": read}

    lines = [
        "# Extraction priority",
        "",
        f"**Read in full: {read} of {len(rows)} screened-in papers.** "
        f"{len(issued)} issued across {len(waves)} wave(s).",
        "",
    ]
    if blockers:
        lines += ["**Extraction is blocked:**", ""] + [f"- {reason}" for reason in blockers] + [""]
    lines += [
        "Order: pinned papers first (`+Key` in extract/selection.txt), then by score, then by",
        "citations per year. Score = role points (primary 3, method or unclassified 1, review or",
        f"theory 0) + extraction-column terms named in the title or abstract (at most {MAX_TERM_POINTS}).",
        f"Columns searched for: {', '.join(sorted(schema_terms(schema))) or '(none)'}.",
        "",
        "The score is a heuristic over metadata and the screener's role label. It says which",
        "papers are likeliest to state the numbers, not which are best or most relevant.",
        "",
        "| # | Key | Year | Role | Score | Terms | Cites/yr | Wave | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, row in enumerate(rows, start=1):
        key = row["key"]
        if key in skipped:
            status = "skipped"
        elif key in answered:
            status = "read"
        elif key in issued:
            status = "pending"
        else:
            status = "queued"
        score = f"{row['score']}{' (pinned)' if row['pinned'] else ''}"
        lines.append(
            f"| {number} | {key} | {row['year'] or '-'} | {row['role']} | {score} | "
            f"{', '.join(row['terms']) or '-'} | {row['citations_per_year']} | {wave_of.get(key, '-')} | {status} |"
        )
    if not rows:
        lines.append("| - | (no screened-in, citable papers) | | | | | | | |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return coverage
