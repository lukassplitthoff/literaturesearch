# Output formats

What a finished search produces, and the rules each format must obey.

The corpus is the deliverable; everything here is a **view over it**. Nothing in this
document may introduce a fact that is not already in `corpus.jsonl` and `evidence.csv`.
A renderer that needs a fact those files do not contain has found a gap in the search, not
a reason to supply the fact itself.

## Where output goes

**Never inside the repository.** Runs are written under `$LITSEARCH_OUT_DIR`, defaulting
to `~/litsearch-runs/<run-name>/`. `run_search.py` warns if the configured directory sits
inside the git working tree. Search results are data: large, regenerated on every run, and
never committed.

## The rules that bind every format

1. **Every claim carries a cite key**, and that key must exist in `refs.bib`. No exceptions
   for "well known" facts, background statements or framing sentences.
2. **Every number traces to a row in `evidence.csv`**, which carries the quote it came
   from. A number without a row does not appear.
3. **Nothing that failed the validation gate appears anywhere** except `quarantine.md`.
4. **State the conditions with the number.** A coherence time without its qubit type,
   material and temperature is not a result, it is a rumour.
5. **Say what the search did not cover.** Every format ends with its limits: the date
   range, the sources that contributed nothing, the papers behind a paywall, the fraction
   extracted from abstracts only.
6. **Never round a value beyond the precision the paper reported**, and never convert units
   silently -- if the paper said 0.36 ms and the table says 360 us, that is fine, but a
   value reported as "about 1 ms" is not 1000.0 us.

---

## `refs.bib` -- the bibliography

Produced by `litsearch.export.write_bibtex`. Rules:

- Keys are `LastnameYEAR`, assigned by bibcheck, with letter suffixes on collision.
- ASCII only. Accented characters become LaTeX escapes (`{\'e}`), because raw UTF-8 in a
  `.bib` breaks cp1252 tooling on Windows.
- `@article` for published work with a venue; `@misc` with `eprint` and `archivePrefix`
  for preprints. A preprint never carries `journal`, `volume` or `pages`.
- Field order is canonical: author, title, journal, volume, pages, year, doi, eprint.
- The file must pass `python -m bibcheck.main <path> --verify` with exit code 0 or 1.
  **Exit code 2 means it is not finished.**

Only works that passed the gate are included. If a work you expect is absent, look in
`quarantine.md` before assuming the search missed it.

## `evidence.csv` -- the extraction table

One row per **distinct measurement**, not per paper. A paper reporting three devices
yields three rows.

| Column | Rule |
| --- | --- |
| `cite_key` | Must match an entry in `refs.bib`. Supplied to the extractor by `export.cite_keys_for`, so the join holds by construction rather than by the extractor guessing |
| `source_quote` | Verbatim sentence from the paper containing the value. **Mandatory** |
| `confidence` | `full_text` only if the PDF was actually read, else `abstract_only` |
| any value column | The number as stated, or `null`. Never inferred, never recalled |

`litsearch.export.write_evidence_csv` drops any row with an empty quote, and
`litsearch.extract.validate_rows` flags any row whose quote does not contain the digits of
the value it claims. Do not bypass either. Neither proves the quote is *about* that
quantity -- "operated at 20 mK" passes for a T1 of 20 -- so a flagged row, and any row a
claim leans on, still needs a reader.

## `reading_plan.md` -- where to start

Produced by `litsearch.plan.write_reading_plan`. Deterministic: it makes no model call and
states no result. A ranked table answers "what survived the gate"; this answers the question
someone actually has in front of forty validated papers, which is what to read first.

Three disjoint phases, each carrying the rule that filled it:

1. **Foundation** -- works the screener labelled `review`, then works that at least two
   other members of this corpus cite. The second group is chosen by the corpus rather than
   by global citation counts, so it is what *this question* treats as foundational.
2. **Core evidence** -- established work, older than the frontier window, ranked.
3. **Frontier** -- the last three years, ranked. Split out because recent work has not had
   time to accumulate citations and would otherwise sort to the bottom of every list.

Rules:

- **Every row names a cite key that exists in `refs.bib`**, from the same mapping the
  bibliography was built with.
- **No row makes a claim about what a paper found.** Placement follows from role, year and
  citation structure. A plan that summarises results is a synthesis wearing a plan's name --
  that is `review.md`, and it has different rules.
- **The phase rule is printed with the phase**, so a reader can disagree with a placement
  instead of assuming it was judged.
- **Rank is not relevance.** `Cites/yr` corrects the age bias in a raw count and introduces
  its own; `Cited here` counts only references the search actually fetched, so a zero means
  the citation was not retrieved, not that it does not exist. Both must be described that
  way wherever they appear.
- Ends by naming what is *not* in it: the review queue, the quarantine, and the coverage
  limits in `run.json`.

## `conflicts.md` -- candidate contradictions

Produced by `litsearch.conflict.write_conflicts`. Groups `evidence.csv` rows by the
conditions they were measured under and flags any group where two different papers report
values differing by more than `DISAGREEMENT_RATIO` (3x).

This exists because rule 4 of `review.md` -- say where papers disagree, and cite both --
had nothing behind it. Written from impression, "the literature disagrees" is exactly the
kind of unsourced claim the rest of the pipeline refuses to emit.

Rules:

- **A flag is a question, not a finding.** The usual explanations are dull: different
  devices, a different definition of the quantity, a misread unit. Nothing in this file may
  assert that a contradiction is real.
- **Two rows from the same paper are never a conflict.** A paper reporting three devices is
  not disagreeing with itself.
- **Rows with different stated conditions are never compared.** A missing condition is its
  own group, not a wildcard -- comparing an unstated material against tantalum manufactures
  a disagreement out of an incomplete record.
- **Both extremes appear with their verbatim quotes**, so the disagreement is judged from
  the papers' own words rather than from two numbers.
- The threshold is arbitrary and the file says so. A real contradiction narrower than it is
  not flagged.

## `review.md` -- the synthesis

Prose, written last, for a reader who wants the answer rather than the corpus.

Structure:

1. **The answer, first, in three sentences or fewer.** If the question was "what is the
   longest reported T1", the first sentence contains a number, a cite key and the
   conditions.
2. **The evidence table**, or its top rows, sorted by the quantity asked about.
3. **How the field got there** -- the trend, the techniques that moved it, the disputes.
   Every sentence cited.
4. **What is contested or uncertain.** Where papers disagree, say so and cite both.
   Start from `conflicts.md` rather than from memory: it lists the groups of rows that
   actually disagree, with both quotes. A disagreement not in that file needs a reason you
   can point at -- a resolved conflict, or one the conditions explain -- not an impression.
5. **Limits of this search** (rule 5 above).

Prohibited: a "conclusion" paragraph that generalises beyond the rows; comparative claims
("the best", "the first") unless a cited paper makes that claim itself; any sentence
whose cite key you cannot point to in `refs.bib`.

## `lecture_notes.md` -- teaching material

Same evidence rules, different shape: the reader is learning the subject, not auditing it.

- Open with the **physical question**, not the literature. Why does this quantity matter,
  what sets it, what would an ideal device look like.
- Introduce each concept before the paper that measures it. A reader should be able to
  follow without opening a single reference.
- Use the evidence table as the **spine of the narrative**: each major row becomes a short
  section explaining what was done and why it moved the number.
- Include the units and the definitions explicitly (T1 vs T2\* vs T2 echo is exactly the
  kind of thing that is assumed and then confused).
- Cite keys stay inline. Teaching material with uncited numbers is worse than none,
  because it gets copied.
- End with open problems, drawn from the "contested" section of the synthesis.

Length: aim for one page per major idea. Do not pad to a target length.

## `slides.pptx` -- presentation

**Not implemented.** Generating it would add `python-pptx` as a dependency; the structure
below is the contract for when it is built, and is also a usable spec for making the deck
by hand from `evidence.csv`.

- **One claim per slide.** The slide title states the claim; the body carries the evidence.
- **Every slide with a number carries its cite key** in the footer, in full enough form to
  find the paper (`Place2021`, not `[3]`).
- **The evidence table becomes a chart, not a wall of text** -- quantity on the y axis,
  year on the x, one series per qubit type. See the `dataviz` guidance before drawing it.
- Maximum six bullets per slide, maximum twelve words per bullet. If it needs more, it is
  two slides.
- A **sources slide** at the end listing the bibliography, and a **limits slide** stating
  what the search did not cover. The limits slide is not optional; a deck that omits it
  implies a completeness the search did not achieve.
- Speaker notes carry the `source_quote` for every number on the slide, so the presenter
  can answer "where does that come from" without leaving the deck.

## Adding a format

A new renderer goes in `litsearch/export.py`, reads only `corpus.jsonl` and
`evidence.csv`, and gets a section here stating its rules before it is written. If a
format cannot obey rules 1 to 6, it does not belong in this pipeline.
