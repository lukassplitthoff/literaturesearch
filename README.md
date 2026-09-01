# literaturesearch

Turns a research question into a **validated** bibliography and an evidence table, where
every cited work has been checked against an authoritative index and every extracted
number carries the sentence it came from.

## Start here

```bash
git clone https://github.com/lukassplitthoff/literaturesearch.git
cd literaturesearch
pip install -e .            # runtime dependency: requests, and nothing else
python -m pytest            # 288 tests, no sockets opened
```

**Then do the thing that is useful in one command.** If you have a `.bib` file:

```bash
python -m bibcheck.main path/to/refs.bib --no-write
```

That reports, without writing anything, what is wrong with your bibliography: missing
fields, duplicate entries, malformed DOIs, keys that do not follow `LastnameYEAR`. Add
`--verify` and it also checks every entry against Crossref, DataCite and arXiv, and tells
you which preprints now have a published version.

```
  entries      : 24
  keys renamed : 24
  errors       : 0
  warnings     : 21
```

Exit code `0` clean, `1` warnings, `2` errors. Nothing is modified: `bibcheck` writes a
cleaned *copy* when you ask it to with `--out-dir`, never in place.

**To run a literature search**, read the worked example first -- it is a real completed
search with its numbers, and it is shorter than this README:

- [examples/parametric_gates_bosonic_cavities/](examples/parametric_gates_bosonic_cavities/README.md)

Then copy `run_search.py`, edit the `SearchSpec`, and run it. A full search is three
invocations, because the two model-driven stages hand off through files:

```bash
python run_search.py     # retrieve, validate, write screening batches
#   an agent answers <out>/screen/verdicts.jsonl
python run_search.py     # apply verdicts, write extraction tasks
#   an agent answers <out>/extract/rows.jsonl
python run_search.py     # write evidence.csv and the final refs.bib
```

Inside Claude Code, `/litsearch "<your question>"` drives all three and fills in the
agent steps for you. Outputs go to `$LITSEARCH_OUT_DIR/<name>/`, default
`~/litsearch-runs/<name>/` -- outside the repository, because run results are data.

**Windows note:** `pip install -e ".[dev]"` can fail with `No such file or directory` on a
long path, because `pre-commit` ships deeply nested fixture files. Either [enable
long-path support](https://pip.pypa.io/warnings/enable-long-paths) or clone somewhere
short. The runtime install is unaffected.

## What is in here

| Package | Status | What it does |
| --- | --- | --- |
| [`bibcheck/`](bibcheck/README.md) | working, 157 tests | Takes a `.bib` file and writes a cleaned, publication-ready copy: keys unified to `LastnameYEAR`, entries sorted, completeness checked, every entry verified against Crossref, DataCite, arXiv and OpenAlex. Useful on its own. |
| [`litsearch/`](litsearch/) | working, 131 tests | The search harness. Retrieval, dedup, snowballing, the validation gate and the exporters are deterministic Python; screening and evidence extraction run through the subagents in `.claude/agents/`. |
| [`benchmark/`](benchmark/benchmark.py) | | A regression benchmark over a frozen corpus for the deterministic layers. No network, no model, about a second. |

The design principle that connects them: **nothing reaches the output that has not been
resolved against an authoritative index.** `bibcheck` is not a companion to the search, it
is the gate the search results pass through.

## Benchmark

```bash
python benchmark/benchmark.py            # compare against the baseline
python benchmark/benchmark.py --update   # accept an intended change
```

It measures dedup, the triage rules, the topical guard and the exporter against a frozen
corpus, and reports the trade that matters: how many tokens the triage rules save, and how
many screener-approved papers they destroy in the process. It deliberately does **not**
measure retrieval recall or screening accuracy -- the labels came from the pipeline's own
screening, so scoring against them would be circular. That needs a gold set chosen by a
domain expert before any run, which does not exist yet.

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

