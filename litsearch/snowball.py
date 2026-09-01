"""Citation-graph expansion, with a saturation stopping criterion.

Keyword search alone systematically misses papers that use different vocabulary for the
same idea. Recall comes from following the graph: forward citations (who cited this) and
backward references (what this cited). Both are free, so this costs no model tokens.

A round stops the loop when the fraction of newly discovered works falls below the
configured threshold -- that is the signal that the search has saturated.
"""

from __future__ import annotations

from dataclasses import dataclass

from litsearch.config import SearchConfig
from litsearch.corpus import Corpus
from litsearch.sources import openalex, semanticscholar
from litsearch.sources.base import Fetcher


@dataclass
class RoundStats:
    round_index: int
    seeds: int
    found: int
    new: int
    corpus_size: int

    @property
    def new_fraction(self) -> float:
        return self.new / self.corpus_size if self.corpus_size else 0.0

    def as_dict(self) -> dict:
        return {
            "round": self.round_index,
            "seeds": self.seeds,
            "found": self.found,
            "new": self.new,
            "corpus_size": self.corpus_size,
            "new_fraction": round(self.new_fraction, 4),
        }


def expand(fetcher: Fetcher, corpus: Corpus, cfg: SearchConfig) -> list[RoundStats]:
    """Run snowball rounds until saturation or the round cap. Returns per-round stats.

    Seeds are drawn from the direct query hits (round 0) and each is expanded at most
    once. See ``Corpus.seed_candidates`` for why seeding on the whole corpus is wrong.
    """
    stats: list[RoundStats] = []
    expanded: set = set()
    for round_index in range(1, cfg.max_rounds + 1):
        seeds = corpus.seed_candidates(cfg.seeds_per_round, seen=expanded)
        if not seeds:
            print("  no unexpanded seeds left; stopping")
            break
        expanded.update(id(seed) for seed in seeds)
        harvested = []
        for seed in seeds:
            oid = seed.source_ids.get(openalex.NAME)
            if oid:
                harvested.extend(openalex.cited_by(fetcher, oid, limit=cfg.per_query_limit))
                for ref_id in seed.references[: cfg.refs_per_seed]:
                    work = openalex.by_id(fetcher, ref_id)
                    if work:
                        harvested.append(work)
            pid = seed.source_ids.get(semanticscholar.NAME)
            if pid and semanticscholar.NAME in cfg.sources:
                harvested.extend(semanticscholar.references(fetcher, pid))

        harvested = [w for w in harvested if cfg.in_year_range(w.year)]
        new_count = corpus.add_all(harvested, round_index=round_index)
        stat = RoundStats(round_index, len(seeds), len(harvested), new_count, len(corpus))
        stats.append(stat)
        print(
            f"  round {round_index}: {len(seeds)} seeds -> {len(harvested)} found, "
            f"{new_count} new, corpus {len(corpus)} (new fraction {stat.new_fraction:.3f})"
        )
        if stat.new_fraction < cfg.saturation_threshold:
            print(f"  saturated: new fraction below {cfg.saturation_threshold}")
            break
    return stats
