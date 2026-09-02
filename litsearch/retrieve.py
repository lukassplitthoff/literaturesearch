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

    if cfg.seed_dois:
        print(f"seed papers ({len(cfg.seed_dois)} DOIs)")
        corpus.add_all(from_seed_dois(fetcher, cfg.seed_dois), round_index=0)

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


def from_seed_dois(fetcher: Fetcher, dois: tuple[str, ...]) -> list:
    """Resolve known papers by DOI, to be used as snowball seeds.

    A different way to start a search: instead of describing the topic in words and hoping
    the vocabulary matches, name a paper that is definitively about it and walk its
    citation graph. This is usually how a researcher actually works -- "find me things
    like this one" -- and it sidesteps the vocabulary problem entirely, because the graph
    does not care what words the papers use.

    Resolved works are marked as round 0, so they seed expansion exactly as query hits do.
    A DOI that does not resolve is reported and skipped, never silently dropped.
    """
    works = []
    for doi in dois:
        clean = (doi or "").strip()
        if not clean:
            continue
        payload = fetcher.get(
            f"openalex:doi:{clean.lower()}",
            openalex.SEARCH_URL,
            {"filter": f"doi:{clean}", "select": openalex.FIELDS},
        )
        results = (payload or {}).get("results") or []
        if not results:
            print(f"  [WARN] seed DOI {clean} did not resolve in OpenAlex; skipped")
            continue
        work = openalex.to_work(results[0])
        work.is_seed = True
        works.append(work)
        print(f"  seed: {work.title[:66]} ({work.cited_by_count} citations, {len(work.references)} refs)")
    return works


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
    if name == openalex.NAME:
        if not openalex.api_key():
            return (
                "OpenAlex has metered its API since Feb 2026. Without a key the daily budget "
                "is about $0.10 and a search costs about $0.001, so roughly a hundred searches "
                f"before HTTP 429. Set {openalex.KEY_ENV} (free key, about a minute, at "
                "openalex.org/settings/api) for ten times that. The budget resets at midnight UTC."
            )
        return (
            "a key is set, so this is a spent daily budget, an outage, or a query the index "
            "genuinely has nothing for. The budget resets at midnight UTC."
        )
    return "check the query syntax for this source, or whether the service is reachable."
