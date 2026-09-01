"""Stage 4: relevance screening.

Screening needs a language model, and this package never calls one directly. Instead it
writes batch files for an agent to answer and reads the answers back, which keeps the
model outside the deterministic code and makes the stage inspectable and re-runnable:
the batches and verdicts are files you can read, diff and correct by hand.

Two backends consume the batches:

* the ``lit-screener`` subagent, driven by the ``/litsearch`` skill
* ``litsearch.sdk_runner``, if ``claude-agent-sdk`` is installed

A work with no verdict stays unscreened and is reported, never silently included.
"""

from __future__ import annotations

import json
from pathlib import Path

from litsearch.corpus import Corpus
from litsearch.sources.base import Work

INCLUDE = "include"
EXCLUDE = "exclude"
UNSURE = "unsure"
VALID_VERDICTS = (INCLUDE, EXCLUDE, UNSURE)

DEFAULT_BATCH_SIZE = 25

INSTRUCTIONS = (
    "For each work below, decide whether it meets the inclusion criteria. "
    "Reply with one JSON object per line and nothing else: "
    '{"index": <int>, "verdict": "include|exclude|unsure", "reason": "<one short clause>"}. '
    "Use 'unsure' when the abstract does not say enough to decide -- that is a real answer, "
    "not a failure. Judge relevance only; do not judge whether the paper is correct."
)


def work_summary(work: Work, index: int) -> dict:
    """The reduced view a screener sees: title and abstract, never the full record."""
    return {
        "index": index,
        "title": work.title,
        "year": work.year,
        "venue": work.venue,
        "abstract": (work.abstract or "")[:1500],
    }


def prepare_batches(
    corpus: Corpus,
    inclusion: str,
    exclusion: str,
    out_dir: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[Path]:
    """Write screening batches. Returns the paths written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("batch_*.json"):
        stale.unlink()

    paths = []
    works = corpus.works
    for start in range(0, len(works), batch_size):
        chunk = works[start : start + batch_size]
        payload = {
            "instructions": INSTRUCTIONS,
            "inclusion_criteria": inclusion,
            "exclusion_criteria": exclusion,
            "works": [work_summary(work, start + offset) for offset, work in enumerate(chunk)],
        }
        path = out_dir / f"batch_{start // batch_size:02d}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def load_verdicts(path: Path) -> dict[int, dict]:
    """Read a verdicts JSONL file. Malformed or unknown verdicts are skipped, not guessed."""
    path = Path(path)
    if not path.exists():
        return {}
    verdicts: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        index = row.get("index")
        verdict = str(row.get("verdict", "")).lower()
        if isinstance(index, int) and verdict in VALID_VERDICTS:
            verdicts[index] = {"verdict": verdict, "reason": str(row.get("reason", ""))}
    return verdicts


def apply_verdicts(corpus: Corpus, verdicts: dict[int, dict]) -> dict[str, int]:
    """Stamp verdicts onto the corpus. Returns counts per verdict, including unscreened."""
    counts = {INCLUDE: 0, EXCLUDE: 0, UNSURE: 0, "unscreened": 0}
    for index, work in enumerate(corpus.works):
        row = verdicts.get(index)
        if row is None:
            work.screen = ""
            work.screen_reason = ""
            counts["unscreened"] += 1
            continue
        work.screen = row["verdict"]
        work.screen_reason = row["reason"]
        counts[row["verdict"]] += 1
    return counts


def included(corpus: Corpus) -> list[Work]:
    """Works the screener kept. 'unsure' is deliberately excluded -- it needs a human."""
    return [work for work in corpus.works if work.screen == INCLUDE]


def needs_review(corpus: Corpus) -> list[Work]:
    """Works a human has to decide: 'unsure', plus anything never screened."""
    return [work for work in corpus.works if work.screen in ("", UNSURE)]


def write_review_queue(path: Path, works: list[Work]) -> int:
    """Surface the undecided works rather than burying them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Needs your decision", ""]
    if not works:
        lines.append("Every work was screened as include or exclude.")
    else:
        lines.append(f"{len(works)} works the screener could not decide, or never saw.")
        lines.append("They are NOT in the results. Decide each one before treating the")
        lines.append("search as complete.")
        lines.append("")
        lines.append("| Year | Title | Verdict | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for work in works:
            title = (work.title or "(no title)")[:70].replace("|", "/")
            lines.append(f"| {work.year or '-'} | {title} | {work.screen or 'unscreened'} | {work.screen_reason} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(works)
