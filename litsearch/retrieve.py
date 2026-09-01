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
            new_count = corpus.add_all(works, round_index=0)
            print(f"  {name}: {len(works)} hits, {new_count} new (corpus {len(corpus)})")
    return corpus
