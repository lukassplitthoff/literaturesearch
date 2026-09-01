"""INSPIRE-HEP: physics-curated literature. Free, no key."""

from __future__ import annotations

from litsearch.sources.base import Fetcher, Work, clean_arxiv_id, clean_doi

SEARCH_URL = "https://inspirehep.net/api/literature"

NAME = "inspire"
FIELDS = "titles,authors,dois,arxiv_eprints,publication_info,citation_count,abstracts,control_number"


def to_work(item: dict) -> Work:
    meta = item.get("metadata") or item
    titles = meta.get("titles") or [{}]
    dois = meta.get("dois") or []
    eprints = meta.get("arxiv_eprints") or []
    pub = (meta.get("publication_info") or [{}])[0]
    abstracts = meta.get("abstracts") or []
    year = str(pub.get("year") or "")
    if not year and meta.get("earliest_date"):
        year = str(meta["earliest_date"])[:4]
    return Work(
        title=titles[0].get("title", "") if titles else "",
        doi=clean_doi(dois[0].get("value") if dois else None),
        arxiv_id=clean_arxiv_id(eprints[0].get("value") if eprints else None),
        year=year,
        authors=[a.get("full_name", "") for a in (meta.get("authors") or [])[:50] if a.get("full_name")],
        venue=pub.get("journal_title") or "",
        abstract=abstracts[0].get("value", "") if abstracts else "",
        cited_by_count=int(meta.get("citation_count") or 0),
        sources=[NAME],
        source_ids={NAME: str(meta.get("control_number") or "")},
    )


def search(fetcher: Fetcher, query: str, limit: int = 50, year_from=None, year_to=None) -> list[Work]:
    q = query
    if year_from or year_to:
        q = f"{query} and de {year_from or 1800}->{year_to or 2100}"
    params = {"q": q, "size": min(limit, 100), "fields": FIELDS, "sort": "mostcited"}
    payload = fetcher.get(f"inspire:search:{q}:{limit}", SEARCH_URL, params)
    if not payload:
        return []
    hits = (payload.get("hits") or {}).get("hits") or []
    return [to_work(item) for item in hits]
