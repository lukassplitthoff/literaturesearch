---
name: litsearch
description: Use when the user wants a literature search on a research question, hypothesis or topic - finding the relevant papers, building a validated bibliography, and extracting quantitative evidence into a table. Runs the deterministic retrieval and validation pipeline in Python, then screens and extracts with subagents. Every cited work is checked against Crossref, DataCite, arXiv or OpenAlex; anything unresolved is quarantined, never silently included.
---

# Literature search

## When to use

Use for: "find me the literature on X", "what is the highest reported Y", "build a
bibliography for this section", "who has measured Z and what did they get".

NOT for: checking or cleaning an existing `.bib` file -> run `python -m bibcheck.main`
directly. NOT for a single known paper you just want the citation for -> `bibcheck` with
`--verify --fix-from-index` does that in one step.

## The one rule

**Nothing reaches an output that has not resolved against an index, and no extracted
number appears without the sentence it came from.** You do not relax this because a paper
looks obviously real, because the user is in a hurry, or because a value seems
well known. A work that fails the gate goes to `quarantine.md` with a reason. A number
that cannot be quoted is recorded as `null`.

You never write a citation from memory. If you believe a paper exists and the pipeline
did not find it, add it to `KNOWN_ITEMS` and re-run retrieval - do not hand-write the
entry.

## Read first

- `README.md` - the eight stages and what each produces
- `docs/OUTPUT_FORMATS.md` - **the output contract**: what every format must obey, and the
  specific rules for refs.bib, evidence.csv, review.md, lecture notes and slides. Read it
  before writing any deliverable.
- `litsearch/config.py` - every knob, and what the defaults mean
- `run_search.py` - the CONFIG block you will edit

## Clarify before starting

Ask only what changes the work, then proceed:

1. **The question**, precisely. "T1 in qubits" and "the longest reported T1 in a
   superconducting qubit, and under what conditions" produce different searches.
2. **Year window.** Defaults to 1995 onward.
3. **Known items** - 3 to 5 papers the user is confident must appear. This is the single
   best check on whether retrieval worked. If the user does not know any, say that the
   run will have no recall check and continue.
4. **What the evidence table needs** - the columns. For a "how large / how long / how
   fast" question this is the actual deliverable, not the bibliography.

Do not ask about sources, rate limits or output formats. Those have defaults.

## Procedure

### Stage 0 - Frame (you, or the lit-scout subagent)

Turn the question into 3 to 6 query strings that use *different vocabulary for the same
idea* - this is where recall is won or lost. For coherence times: "T1 T2 coherence",
"relaxation time", "energy relaxation", "qubit lifetime", "dephasing" all find different
papers. Write them into `QUERIES` in `run_search.py`, along with `KNOWN_ITEMS`,
`YEAR_FROM` and `RUN_NAME`.

Also write down the inclusion and exclusion criteria explicitly, in one or two sentences
each. The screener needs them and cannot invent them.

### Stages 1, 2, 3, 5 - Run the deterministic pipeline

```bash
python run_search.py
```

Retrieval, dedup, snowballing and the validation gate are pure Python and involve no
model. Do not attempt to do this part yourself with WebSearch - you would lose the
caching, the dedup and the gate.

Read `$LITSEARCH_OUT_DIR/<name>/run.json` afterwards and check two things:

- **Known items.** A `MISS` means retrieval is incomplete. Add vocabulary to `QUERIES`
  and run again before going any further. Report a persistent miss to the user; do not
  proceed quietly.
- **The saturation curve.** If `new_fraction` is still high in the last round, the search
  stopped early. Raise `MAX_ROUNDS`.

### Stage 4 - Screen

Delegate to the **lit-screener** subagent in batches of ~25 works from `corpus.jsonl`,
with the inclusion and exclusion criteria. It returns `include`, `exclude` or `unsure`
per work with a one-line reason.

Surface every `unsure` to the user rather than deciding yourself. That list is usually
short and is where the interesting edge cases live.

### Stage 6 - Extract

Delegate to the **lit-extractor** subagent, one work at a time, for works that were
included AND verified. Give it the agreed columns. It reads the open-access PDF where
`oa_pdf_url` is set and the abstract otherwise, and returns one row per measurement with
a mandatory `source_quote`.

Write the rows with `litsearch.export.write_evidence_csv`, which refuses any row whose
quote is empty. Do not bypass it.

### Stage 7 - Report

`run_search.py` already writes `corpus.jsonl`, `refs.bib`, `shortlist.md`,
`quarantine.md`, `needs_review.md` and `run.json`, into `$LITSEARCH_OUT_DIR` (default
`~/litsearch-runs/<name>/`) -- **outside the repository**, because run outputs are data and
must never be committed.

Add `evidence.csv` from stage 6, then write whichever deliverable was asked for. The rules
for each are in `docs/OUTPUT_FORMATS.md` and are not negotiable: every claim carries a cite
key present in `refs.bib`, every number traces to an `evidence.csv` row, nothing that
failed the gate appears, and every format ends by stating what the search did not cover.

## After

Verify before reporting done:

```bash
python -m bibcheck.main "$LITSEARCH_OUT_DIR/<name>/refs.bib" --verify
```

Exit code 0 or 1 is fine; 2 means the bibliography has errors and is not finished.

Tell the user plainly: how many works were retrieved, how many survived the gate, how
many are quarantined and why, and which known items were missed. If retrieval looked
thin, say so rather than presenting a short list as a complete answer.
