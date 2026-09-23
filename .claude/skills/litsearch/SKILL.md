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

**Every criterion must be decidable from a title and abstract.** Screen on the subject --
the device, the physics, the quantity. A question about *how* papers did something (which
simulation method, which fabrication step) is usually answered in the body, not the
abstract: put it in an extraction column instead. A criterion like "uses a Floquet-Markov
analysis" once excluded one of the user's own reference papers and left 43 papers unsure,
all for the same reason: the abstract never named the method.

**Reference papers the user names are a gold set first, seeds second.** Put them in a
gold-set file (DOI plus `arxiv` id) so recall against them is measured. Seed only a paper
the queries cannot find -- typically one too recent to rank -- and say so in a comment.
Seeding an arXiv-only record expands nothing: OpenAlex usually holds no references or
citers for it.

**A question about methods or theory needs its own wave ranking.** The default ranks
experimental (`primary`) papers first, with terms from the extraction column names. For
"how is X simulated / modelled", set `priority_role_points` to put `theory` and `method`
first and `priority_terms` to the words that mark the papers worth reading in full.

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
per work with a one-line reason, plus a `role` saying what kind of paper it is -- `review`,
`primary`, `method` or `theory`. The role is what `reading_plan.md` groups by, and it costs
nothing here because the abstract is already being read. Omitted when the abstract does not
make the kind clear; a guessed role is worse than none.

Surface every `unsure` to the user rather than deciding yourself. That list is usually
short and is where the interesting edge cases live. If it is long and the reasons repeat
("method not stated", "value not in abstract"), the criteria are asking something an
abstract cannot answer -- revise them with the user and re-screen, rather than deciding
the pile yourself. Check the gold-set line after screening too: a gold paper that is
found but screened out is the same symptom.

### Stage 4b - Overview (abstract level)

Once screening is complete, `run_search.py` writes `overview/packet_NN.json`: every
screened-in, validated work with its full abstract and its cite key. Delegate to the
**lit-summarizer** subagent, which writes `overview/draft.md`, then re-run. The pipeline
checks the draft -- every paragraph cited, every quote found in its abstract, every
number carried by a quote -- and only then publishes `overview.md` between a header and
footer it writes itself. If it prints `[BLOCKED]`, hand `overview/problems.txt` back to
the summarizer; never edit the draft past the check yourself.

Ask the user for the angle before delegating, if they have one: it goes in
`summary_focus` ("which materials, and what limits T1"). `summary_group_by` is `role`
by default; `year` or `theme` on request.

**Show the user `overview.md` before any full-text extraction.** It is cheap, it covers
every included paper, and it is often the answer they wanted. It is also where they will
notice that the criteria let in the wrong papers -- which is far cheaper to fix now.

### Stage 6 - Extract, in waves

This is the expensive stage: every task is an Opus subagent reading one whole paper. So it
runs in waves of `extraction_wave_size` (20) papers, most promising first, and only
`extraction_waves` waves are issued. `priority.md` ranks every screened-in paper -- role,
extraction columns named in the abstract, citations per year -- and shows each paper's
wave and status.

`run_search.py` writes tasks only for issued papers not yet answered, and only when
screening is complete, the extraction schema is non-empty and the papers issued in total
stay within `max_extraction_tasks`; otherwise it prints `[BLOCKED]` with the reason and
writes none. **Never work around a block** -- do not write task files yourself, do not
raise a limit on your own, and do not read the papers directly instead. Fix the cause
(finish screening, agree the columns with the user), or put the numbers to the user.

Before each wave, show the user the top of `priority.md` and ask one question: extract
this wave, adjust the selection, or stop. The user steers with `extract/selection.txt` --
one cite key per line, `+Key` to read it in the next wave regardless of score, `-Key` to
never read it. A pin cannot pull in a paper screening did not include.

After a wave, show what came back (`evidence.csv`, `conflicts.md`) and ask whether the
next wave is worth it. If yes, the user -- not you -- raises `extraction_waves` by one.

Delegate to the **lit-extractor** subagent, one task file per agent, with the agreed
columns. Each writes its rows to the `rows_file` its task names
(`extract/rows/<key>.jsonl`), so the agents of a wave can run in parallel. Its prompt is
just the task file: the pipeline has already fetched each paper (arXiv first) and converted
it to `extract/text/<key>.txt`, and the task carries that path and every column's type and
definition. The extractor reads the text, never fetches, and returns one row
per measurement with a mandatory `source_quote`. Record the token count each agent
reports: per-paper cost is the number that decides whether the next wave is worth it.

Write the rows with `litsearch.export.write_evidence_csv`, which refuses any row whose
quote is empty. Do not bypass it.

### Stage 7 - Report

`run_search.py` already writes `corpus.jsonl`, `refs.bib`, `shortlist.md`,
`reading_plan.md`, `overview.md`, `priority.md`, `conflicts.md`, `quarantine.md`,
`needs_review.md` and `run.json`, into
`$LITSEARCH_OUT_DIR` (default `~/litsearch-runs/<name>/`) -- **outside the repository**,
because run outputs are data and must never be committed.

Two of those are worth opening before you write anything:

- **`reading_plan.md`** sorts the validated works into foundation, core evidence and
  frontier, by metadata alone. It is the right thing to hand a user who asked "where do I
  start" rather than "what is the number".
- **`conflicts.md`** lists groups of `evidence.csv` rows, measured under the same stated
  conditions, whose values differ by more than 3x -- with both quotes. Read it before
  writing the "what is contested" section of any synthesis. Each flag is a question: the
  usual answer is different devices or a misread unit, and you have to look to tell.

Add `evidence.csv` from stage 6, then write whichever deliverable was asked for. Every
deliverable states how much was read in full, from the first line of `priority.md`: "numbers
come from 20 of 47 screened-in papers read in full; the rest appear at abstract level".
`overview.md` may supply context, never numbers. The rules
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

## "Summarise these papers"

The summary is `overview.md` (stage 4b): every screened-in paper, from its abstract, for
a few hundred tokens a paper. Never answer "summarise these" by reading full papers --
that is the single most expensive way to use this pipeline, hundreds of full reads that
produce nothing checkable. For "where do I start", point at `reading_plan.md`; for "what
are the numbers", at `evidence.csv` and `conflicts.md`, extended wave by wave. If the user
wants something specific from every paper, that is an extraction column, read in waves.

## Follow-up questions

The interesting questions arrive after the user has read the output, and every one of them
is answerable from files on disk. None of them is answerable from memory, and answering
from memory is the failure this whole pipeline exists to prevent.

- **"Does that pattern hold?"** -- e.g. "recent papers all use tantalum, older ones
  niobium". Check it against `corpus.jsonl` and `evidence.csv` and answer with the counts.
  If the corpus cannot settle it, say which papers would be needed rather than guessing.
- **"Why do these two disagree?"** -- start from the `conflicts.md` entry and its two
  quotes. If the conditions differ in a way the schema does not capture, the answer is a
  new extraction column and a re-run, not a verdict about who is right.
- **"You missed X."** -- add it to `KNOWN_ITEMS` or `seed_dois` and re-run retrieval. Never
  hand-write the entry. A re-run is cheap: the index cache makes it nearly free, and
  screening verdicts survive it because they are relocated by checksum rather than by
  position.
- **"Fill this gap."** -- add vocabulary to `QUERIES` covering the gap and re-run. The
  corpus is additive, so the new works merge into the existing one and only the new works
  need screening.
