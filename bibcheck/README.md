# bibcheck

Takes a `.bib` file and writes a cleaned, publication-ready **copy** beside it: keys
unified to `LastnameYEAR`, entries sorted, completeness checked, and metadata verified
against the public indexes.

The input file is never modified. Cite keys in `.tex` files are **not** rewritten -- the
tool reports a rename map instead, and you apply it.

## Quick start

```bash
pip install -e .

# report only, offline, no files written
python -m bibcheck.main path/to/refs.bib --no-write

# write the cleaned copy plus the reports
python -m bibcheck.main path/to/refs.bib --out-dir ./bibcheck_out

# also cross-check every entry against Crossref, arXiv and OpenAlex
python -m bibcheck.main path/to/refs.bib \
    --verify --mailto you@example.com --out-dir ./bibcheck_out

# and let the indexes fill the gaps they can fill
python -m bibcheck.main path/to/refs.bib \
    --verify --fix-from-index --mailto you@example.com --out-dir ./bibcheck_out
```

## Outputs

For an input `refs.bib`, in the input's directory unless `--out-dir` says otherwise:

| File | Contents |
| --- | --- |
| `refs_checked.bib` | the cleaned, re-keyed, sorted bibliography |
| `refs_bibcheck_report.md` | rename map, findings, verification table |
| `refs_bibcheck_report.json` | the same, machine-readable |
| `refs_refs_plaintext.txt` | numbered plain-text references, for pasting into a web checker |
| `refs_bibcheck_cache.json` | cached index responses, so re-runs are free |

Exit code: `0` clean, `1` warnings only, `2` errors (`--strict` promotes `1` to `2`).

## What it does

**1. Parses without normalising.** Field values are carried through verbatim, so
`F\"osel`, `Lo{\"i}ck`, `{\'E}lie` and brace-protected proper nouns like `{Hamiltonian}`
and `{SNAP}` survive untouched. `parse -> write -> parse` is asserted to be lossless.

**2. Normalises what has exactly one right answer.**

| Before | After |
| --- | --- |
| `@Article{...}` | `@article{...}` |
| `journal = {arXiv:2004.14256}` | `eprint = {2004.14256}`, `archivePrefix = {arXiv}` |
| `note = {arXiv:2509.03375 [quant-ph]}` | `eprint`, `archivePrefix`, `primaryClass = {quant-ph}` |
| `doi = {https://doi.org/10.1/x}` | `doi = {10.1/x}` |
| `pages = {296-305}` | `pages = {296--305}` |
| `eprint = {https://pubs.aip.org/....pdf}` | moved to `url` (it is not a preprint id) |
| U+FEFF and other zero-width characters | removed |
| trailing commas, ragged indentation | canonical 4-space layout, canonical field order |

A `note` that carries prose as well as an arXiv id keeps the prose; only a field whose
entire content *was* the arXiv reference is removed.

**3. Re-keys to `LastnameYEAR`.** `Cahill1969`, `Acharya2025`, `Ansys2021`. Surnames are
folded to ASCII; corporate authors in a single brace group (`{Sonnet Software}`) use
their first word. When several entries land on the same base key, **all** of them get a
letter suffix (`Eriksson2025a`, `Eriksson2025b`) so a bare key never sits beside a
suffixed one. Suffix assignment is ordered by normalised title, so it is stable across
runs and independent of the order entries appear in the file.

An entry with no parseable first-author surname or no 4-digit year keeps its original
key and is reported as an error, rather than being silently mangled.

**4. Checks completeness**, per entry type:

| Type | error if missing | warning if missing |
| --- | --- | --- |
| `article` | author, title, year | journal, volume, pages, doi |
| `software` | author, title, year, and one of {url, doi, eprint} | -- |
| `misc` | author, title, year | one of {url, doi, eprint, note, howpublished} |
| `phdthesis` | author, title, school, year | one of {doi, url} |
| `inproceedings` | author, title, booktitle, year | pages, doi |

Preprints are exempt from the journal/volume/pages warnings. Also checked: DOI shape,
4-digit years, `and others` truncation, non-ASCII bytes (which break cp1252 tooling on
Windows), repeated citation keys, and duplicate works -- same DOI, or same
author/year/title under two different keys.

Two checks exist because real bibliographies keep tripping over them:

* **placeholders** -- `author = {{TO VERIFY: ...}}`, `TBD`, `FIXME`. An entry with one
  is an error, and its key is not generated from the placeholder text.
* **unbraced organisations** -- `author = {Sonnet Software}` makes BibTeX read
  *Software* as the surname, which then lands in the citation key. Write `{{...}}`.

**5. Sorts.** By default (`--sort sections`) a `%%%%...` banner block opens a section and
entries are sorted by key *within* it, so the "software" / "articles / preprints /
theses" structure of `references_PBC.bib` survives. `--sort global` emits one flat sorted
list with the banners hoisted to the top. A short `%` note directly above an entry is
treated as belonging to that entry and travels with it when sorting.

**6. Optionally rewrites to pure ASCII** (`--ascii`). Non-ASCII characters in field
values become LaTeX escapes: an e-acute becomes ``{'e}``, an en dash becomes `--`.
Off by default, since it is the one normalisation that changes characters the author
typed rather than only their arrangement.

## Verification (`--verify`)

Tiered, cheapest and most authoritative first:

1. **DOI present** -> Crossref `/works/{doi}` -- the publisher's own deposited record.
   Zenodo (`10.5281`), arXiv (`10.48550`) and repository DOIs are not in Crossref, so
   those fall through to **DataCite**, which is where they are actually registered.
2. **arXiv id present** -> the arXiv Atom API. Its `<arxiv:doi>` element reveals when a
   preprint has since been published, which is the main thing this is for.
3. **Neither** -> OpenAlex title search, confirmed against Crossref.

Comparison is deliberately forgiving where the indexes are unhelpful: Crossref deposits
inline MathML in titles and collaboration names ahead of the real first author, Zenodo
titles software after its GitHub release, and `10.48550/arXiv.*` is arXiv's own DOI
rather than evidence of publication. None of those count as a mismatch.

Each entry comes back `verified`, `mismatched` (with the differing fields listed) or
`not_found`. Title comparison tolerates LaTeX markup and hyphenation; journal names
compare tolerantly, so `Phys. Rev. Lett.` matches `Physical Review Letters`; a one-year
difference is accepted, because online-first and issue years disagree routinely.

`--fix-from-index` fills fields the entry is missing and expands an `and others` author
list from the index record. It is the only operation that replaces an existing value, and
every change it makes is listed in the report.

Requests are throttled to 1/s, retried with backoff, and cached to disk. `--offline`
serves the cache and never opens a connection. A network failure degrades to a warning;
it never aborts the run or corrupts the output.

### Why not CiteMe or Citely

Both are good, and neither has a public API -- they are browser-upload services. They are
also wrappers over the same indexes this tool queries: CiteMe consults "OpenAlex,
CrossRef, Semantic Scholar, and PubMed", Citely "cross-checks CrossRef, PubMed, arXiv &
OpenAlex in parallel". Querying the indexes directly gives the same field-level verdicts
plus caching, determinism and unit tests.

For an independent second opinion, paste `refs_refs_plaintext.txt` into either:

- <https://citeme.app/tools/reference-checker>
- <https://citely.ai/reference-checker>

Note that **arXiv itself does not check references**. It does not even run BibTeX -- you
upload the compiled `.bbl` -- so nothing about your `.bib` is validated at submission.
Reference extraction happens afterwards, in NASA ADS, Semantic Scholar, INSPIRE-HEP and
Crossref.

## Options

```
python -m bibcheck.main INPUT.bib
    --out PATH            output .bib path (default: <stem>_checked.bib beside the input)
    --out-dir DIR         directory for all outputs
    --force               overwrite output files that already exist
    --key-style STYLE     citation key convention (only: lastnameyear)
    --sort MODE           sections (default) | global | none
    --verify              query Crossref, arXiv and OpenAlex
    --offline             with --verify, use only the on-disk cache
    --mailto EMAIL        contact address for Crossref/OpenAlex polite-pool service
    --fix-from-index      with --verify, fill empty fields from the index records
    --ascii               rewrite non-ASCII field values as LaTeX escapes
    --strict              treat warnings as errors in the exit code
    --no-write            report only
    --show-info           include automatic changes in the console output
```

## Tests

```bash
python -m pytest bibcheck/tests -q
```

No test opens a socket: `test_verify.py` drives the client with `offline=True` over a
pre-seeded cache of recorded Crossref/arXiv/OpenAlex payloads.

## Layout

| Module | Responsibility |
| --- | --- |
| `parser.py` | byte-faithful BibTeX reader/writer (stdlib only) |
| `keys.py` | `LastnameYEAR` generation, LaTeX/Unicode -> ASCII folding, collisions |
| `rules.py` | normalisations, completeness rules, duplicate detection |
| `verify.py` | Crossref / arXiv / OpenAlex clients, caching, field comparison |
| `report.py` | ASCII console output, Markdown, JSON, plain-text export |
| `main.py` | argparse CLI |

`requests` is the only third-party dependency. No BibTeX library is used: `bibtexparser`'s v1
API normalises exactly the LaTeX escapes this package needs to preserve.
