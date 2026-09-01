"""Search configuration.

Configuration is a dataclass built from named constants, not command-line flags.
Edit ``run_search.py`` to change a run.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Where run outputs go. Search results are data, not source: they are large, they change
# on every run, and they must never end up in a commit. The default is therefore OUTSIDE
# the repository entirely, so a stray `git add -A` cannot pick them up.
OUT_DIR_ENV = "LITSEARCH_OUT_DIR"
DEFAULT_OUT_ROOT = Path.home() / "litsearch-runs"


def out_root() -> Path:
    """The directory runs are written under: $LITSEARCH_OUT_DIR, else ~/litsearch-runs."""
    configured = os.environ.get(OUT_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_OUT_ROOT


def run_dir(name: str) -> Path:
    """The output directory for one named run."""
    return out_root() / name


def _repo_root() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None


def warn_if_inside_repo(path: Path) -> str:
    """Return a warning if outputs would land inside the git repository, else ''.

    Not an error -- someone may deliberately want a run beside the code -- but it is
    always worth saying out loud, because the failure mode is committing a corpus.
    """
    repo = _repo_root()
    if repo is None:
        return ""
    try:
        Path(path).resolve().relative_to(repo.resolve())
    except ValueError:
        return ""
    return (
        f"output directory {path} is inside the git repository at {repo}. "
        f"Run outputs must never be committed. Set {OUT_DIR_ENV} to a path outside the repo."
    )

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
    # Distinct query terms a snowballed work must show to be admitted. 0 disables the guard.
    min_term_hits: int = 2
    known_items: list[str] = field(default_factory=list)
    # Known papers to seed from, by DOI. An alternative to describing the topic in words.
    seed_dois: tuple[str, ...] = ()
    mailto: str = ""
    out_dir: Path = field(default_factory=lambda: run_dir("latest"))
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
