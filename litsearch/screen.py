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
import re
from difflib import SequenceMatcher
from pathlib import Path

from litsearch.corpus import Corpus
from litsearch.sources.base import Work

INCLUDE = "include"
EXCLUDE = "exclude"
UNSURE = "unsure"
VALID_VERDICTS = (INCLUDE, EXCLUDE, UNSURE)

DEFAULT_BATCH_SIZE = 25

# Abstracts are truncated before they are sent. A screening decision -- is this the right
# platform, is there a measurement -- is almost always settled by the first few sentences,
# and the tail of an abstract is method detail that costs tokens without changing the
# verdict. 600 characters keeps the opening claim and the usual "we measure X" sentence.
ABSTRACT_CHARS = 600

INSTRUCTIONS = (
    "For each work below, decide whether it meets the inclusion criteria. "
    "Reply with one JSON object per line and nothing else: "
    '{"index": <the work\'s "i">, "t": <the work\'s "c", copied verbatim>, '
    '"verdict": "include|exclude|unsure", "reason": "<one short clause>"}. '
    "Copy 'c' exactly as given -- do not derive, retype or reformat it. It is a checksum "
    "that catches a verdict applied to the wrong paper, and a wrong one is rejected. "
    "Use 'unsure' when the abstract does not say enough to decide -- that is a real answer, "
    "not a failure. Judge relevance only; do not judge whether the paper is correct."
)

# How much of the echoed title must match before a verdict is trusted.
TITLE_CHECK_RATIO = 0.7

# Words of the title used as the checksum. Precomputed and shipped in the batch rather
# than described in prose: asking a screener to derive "the first four words" was ambiguous
# for titles like "1 / f noise:" or ones shorter than four words, and invited retyping --
# and a retyped checksum that drifts is a false alarm.
CHECKSUM_WORDS = 4


def checksum(title: str) -> str:
    """The value a screener must echo back to prove a verdict names the right work."""
    words = [w for w in re.split(r"\W+", (title or "").lower()) if w]
    return " ".join(words[:CHECKSUM_WORDS])


def work_summary(work: Work, index: int, abstract_chars: int = ABSTRACT_CHARS) -> dict:
    """The reduced view a screener sees: title and abstract, never the full record.

    Short keys and a truncated abstract, because this structure is repeated once per work
    and the field names are paid for every time.
    """
    summary = {"i": index, "t": work.title, "c": checksum(work.title)}
    if work.year:
        summary["y"] = work.year
    abstract = (work.abstract or "").strip()
    if abstract:
        summary["a"] = abstract[:abstract_chars]
    return summary


def prepare_batches(
    corpus: Corpus,
    inclusion: str,
    exclusion: str,
    out_dir: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    abstract_chars: int = ABSTRACT_CHARS,
    works: list[Work] | None = None,
) -> list[Path]:
    """Write screening batches. Returns the paths written.

    ``works`` overrides which works are batched -- pass the survivors of triage so the
    model is not asked about papers a rule already settled. Indices stay global, matching
    positions in the corpus, so verdicts apply correctly on the way back.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("batch_*.json"):
        stale.unlink()

    paths = []
    works = corpus.works if works is None else works

    # The index in a batch must be the work's position in the CORPUS, not its position in
    # whatever subset was passed. apply_verdicts looks up corpus.works[index], so numbering
    # a filtered subset from zero silently applies every verdict to the wrong paper -- and
    # it looks entirely plausible on the way out, which is what makes it dangerous.
    global_index = {id(work): position for position, work in enumerate(corpus.works)}
    missing = [work for work in works if id(work) not in global_index]
    if missing:
        raise ValueError(
            f"{len(missing)} works passed to prepare_batches are not in the corpus; "
            "their verdicts could not be applied back"
        )

    for start in range(0, len(works), batch_size):
        chunk = works[start : start + batch_size]
        payload = {
            "instructions": INSTRUCTIONS,
            "inclusion_criteria": inclusion,
            "exclusion_criteria": exclusion,
            "works": [work_summary(work, global_index[id(work)], abstract_chars) for work in chunk],
        }
        path = out_dir / f"batch_{start // batch_size:02d}.json"
        # Compact separators, no indentation: this file exists to be read by a model, and
        # pretty-printing costs a sizeable slice of the payload in whitespace alone.
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
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
            verdicts[index] = {
                "verdict": verdict,
                "reason": str(row.get("reason", "")),
                "t": str(row.get("t", "")),
            }
    return verdicts


def _title_echo_matches(echo: str, title: str) -> bool:
    """Does the echoed title fragment belong to this work?

    Compared word by word against the title's leading words, because the echo is a short
    prefix. Word-wise rather than whole-string: two titles differing in one token --
    '... Part I' and '... Part II', or a numbered series -- score over 0.9 as strings while
    naming different papers, which is exactly the confusion this check exists to catch.
    Individual words are compared loosely, so punctuation, case and a typo do not matter.
    """
    if not echo:
        return True  # no checksum supplied: nothing to verify against
    probe = [w for w in re.split(r"\W+", echo.lower()) if w]
    if not probe:
        return True
    target = [w for w in re.split(r"\W+", (title or "").lower()) if w]
    if len(probe) > len(target):
        return False
    return all(
        SequenceMatcher(None, word, target[position]).ratio() >= TITLE_CHECK_RATIO
        for position, word in enumerate(probe)
    )


def apply_verdicts(corpus: Corpus, verdicts: dict[int, dict], keep_rule_verdicts: bool = True) -> dict[str, int]:
    """Stamp model verdicts onto the corpus. Returns counts per verdict.

    A work already settled by triage keeps that verdict: it was never sent to the model,
    so the absence of a model answer for it is expected, not a gap. Clearing it here would
    silently undo the triage pass and push hundreds of rule-excluded papers back into the
    review queue. ``keep_rule_verdicts=False`` forces a clean slate when re-screening from
    scratch.
    """
    counts = {INCLUDE: 0, EXCLUDE: 0, UNSURE: 0, "unscreened": 0, "by_rule": 0,
              "misaligned": 0, "unverified": 0, "realigned": 0}

    # The index is a hint; the checksum is the identity. Corpus positions shift whenever
    # dedup or triage changes -- merging one duplicate renumbers every work after it -- and
    # re-screening a whole corpus because one record moved would be absurd. So a verdict
    # whose checksum does not match its index is looked up by checksum instead, and applied
    # only when exactly one work answers to it. Ambiguous or absent: refused.
    by_checksum: dict[str, list[int]] = {}
    for position, work in enumerate(corpus.works):
        by_checksum.setdefault(checksum(work.title), []).append(position)

    relocated: dict[int, dict] = {}
    stale: set[int] = set()
    for index, row in verdicts.items():
        echo = row.get("t", "")
        at_index = corpus.works[index].title if 0 <= index < len(corpus.works) else ""
        if not echo or _title_echo_matches(echo, at_index):
            continue
        candidates = by_checksum.get(" ".join(echo.lower().split()), [])
        if len(candidates) == 1:
            relocated[candidates[0]] = row
            # Drop the stale entry, or it stays behind pointing at the wrong work and is
            # counted a second time as a misalignment.
            stale.add(index)
            counts["realigned"] += 1
    verdicts = {
        index: row for index, row in verdicts.items() if index not in stale or index in relocated
    }
    verdicts.update(relocated)
    for index, work in enumerate(corpus.works):
        row = verdicts.get(index)
        if row is not None and not row.get("t"):
            # No checksum to verify against. Accepted for compatibility with verdicts
            # written before the checksum existed, but counted: corpus positions shift
            # whenever dedup or triage changes, and an unverifiable verdict silently
            # follows the shift. This actually happened -- 64 stale verdicts landed on
            # the wrong works and nothing caught them.
            counts["unverified"] += 1
        if row is not None and not _title_echo_matches(row.get("t", ""), work.title):
            # The verdict names a different paper than the index points at. Refusing it is
            # the whole point: a misaligned batch once produced a clean-looking, fully
            # validated bibliography of papers nobody had screened.
            counts["misaligned"] += 1
            row = None
        if row is None:
            rule_set = keep_rule_verdicts and work.screen and work.screen_reason.startswith("rule:")
            if rule_set:
                counts[work.screen] += 1
                counts["by_rule"] += 1
                continue
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
