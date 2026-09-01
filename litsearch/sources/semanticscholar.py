"""Semantic Scholar Graph API: search, citation graph and TLDR summaries.

Free without a key, but throttled hard; a key raises the limit. When it rate-limits,
the run degrades to OpenAlex, which covers the same graph.
"""

from __future__ import annotations

import os

from litsearch.sources.base import Fetcher, Work, clean_arxiv_id, clean_doi

SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/{pid}"
CITATIONS_URL = "https://api.semanticscholar.org/graph/v1/paper/{pid}/citations"

NAME = "semanticscholar"
KEY_ENV = "S2_API_KEY"


def has_key() -> bool:
    """Without a key S2 rate-limits keyless clients to near zero -- see available()."""
    return bool(os.environ.get(KEY_ENV))
FIELDS = "paperId,title,year,abstract,authors,externalIds,venue,citationCount,openAccessPdf"


def to_work(item: dict) -> Work:
    external = item.get("externalIds") or {}
    oa = item.get("openAccessPdf") or {}
    return Work(
        title=item.get("title") or "",
        doi=clean_doi(external.get("DOI")),
        arxiv_id=clean_arxiv_id(external.get("ArXiv")),
        year=str(item.get("year") or ""),
        authors=[a.get("name", "") for a in item.get("authors") or [] if a.get("name")],
        venue=item.get("venue") or "",
        abstract=item.get("abstract") or "",
        cited_by_count=int(item.get("citationCount") or 0),
        oa_pdf_url=oa.get("url") or "",
        sources=[NAME],
        source_ids={NAME: item.get("paperId") or ""},
        references=[r.get("paperId") for r in item.get("references") or [] if r.get("paperId")],
    )


def search(fetcher: Fetcher, query: str, limit: int = 50, year_from=None, year_to=None) -> list[Work]:
    params = {"query": query, "limit": min(limit, 100), "fields": FIELDS}
    if year_from or year_to:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    payload = fetcher.get(f"s2:search:{query}:{year_from}:{year_to}:{limit}", SEARCH_URL, params)
    if not payload:
        return []
    return [to_work(item) for item in payload.get("data") or []]


def references(fetcher: Fetcher, pid: str) -> list[Work]:
    """Backward: the works this one cites."""
    payload = fetcher.get(f"s2:refs:{pid}", PAPER_URL.format(pid=pid), {"fields": f"references.{FIELDS}"})
    if not payload:
        return []
    return [to_work(r) for r in payload.get("references") or [] if r]


def cited_by(fetcher: Fetcher, pid: str, limit: int = 50) -> list[Work]:
    """Forward: the works that cite this one."""
    params = {"fields": f"citingPaper.{FIELDS}".replace("citingPaper.paperId,", "citingPaper."), "limit": limit}
    payload = fetcher.get(f"s2:cites:{pid}:{limit}", CITATIONS_URL.format(pid=pid), params)
    if not payload:
        return []
    return [to_work(row.get("citingPaper") or {}) for row in payload.get("data") or [] if row.get("citingPaper")]
