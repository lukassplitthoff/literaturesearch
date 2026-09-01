"""The corpus: merge works from many sources into one record per real paper.

Dedup is tiered, strongest identity first: DOI, then arXiv id, then a fuzzy title match
at the same 0.92 ratio bibcheck uses. Merging is additive -- a field is only filled in
if it was empty, so the first source to supply a value wins and nothing is overwritten.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from litsearch.sources.base import Work

TITLE_MATCH_RATIO = 0.92


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
            if work.doi and existing.doi and work.doi != existing.doi:
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
