"""Batched DOI resolution, so validation is not one request per work.

The gate resolves every work against an index, and at the 1 req/s politeness throttle
that made runtime equal to corpus size in seconds -- seventeen minutes for a thousand
works. Both Crossref and OpenAlex accept many DOIs in a single query, so the same
information costs a fiftieth of the requests.

Crossref is the one used for prefetching, deliberately. It ORs repeated same-name filters
(``filter=doi:a,doi:b``), and it is the publisher's own deposited record, which is what
the gate treats as authoritative. OpenAlex offers the same batching via ``doi:a|b|c``, but
most works in a typical run were *found* through OpenAlex, so confirming them against
OpenAlex would be close to circular -- it would show the record we already had, not an
independent corroboration. The OpenAlex batch helper is provided for the works that came
from elsewhere, and is not used by the gate's fast path.

Results are written into the shared on-disk cache under exactly the keys the per-work
lookups use, so a prefetch simply makes the subsequent per-work calls free. Nothing
downstream needs to know whether a record arrived in a batch or on its own, and a DOI the
batch fails to return is left absent rather than cached as a miss -- the per-work path
then asks about it individually, so a batch hiccup can never silently quarantine a work.
"""

from __future__ import annotations

import urllib.parse

CROSSREF_SEARCH = "https://api.crossref.org/works"
OPENALEX_SEARCH = "https://api.openalex.org/works"

# Crossref accepts long filter strings but the URL has to stay sane; 50 keeps the request
# well inside limits and is also OpenAlex's documented ceiling for an OR filter.
BATCH_SIZE = 50


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def crossref_cache_key(doi: str) -> str:
    """The exact key bibcheck's IndexClient.crossref_by_doi uses."""
    return f"crossref:doi:{doi.strip().lower()}"


def prefetch_crossref(client, dois: list[str], batch_size: int = BATCH_SIZE, verbose: bool = True) -> int:
    """Resolve many DOIs in few requests, seeding the client's cache. Returns hits found.

    ``client`` is a bibcheck IndexClient. Its cache is populated in the shape
    ``crossref_by_doi`` expects, so afterwards those calls answer from memory.
    """
    wanted = []
    seen = set()
    for doi in dois:
        if not doi:
            continue
        key = crossref_cache_key(doi)
        if key in client.cache or key in seen:
            continue
        seen.add(key)
        wanted.append(doi.strip())

    if not wanted:
        return 0
    if getattr(client, "offline", False):
        return 0

    found = 0
    for index, chunk in enumerate(_chunks(wanted, batch_size), start=1):
        params = {"filter": ",".join(f"doi:{d}" for d in chunk), "rows": batch_size}
        if getattr(client, "mailto", ""):
            params["mailto"] = client.mailto
        payload = client._get(f"crossref:batch:{index}:{hash(tuple(chunk)) & 0xFFFFFFFF}", CROSSREF_SEARCH, params)
        if not payload:
            continue
        for item in payload.get("message", {}).get("items", []) or []:
            doi = (item.get("DOI") or "").strip().lower()
            if doi:
                # Store under the per-work key, in the per-work response shape.
                client.cache[crossref_cache_key(doi)] = {"payload": {"message": item}}
                found += 1
        if verbose:
            print(f"    crossref batch {index}: {found} resolved so far")

    # Deliberately no negative caching: a DOI the batch did not return is left unknown so
    # the per-work lookup still asks. Recording it as a miss here would let one bad batch
    # quarantine real papers.
    return found


def openalex_batch_url(dois: list[str]) -> str:
    """The OR-filter URL OpenAlex uses for a DOI batch. For sources other than OpenAlex."""
    joined = "|".join(d.strip() for d in dois if d)
    return f"{OPENALEX_SEARCH}?filter=doi:{urllib.parse.quote(joined, safe='|:/.')}"
