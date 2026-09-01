"""The Work record and the shared HTTP fetcher.

There is exactly one HTTP layer in this repository: ``bibcheck.verify.IndexClient``,
which already provides on-disk caching, a 1 req/s throttle, retry with backoff, a
polite-pool User-Agent and an offline replay mode. ``Fetcher`` wraps it rather than
introducing a second one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bibcheck.keys import normalize_title
from bibcheck.verify import IndexClient

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def clean_doi(value: str | None) -> str | None:
    """Strip a DOI down to its bare form, or None if there is not one."""
    if not value:
        return None
    text = str(value).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.strip()
    return text if text.startswith("10.") else None


def clean_arxiv_id(value: str | None) -> str | None:
    """Pull a bare arXiv id out of a URL, a versioned id or an arXiv DOI."""
    if not value:
        return None
    match = ARXIV_ID_RE.search(str(value))
    return match.group(1) if match else None


@dataclass
class Work:
    """One paper, normalised across every source that saw it."""

    title: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    year: str = ""
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    volume: str = ""
    pages: str = ""
    abstract: str = ""
    cited_by_count: int = 0
    oa_pdf_url: str = ""
    landing_url: str = ""
    # provenance: which source supplied this, and under which native id
    sources: list[str] = field(default_factory=list)
    source_ids: dict[str, str] = field(default_factory=dict)
    # citation graph, as native ids of the supplying source
    references: list[str] = field(default_factory=list)
    # bookkeeping filled in later in the pipeline
    found_in_round: int = 0
    screen: str = ""
    screen_reason: str = ""
    validation: str = ""
    validation_source: str = ""

    @property
    def norm_title(self) -> str:
        return normalize_title(self.title)

    def identity(self) -> tuple[str, str]:
        """The strongest available identity, as (kind, value). Used for dedup."""
        if self.doi:
            return ("doi", self.doi)
        if self.arxiv_id:
            return ("arxiv", self.arxiv_id)
        return ("title", self.norm_title)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "year": self.year,
            "authors": self.authors,
            "venue": self.venue,
            "volume": self.volume,
            "pages": self.pages,
            "abstract": self.abstract,
            "cited_by_count": self.cited_by_count,
            "oa_pdf_url": self.oa_pdf_url,
            "landing_url": self.landing_url,
            "sources": self.sources,
            "source_ids": self.source_ids,
            "references": self.references,
            "found_in_round": self.found_in_round,
            "screen": self.screen,
            "screen_reason": self.screen_reason,
            "validation": self.validation,
            "validation_source": self.validation_source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Work":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class Fetcher:
    """Cached, throttled HTTP, borrowed wholesale from bibcheck."""

    def __init__(self, cache_path: Path | None = None, mailto: str = "", offline: bool = False) -> None:
        self.client = IndexClient(cache_path=cache_path, mailto=mailto, offline=offline)

    def get(self, cache_key: str, url: str, params: dict | None = None) -> dict | None:
        """GET returning parsed JSON, or None on cache miss while offline / on failure."""
        # _get is bibcheck-internal by name only; this is the single intentional reach
        # into it, and exists so that no second HTTP layer gets written.
        return self.client._get(cache_key, url, params=params)

    @property
    def errors(self) -> list[str]:
        return self.client.network_errors

    def save_cache(self) -> None:
        self.client.save_cache()
