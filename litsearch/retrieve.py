"""Stage 1: fan a set of queries out across the enabled sources."""

from __future__ import annotations

from litsearch.config import SearchConfig
from litsearch.corpus import Corpus
from litsearch.sources import ads, inspire, openalex, semanticscholar
from litsearch.sources.base import Fetcher

SOURCE_MODULES = {
    openalex.NAME: openalex,
    semanticscholar.NAME: semanticscholar,
    inspire.NAME: inspire,
    ads.NAME: ads,
}


def run(fetcher: Fetcher, cfg: SearchConfig) -> Corpus:
    corpus = Corpus()
    hits_per_source = {name: 0 for name in cfg.sources}

    for query in cfg.queries:
        print(f"query: {query}")
        for name in cfg.sources:
            module = SOURCE_MODULES.get(name)
            if module is None:
                print(f"  [skip] unknown source '{name}'")
                continue
            try:
                works = module.search(
                    fetcher, query, limit=cfg.per_query_limit,
                    year_from=cfg.year_from, year_to=cfg.year_to,
                )
            except Exception as exc:  # a dead source degrades the run, never aborts it
                print(f"  [warn] {name} failed: {type(exc).__name__}: {exc}")
                continue
            works = [w for w in works if cfg.in_year_range(w.year)]
            hits_per_source[name] += len(works)
            new_count = corpus.add_all(works, round_index=0)
            print(f"  {name}: {len(works)} hits, {new_count} new (corpus {len(corpus)})")

    # A source that returned nothing on every query is almost never a source with no
    # relevant papers -- it is blocked, throttled or misconfigured. Reporting that as a
    # bare "0 hits" per query hides a hole in the search, so say it plainly.
    for name, total in hits_per_source.items():
        if total == 0 and name in SOURCE_MODULES:
            print(f"  [WARN] {name} returned nothing across all {len(cfg.queries)} queries")
            print(f"         {_why_empty(name)}")
    return corpus


def _why_empty(name: str) -> str:
    """The usual explanation, so the warning is actionable rather than merely alarming."""
    if name == semanticscholar.NAME:
        if not semanticscholar.has_key():
            return (
                f"Semantic Scholar rate-limits keyless clients hard (HTTP 429). Set "
                f"{semanticscholar.KEY_ENV} to raise the limit; OpenAlex covers the same "
                f"citation graph meanwhile, so recall is reduced rather than broken."
            )
        return "a key is set, so this is more likely a transient outage or a changed API."
    if name == ads.NAME:
        return f"NASA ADS is not implemented yet and needs {ads.TOKEN_ENV}; see litsearch/sources/ads.py."
    return "check the query syntax for this source, or whether the service is reachable."
