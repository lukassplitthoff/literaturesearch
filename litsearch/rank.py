"""Ranking signals over a validated corpus.

Sorting by raw citation count is the obvious thing to do and it is wrong in a specific,
predictable way: a paper accumulates citations for as long as it has existed, so the
ordering is dominated by age. On a search whose question is usually "what is the state of
the art", that buries precisely the work the reader came for.

Two signals fix it, both computed from metadata retrieval already fetched, so neither
costs a request or a token:

* **citations per year** -- the citation count divided by the paper's age. It makes a 2019
  paper with 500 citations and a 2025 paper with 60 comparable.
* **in-corpus citations** -- how many OTHER papers in this corpus cite this one. This is
  the landmark signal, and it is the more interesting of the two because it is local: it
  says a paper is foundational *to this question*, not that it is famous in general.
  Snowballing already fetches the reference lists this needs and then discards them.

Neither is a relevance judgement, and nothing here decides what belongs in the corpus.
Ranking orders a list; screening decides what is on it.
"""

from __future__ import annotations

from datetime import date

from litsearch.sources.base import Work

# A paper published in the current year is treated as one year old rather than zero, so
# the division is defined and a brand-new paper is not handed an infinite rate.
MIN_AGE_YEARS = 1


def year_of(work: Work) -> int | None:
    """The work's publication year as an int, or None when it is missing or unparseable."""
    try:
        return int(str(work.year)[:4])
    except (TypeError, ValueError):
        return None


def in_corpus_citations(works: list[Work]) -> list[int]:
    """For each work, how many others in the list cite it.

    Reference lists arrive as native ids of whichever source supplied them, so the lookup
    is built from every source id every work carries. Keying on the bare id string is safe
    because the id formats are disjoint -- OpenAlex ids start with 'W', Semantic Scholar
    ids are 40 hex characters, INSPIRE control numbers are plain integers -- so two
    sources cannot claim the same string.

    A self-citation is ignored, and two reference entries that dedup onto one record count
    once, so the number is "how many distinct corpus members cite this".
    """
    position_of: dict[str, int] = {}
    for position, work in enumerate(works):
        for native_id in work.source_ids.values():
            if native_id:
                position_of[str(native_id)] = position

    counts = [0] * len(works)
    for position, work in enumerate(works):
        seen: set[int] = set()
        for reference in work.references:
            target = position_of.get(str(reference))
            if target is None or target == position or target in seen:
                continue
            seen.add(target)
            counts[target] += 1
    return counts


def citations_per_year(work: Work, now_year: int) -> float:
    """Citation count divided by the paper's age in years.

    0.0 when the year is unknown. That sorts such a work last rather than dropping it:
    missing metadata is a reason to look at a paper later, not a reason to lose it.
    """
    year = year_of(work)
    if year is None:
        return 0.0
    age = max(MIN_AGE_YEARS, now_year - year + 1)
    return work.cited_by_count / age


def signals(works: list[Work], now_year: int | None = None) -> list[dict]:
    """One row of ranking signals per work, in the order given."""
    now_year = now_year or date.today().year
    local = in_corpus_citations(works)
    return [
        {
            "year": year_of(work),
            "cited_by_count": work.cited_by_count,
            "in_corpus_citations": local[position],
            "citations_per_year": round(citations_per_year(work, now_year), 2),
        }
        for position, work in enumerate(works)
    ]


def rank(works: list[Work], now_year: int | None = None) -> list[tuple[Work, dict]]:
    """Works paired with their signals, best first.

    Ordered by citations per year, then by in-corpus citations, then by the raw count. The
    first term is the one that undoes the age bias; the other two only break ties. This is
    a heuristic over metadata and says nothing about whether a paper answers the question.
    """
    paired = list(zip(works, signals(works, now_year)))
    paired.sort(
        key=lambda pair: (
            pair[1]["citations_per_year"],
            pair[1]["in_corpus_citations"],
            pair[1]["cited_by_count"],
        ),
        reverse=True,
    )
    return paired
