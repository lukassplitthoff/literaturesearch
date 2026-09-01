# literaturesearch

Two packages that together turn a research question into a **validated** bibliography.

| Package | Status | What it does |
| --- | --- | --- |
| [`bibcheck/`](bibcheck/README.md) | working, 154 tests | Takes a `.bib` file and writes a cleaned, publication-ready copy: keys unified to `LastnameYEAR`, entries sorted, completeness checked, and every entry verified against Crossref, DataCite, arXiv and OpenAlex. |
| `litsearch/` | spine working, 43 tests | AI-assisted literature search harness. Question in, validated corpus out. Retrieval, dedup, snowballing, the validation gate and the bibliography exporter are built and tested; screening and evidence extraction run through the Claude subagents in `.claude/agents/`. See [Design](#litsearch-design). |

The design principle that connects them: **nothing reaches the output that has not been
resolved against an authoritative index.** `bibcheck` is not a companion tool to the search,
it is the gate the search results must pass through.

## Install

```bash
pip install -e .          # runtime: requests only
pip install -e ".[dev]"   # adds pytest and pre-commit
```

Verified from a clean clone into a fresh virtualenv on Windows (Python 3.12):
runtime install, 252 tests, and a live search all pass.

**Windows note:** installing the `[dev]` extra can fail with
`No such file or directory` on a long path, because `pre-commit` ships deeply
nested fixture files. Either [enable long-path
support](https://pip.pypa.io/warnings/enable-long-paths) or clone somewhere
short (`C:\src\literaturesearch`). The runtime install is unaffected.

## bibcheck quick start

```bash
python -m bibcheck.main path/to/refs.bib --no-write                        # report only
python -m bibcheck.main path/to/refs.bib --out-dir ./out                   # write cleaned copy
python -m bibcheck.main path/to/refs.bib --verify --mailto you@example.com # cross-check indexes
```

Exit code: `0` clean, `1` warnings, `2` errors. Full documentation in
[`bibcheck/README.md`](bibcheck/README.md).

```bash
python -m pytest          # 197 tests, no sockets opened
```

## litsearch quick start

```bash
python run_search.py      # edit the CONFIG block at the top first
```

Writes `corpus.jsonl`, `refs.bib`, `shortlist.md`, `quarantine.md` and `run.json` into
`runs/<name>/`. Only works that passed the validation gate reach `refs.bib`.

Conversationally, `/litsearch "<your question>"` loads the skill in `.claude/skills/`,
which drives the same pipeline and adds the screening and extraction stages through the
`lit-scout`, `lit-screener` and `lit-extractor` subagents.

## litsearch design

A question, hypothesis or topic goes in; a validated corpus plus an evidence table and a
synthesis come out. Eight stages, with deterministic Python and the language model kept
strictly apart -- Python does HTTP, dedup, caching and validation and contains no model call;
Claude does language work and never touches HTTP. Every stage boundary is a file on disk, so
any stage can be re-run or inspected alone.

| # | Stage | Who | Notes |
| --- | --- | --- | --- |
| 0 | Frame | Claude (scout) | Question -> per-source queries, inclusion/exclusion criteria, proposed known-item papers |
| 1 | Retrieve | Python | Fan out across enabled sources. Cached, throttled, offline-replayable |
| 2 | Merge | Python | Dedup DOI -> arXiv id -> fuzzy title. One record per paper |
| 3 | Snowball | Python | Backward references + forward citations, until saturation |
| 4 | Screen | Claude (cheap) | Title+abstract vs criteria -> include / exclude / unsure |
| 5 | **Validate** | bibcheck | **The hard gate.** Only `verified` works proceed |
| 6 | Extract | Claude (strong) | Structured fields, each with a mandatory `source_quote` |
| 7 | Render | Python | bibtex, evidence table, synthesis, quarantine, run log |

### Sources

Free and keyless unless noted. Google Scholar is deliberately **not** a programmatic source:
it has no API, blocks scrapers, and scraping it violates its terms.

| Source | Role |
| --- | --- |
| OpenAlex | Search + citation graph + open-access PDF locations |
| Semantic Scholar | Search + citation graph + TLDR summaries (optional key raises rate limit) |
| arXiv | Preprint search and full text |
| Crossref / DataCite | Publisher-deposited record; the validation backbone |
| INSPIRE-HEP | Physics-curated |
| NASA ADS | Strongest physics coverage (**requires a free API token**) |

### Why the citation graph matters

Keyword search alone systematically misses papers that use different vocabulary for the same
idea. Recall comes from snowballing: follow references backward and citations forward from
seed papers, and keep going until new-unique-papers-per-round falls below a threshold. Both
OpenAlex and Semantic Scholar expose this for free, so it costs no model tokens.

### Anti-hallucination

Two independent mechanisms, neither relying on the model behaving well:

1. **The validation gate.** A fabricated reference has no DOI that Crossref knows, no arXiv id
   that resolves, and no title OpenAlex can confirm, so it is quarantined by construction.
2. **Mandatory source quotes.** Every extracted field carries the sentence it came from. A
   field that cannot be quoted is recorded as `null`, never inferred.

Quarantined entries are reported with reasons -- never silently dropped, never silently kept.

## Configuration

API tokens go in `.env` (gitignored), never in source:

```
ADS_API_TOKEN=...          # https://ui.adsabs.harvard.edu -> Account -> API Token
S2_API_KEY=...             # optional, raises the Semantic Scholar rate limit
CROSSREF_MAILTO=you@example.com
```

`WebSearch` and `WebFetch` are not pre-granted. The tracked `.claude/settings.json` carries
only `deny` and `ask` rules; add web permissions to your own gitignored
`.claude/settings.local.json`.

## Provenance

`bibcheck` was developed inside a private measurement-framework repository and moved here on
2026-09-01, from commit `38b54122e`. It had no dependencies on that repository.
