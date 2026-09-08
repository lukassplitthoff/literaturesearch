"""The reading plan: a view over the corpus that says what to read, and in what order.

``shortlist.md`` answers "what survived the gate". It does not answer the question anyone
actually has in front of forty validated papers, which is where to start. A ranked table
is not a plan -- it implies the right move is to read downwards, and it is not.

Three phases, each with a rule the reader can check:

1. **Foundation** -- reviews, and the papers this corpus cites most. Reading these first is
   what makes the rest legible, and the second group is chosen by the corpus itself rather
   than by anyone's opinion of what matters.
2. **Core evidence** -- established work, older than the frontier window, ranked.
3. **Frontier** -- everything from the last few years, ranked. Separated out because the
   question is usually about the current state, and because recent work has not had time
   to accumulate the citations that would rank it fairly against the rest.

Every phase is disjoint, every placement follows from metadata, and no sentence here is a
claim about what any paper found. This module makes no model call and asserts no result:
it sorts papers into an order, and the papers still have to be read.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from litsearch import rank
from litsearch.screen import ROLES
from litsearch.sources.base import Work

FOUNDATION_LIMIT = 8
CORE_LIMIT = 15
FRONTIER_LIMIT = 12

# How recent a paper has to be to count as the frontier rather than the established
# literature. Three years is roughly the point at which citation counts start to mean
# something, which is the same reason the phases are split here.
FRONTIER_YEARS = 3

# A paper this many other corpus members cite is a landmark for this question, whatever
# its global citation count says. One citation is noise; two is a pattern worth reading.
LANDMARK_MIN_CITATIONS = 2


def _phase_rows(pairs, cite_keys, positions):
    """Table rows for one phase, in the order given."""
    rows = []
    for work, signal in pairs:
        rows.append(
            {
                "cite_key": cite_keys.get(positions[id(work)], "(not in refs.bib)"),
                "year": work.year or "-",
                "role": work.role or "unclassified",
                "rate": signal["citations_per_year"],
                "in_corpus": signal["in_corpus_citations"],
                "pdf": "yes" if (work.oa_pdf_url or work.arxiv_id) else "-",
                "title": (work.title or "(no title)")[:70].replace("|", "/"),
            }
        )
    return rows


def _render_table(rows: list[dict]) -> list[str]:
    lines = [
        "| Cite key | Year | Role | Cites/yr | Cited here | PDF | Title |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['cite_key']} | {row['year']} | {row['role']} | {row['rate']} | "
            f"{row['in_corpus']} | {row['pdf']} | {row['title']} |"
        )
    return lines


def build_phases(
    works: list[Work],
    now_year: int | None = None,
    frontier_years: int = FRONTIER_YEARS,
    landmark_min: int = LANDMARK_MIN_CITATIONS,
) -> dict:
    """Sort works into the three phases. Returns phase name -> list of (work, signals).

    Assignment is exclusive and in order: a paper the foundation claims does not reappear
    later. Phases are capped by the caller, not here, so the counts a report quotes are the
    real ones rather than the truncated ones.
    """
    now_year = now_year or date.today().year
    ranked = rank.rank(works, now_year=now_year)
    cutoff = now_year - frontier_years

    taken: set[int] = set()

    reviews = [pair for pair in ranked if pair[0].role == "review"]
    taken.update(id(pair[0]) for pair in reviews)

    landmarks = [
        pair for pair in ranked if id(pair[0]) not in taken and pair[1]["in_corpus_citations"] >= landmark_min
    ]
    taken.update(id(pair[0]) for pair in landmarks)

    frontier, core = [], []
    for pair in ranked:
        if id(pair[0]) in taken:
            continue
        year = pair[1]["year"]
        # An unknown year cannot be shown to be recent, so it stays with the established
        # work rather than being promoted into a phase that claims it is new.
        if year is not None and year > cutoff:
            frontier.append(pair)
        else:
            core.append(pair)

    return {
        "foundation": reviews + landmarks,
        "reviews": reviews,
        "landmarks": landmarks,
        "core": core,
        "frontier": frontier,
        "cutoff": cutoff,
        "now_year": now_year,
    }


def role_counts(works: list[Work], roles: tuple[str, ...] = ROLES) -> dict[str, int]:
    """How the corpus distributes across paper kinds, unclassified included."""
    counts = {role: 0 for role in roles}
    counts["unclassified"] = 0
    for work in works:
        counts[work.role if work.role in counts else "unclassified"] += 1
    return counts


def write_reading_plan(
    path,
    works: list[Work],
    question: str,
    cite_keys: dict[int, str] | None = None,
    now_year: int | None = None,
    frontier_years: int = FRONTIER_YEARS,
    limits: tuple[int, int, int] = (FOUNDATION_LIMIT, CORE_LIMIT, FRONTIER_LIMIT),
    review_queue: int = 0,
    quarantined: int = 0,
) -> dict:
    """Write reading_plan.md. Returns the number of works placed in each phase."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cite_keys = cite_keys or {}
    positions = {id(work): position for position, work in enumerate(works)}

    phases = build_phases(works, now_year=now_year, frontier_years=frontier_years)
    foundation_limit, core_limit, frontier_limit = limits
    counts = role_counts(works)

    lines = [
        "# Reading plan",
        "",
        f"> {question}",
        "",
        f"{len(works)} validated works, sorted into three phases by metadata alone. Nothing",
        "below is a claim about what any of these papers found -- that is what reading them",
        "is for. The placement rules are stated with each phase so you can disagree with one.",
        "",
        "## What the corpus is made of",
        "",
        "| Kind | Works |",
        "| --- | --- |",
    ]
    for role, count in counts.items():
        lines.append(f"| {role} | {count} |")
    if counts["unclassified"] == len(works) and works:
        lines += [
            "",
            "Every work is unclassified, which means the screening verdicts carry no `role`.",
            "Re-screen to populate it -- the phases below still work, but phase 1 falls back",
            "to citation structure alone because it cannot find the reviews.",
        ]

    sections = [
        (
            "Phase 1 -- Foundation",
            phases["foundation"],
            foundation_limit,
            f"Reviews first ({len(phases['reviews'])} of them), then the {len(phases['landmarks'])} "
            f"paper(s) that at least {LANDMARK_MIN_CITATIONS} other works in this corpus cite. "
            "The second group is chosen by the corpus, not by citation counts from the wider "
            "literature, so it is what this question treats as foundational.",
        ),
        (
            "Phase 2 -- Core evidence",
            phases["core"],
            core_limit,
            f"Established work, published {phases['cutoff']} or earlier, ranked by citations per "
            "year. This is the bulk of the evidence and the phase the evidence table is drawn "
            "from. Works with no year on their index record sit here too.",
        ),
        (
            "Phase 3 -- Frontier",
            phases["frontier"],
            frontier_limit,
            f"Everything published after {phases['cutoff']}, ranked. Separated because recent "
            "work has not had time to accumulate citations and would otherwise sort to the "
            "bottom of every list -- which is the opposite of useful when the question is about "
            "the current state.",
        ),
    ]

    placed = {}
    for title, pairs, limit, rationale in sections:
        shown = pairs[:limit]
        placed[title.split(" -- ")[1].lower()] = len(pairs)
        lines += ["", f"## {title}", "", rationale, ""]
        if not shown:
            lines.append("Nothing was placed in this phase.")
            continue
        lines.append(f"Showing {len(shown)} of {len(pairs)}.")
        lines.append("")
        lines += _render_table(_phase_rows(shown, cite_keys, positions))
        if len(pairs) > len(shown):
            lines.append("")
            lines.append(f"{len(pairs) - len(shown)} further work(s) in this phase are in corpus.jsonl.")

    lines += [
        "",
        "## What this plan does not tell you",
        "",
        "- **It is metadata, not judgement.** Phases come from role, citation structure and",
        "  year. A paper that answers the question exactly and nobody has cited yet sits at the",
        "  bottom of phase 3.",
        "- **`Cites/yr` is a rate, not a quality.** It divides the citation count by the",
        "  paper's age, which corrects the age bias in a raw count and introduces its own: a",
        "  paper from this year is judged on a few months of citations.",
        "- **`Cited here` counts only references the search actually fetched.** Snowballing",
        "  pulls a bounded number per seed, so a zero means the citation was not retrieved, not",
        "  that it does not exist.",
    ]
    if review_queue:
        lines.append(
            f"- **{review_queue} work(s) are not in any phase**, because the screener could not"
            " decide or never saw them. They are in needs_review.md and they are not below."
        )
    if quarantined:
        lines.append(
            f"- **{quarantined} work(s) failed the validation gate** and appear nowhere here."
            " They are in quarantine.md with a reason each."
        )
    lines.append("- **Coverage limits are in run.json** -- the year window, the sources that")
    lines.append("  contributed nothing, and which known items retrieval missed.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return placed
