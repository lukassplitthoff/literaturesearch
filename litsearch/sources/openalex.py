"""OpenAlex: keyword search, the citation graph, and open-access PDF locations.

Free, no key. A ``mailto`` puts requests in the polite pool.
"""

from __future__ import annotations

from litsearch.sources.base import Fetcher, Work, clean_arxiv_id, clean_doi

SEARCH_URL = "https://api.openalex.org/works"
WORK_URL = "https://api.openalex.org/works/{oid}"

NAME = "openalex"
FIELDS = (
    "id,doi,display_name,publication_year,authorships,primary_location,"
    "abstract_inverted_index,cited_by_count,best_oa_location,referenced_works,biblio"
)


def _abstract(inverted: dict | None) -> str:
    """OpenAlex ships abstracts as an inverted index; put the words back in order."""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in inverted.items():
        for spot in spots:
            positions.append((spot, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _short_id(oid: str | None) -> str:
    return str(oid).rsplit("/", 1)[-1] if oid else ""


def to_work(item: dict) -> Work:
    oid = _short_id(item.get("id"))
    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    best_oa = item.get("best_oa_location") or {}
    doi = clean_doi(item.get("doi"))
    biblio = item.get("biblio") or {}
    first_page = biblio.get("first_page") or ""
    last_page = biblio.get("last_page") or ""
    # A single-page article has first == last, and "045014--045014" is not a page range --
    # Crossref deposits it as "045014", and bibcheck rightly reports the difference.
    if first_page and last_page and first_page != last_page:
        pages = f"{first_page}--{last_page}"
    else:
        pages = first_page or last_page
    return Work(
        title=item.get("display_name") or "",
        doi=doi,
        arxiv_id=clean_arxiv_id(doi if doi and "arxiv" in doi else location.get("landing_page_url")),
        year=str(item.get("publication_year") or ""),
        authors=[
            (a.get("author") or {}).get("display_name", "")
            for a in item.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        venue=source.get("display_name") or "",
        volume=str(biblio.get("volume") or ""),
        pages=pages,
        abstract=_abstract(item.get("abstract_inverted_index")),
        cited_by_count=int(item.get("cited_by_count") or 0),
        oa_pdf_url=best_oa.get("pdf_url") or "",
        landing_url=location.get("landing_page_url") or "",
        sources=[NAME],
        source_ids={NAME: oid},
        references=[_short_id(r) for r in item.get("referenced_works") or []],
    )


def search(fetcher: Fetcher, query: str, limit: int = 50, year_from=None, year_to=None) -> list[Work]:
    params = {"search": query, "per-page": min(limit, 200), "select": FIELDS}
    if year_from or year_to:
        params["filter"] = f"publication_year:{year_from or 1800}-{year_to or 2100}"
    payload = fetcher.get(f"openalex:search:{query}:{year_from}:{year_to}:{limit}", SEARCH_URL, params)
    if not payload:
        return []
    return [to_work(item) for item in payload.get("results") or []]


def by_id(fetcher: Fetcher, oid: str) -> Work | None:
    payload = fetcher.get(f"openalex:work:{oid}", WORK_URL.format(oid=oid), {"select": FIELDS})
    return to_work(payload) if payload else None


def cited_by(fetcher: Fetcher, oid: str, limit: int = 50) -> list[Work]:
    """Forward citations: the works that cite this one."""
    params = {"filter": f"cites:{oid}", "per-page": min(limit, 200), "select": FIELDS}
    payload = fetcher.get(f"openalex:cites:{oid}:{limit}", SEARCH_URL, params)
    if not payload:
        return []
    return [to_work(item) for item in payload.get("results") or []]
