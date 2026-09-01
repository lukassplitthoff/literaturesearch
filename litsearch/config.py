"""Search configuration.

Configuration is a dataclass built from named constants, not command-line flags.
Edit ``run_search.py`` to change a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Sources that need no credential. NASA ADS is deliberately absent: it needs a token,
# and it is deferred until one exists. See litsearch/sources/ads.py.
DEFAULT_SOURCES = ("openalex", "semanticscholar", "inspire")

# Snowballing stops when a round adds fewer than this fraction of new unique works.
DEFAULT_SATURATION_THRESHOLD = 0.05
DEFAULT_MAX_ROUNDS = 3
DEFAULT_SEEDS_PER_ROUND = 15
# Backward references pulled per seed. Each one is a separate throttled request at
# 1 req/s, so this number is the main driver of how long a snowball round takes.
DEFAULT_REFS_PER_SEED = 5


@dataclass
class SearchConfig:
    """Everything one search run needs to be reproducible."""

    question: str
    queries: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    sources: tuple[str, ...] = DEFAULT_SOURCES
    per_query_limit: int = 50
    max_rounds: int = DEFAULT_MAX_ROUNDS
    seeds_per_round: int = DEFAULT_SEEDS_PER_ROUND
    refs_per_seed: int = DEFAULT_REFS_PER_SEED
    saturation_threshold: float = DEFAULT_SATURATION_THRESHOLD
    known_items: list[str] = field(default_factory=list)
    mailto: str = ""
    out_dir: Path = Path("runs/latest")
    offline: bool = False

    def __post_init__(self) -> None:
        self.out_dir = Path(self.out_dir)
        if not self.queries:
            self.queries = [self.question]

    @property
    def cache_path(self) -> Path:
        return self.out_dir / "index_cache.json"

    def in_year_range(self, year: str | int | None) -> bool:
        """True if a work's year falls inside the configured window."""
        if year in (None, ""):
            return True
        try:
            value = int(str(year)[:4])
        except (TypeError, ValueError):
            return True
        if self.year_from is not None and value < self.year_from:
            return False
        if self.year_to is not None and value > self.year_to:
            return False
        return True
