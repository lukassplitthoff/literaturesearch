"""The corpus: merge works from many sources into one record per real paper.

Dedup is tiered, strongest identity first: DOI, then arXiv id, then a fuzzy title match
at the same 0.92 ratio bibcheck uses. Merging is additive -- a field is only filled in
if it was empty, so the first source to supply a value wins and nothing is overwritten.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from bibcheck.keys import first_author_surname
from bibcheck.verify import is_arxiv_doi
from litsearch.sources.base import Work

TITLE_MATCH_RATIO = 0.92


def _publisher_doi(work: Work) -> str | None:
    """The work's DOI, unless it is arXiv's own -- which identifies a preprint, not a venue."""
    if not work.doi or is_arxiv_doi(work.doi):
        return None
    return work.doi


def _same_work_despite_doi(left: Work, right: Work) -> bool:
    """Two records with different publisher DOIs that are nonetheless one paper.

    Requires an EXACT normalised title, the same year and the same first-author surname.
    All three, because each alone is common: many papers share a year, sequels share an
    author, and near-identical titles are exactly the case the DOI conflict rule protects.
    """
    if left.norm_title != right.norm_title or not left.norm_title:
        return False
    if left.year and right.year and left.year != right.year:
        return False
    # bibcheck's parser rather than a naive split: indexes give the same person as both
    # "Yao Lu" and "Lu, Yao", and taking the last token yields "Lu" for one and "Yao" for
    # the other. That mismatch let a known duplicate through twice.
    surnames = [first_author_surname(work.authors[0]).lower() for work in (left, right) if work.authors]
    return len(surnames) == 2 and surnames[0] and surnames[0] == surnames[1]


def title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


class Corpus:
    """A set of Works, deduplicated as they are added."""

    def __init__(self) -> None:
        self.works: list[Work] = []
        self._by_doi: dict[str, Work] = {}
        self._by_arxiv: dict[str, Work] = {}

    def __len__(self) -> int:
        return len(self.works)

    def _find(self, work: Work) -> Work | None:
        if work.doi and work.doi in self._by_doi:
            return self._by_doi[work.doi]
        if work.arxiv_id and work.arxiv_id in self._by_arxiv:
            return self._by_arxiv[work.arxiv_id]
        norm = work.norm_title
        if not norm:
            return None
        for existing in self.works:
            # A conflicting identifier settles it: two works with different DOIs are
            # different papers, however alike their titles look. Without this, near-identical
            # titles -- "... Part I" and "... Part II", a paper and its erratum, two devices
            # from one group -- collapse into a single record and one of them is lost.
            #
            # An arXiv DOI (10.48550/arXiv.*) is exempt, because it is not a publisher DOI:
            # it is arXiv's own registration of the preprint, so the preprint and the
            # published article legitimately carry different DOIs while being one paper.
            if _publisher_doi(work) and _publisher_doi(existing) and work.doi != existing.doi:
                # ...unless they are unmistakably the same paper. Indexes do carry two
                # publisher DOIs for one article -- a correction record, or a second
                # deposit -- and a *fuzzy* title match is far too weak to override a DOI
                # conflict. An exact normalised title plus the same year and the same
                # first author is not.
                if not _same_work_despite_doi(work, existing):
                    continue
            if work.arxiv_id and existing.arxiv_id and work.arxiv_id != existing.arxiv_id:
                continue
            if title_similarity(norm, existing.norm_title) >= TITLE_MATCH_RATIO:
                return existing
        return None

    def _index(self, work: Work) -> None:
        if work.doi:
            self._by_doi[work.doi] = work
        if work.arxiv_id:
            self._by_arxiv[work.arxiv_id] = work

    def add(self, work: Work) -> tuple[Work, bool]:
        """Add or merge. Returns (record, is_new)."""
        if not work.title:
            return work, False
        existing = self._find(work)
        if existing is None:
            self.works.append(work)
            self._index(work)
            return work, True
        merge(existing, work)
        self._index(existing)
        return existing, False

    def add_all(self, works: list[Work], round_index: int = 0) -> int:
        """Add many, returning how many were new."""
        new_count = 0
        for work in works:
            record, is_new = self.add(work)
            if is_new:
                record.found_in_round = round_index
                new_count += 1
        return new_count

    def top_by_citations(self, count: int, exclude_rounds: tuple[int, ...] = ()) -> list[Work]:
        pool = [w for w in self.works if w.found_in_round not in exclude_rounds]
        return sorted(pool, key=lambda w: w.cited_by_count, reverse=True)[:count]

    def screened_in(self) -> list[Work]:
        """Works a screener read the abstract of and kept."""
        return [work for work in self.works if work.screen == "include"]

    def seed_candidates(
        self,
        count: int,
        from_rounds: tuple[int, ...] = (0,),
        seen: set | None = None,
        prefer_screened: bool = True,
    ) -> list[Work]:
        """Pick snowball seeds, most cited first.

        Seeds are drawn, in order of preference:

        1. Works a screener read and marked ``include`` -- an abstract-level judgement
           that the paper is actually about the question. This is the strongest signal
           available and it makes each expansion round better than the last.
        2. Otherwise the round-0 direct query hits, which the search engines
           relevance-ranked.

        Never from the whole corpus. Ranking the whole corpus by citation count seeds on the most-cited
        paper present, which after one round is a famous review from an adjacent field
        rather than anything on topic. Expanding that drags in its entire neighbourhood,
        and the next round does it again: the corpus grows without becoming more relevant,
        and `new_fraction` can never fall because the neighbourhood is effectively endless.

        Anchoring seeds to the query hits keeps expansion pointed at the question. `seen`
        carries the ids already expanded, so no seed is paid for twice.
        """
        seen = seen or set()

        # Works the user named by DOI come first, always, however lightly cited they are.
        # Ranking them alongside query hits by citation count meant a seed with 82 citations
        # lost its place to famous papers the queries dragged in -- so a DOI-seeded search
        # never walked the graph of the very paper it was seeded with, which is the entire
        # point of naming one.
        explicit = [w for w in self.works if w.is_seed and id(w) not in seen]

        screened = self.screened_in() if prefer_screened else []
        if screened:
            # Best case: a screener has read these abstracts and judged them relevant.
            # Seeding from them expands outward from papers that are actually about the
            # question, rather than from whatever the keyword guard let through -- the
            # difference between "shares two query words" and "reports the measurement".
            pool = [work for work in screened if id(work) not in seen]
        else:
            # No screening yet: fall back to the direct query hits, which the search
            # engines relevance-ranked. Never the whole corpus -- see the docstring.
            pool = [
                work
                for work in self.works
                if work.found_in_round in from_rounds and id(work) not in seen
            ]
        ranked = sorted(pool, key=lambda w: w.cited_by_count, reverse=True)
        # Explicit seeds are prepended, not merged into the ranking, and de-duplicated
        # against it so one cannot be counted twice.
        explicit_ids = {id(w) for w in explicit}
        return (explicit + [w for w in ranked if id(w) not in explicit_ids])[:count]

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for work in self.works:
                handle.write(json.dumps(work.as_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def read_jsonl(cls, path: Path) -> "Corpus":
        corpus = cls()
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    corpus.add(Work.from_dict(json.loads(line)))
        return corpus


def merge(target: Work, other: Work) -> None:
    """Fold `other` into `target` without overwriting anything already present."""
    for name in ("doi", "arxiv_id", "year", "venue", "volume", "pages", "abstract", "oa_pdf_url", "landing_url"):
        if not getattr(target, name) and getattr(other, name):
            setattr(target, name, getattr(other, name))
    if not target.authors and other.authors:
        target.authors = other.authors
    if len(other.title) > len(target.title):
        target.title = other.title
    target.cited_by_count = max(target.cited_by_count, other.cited_by_count)
    for source in other.sources:
        if source not in target.sources:
            target.sources.append(source)
    target.source_ids.update(other.source_ids)
    if other.references and not target.references:
        target.references = other.references
