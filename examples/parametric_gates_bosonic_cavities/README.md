# Which other parametric gates have been studied in bosonic cavities?

The worked example. Everything below is from a real run, not a design sketch.

```bash
python examples/parametric_gates_bosonic_cavities/search.py
```

**Or open [`demo.ipynb`](demo.ipynb)** in this directory -- a runnable notebook with two
things you can try in a couple of minutes: `bibcheck` catching a fabricated citation, and a
small seeded search that finishes in about 25 seconds. It is committed without outputs, so
what you see is what your run produced.

## Why this search is shaped the way it is

The question is "what **other** gates", which makes it a **related-work** search, not a
topic search. So it is seeded by DOI from

> Chapman et al., *High-On-Off-Ratio Beam-Splitter Interaction for Gates on Bosonically
> Encoded Qubits*, PRX Quantum **4**, 020355 (2023)

and expands along that paper's 71 references and 82 citers. Naming a paper that is
definitively on topic sidesteps the vocabulary problem that keyword queries have: the
citation graph does not care what words a paper chose.

The seven queries are only there to catch gates that neither cite nor are cited by the
seed, and they deliberately span **different vocabulary** rather than rephrasings --
beam splitter, two-mode squeezing, frequency conversion, SNAP, echoed conditional
displacement, SNAIL/ATS three-wave mixing, controlled-SWAP. Each community names its gate
differently, so one phrasing finds one cluster.

## What the run produced

| Stage | Result |
| --- | --- |
| Retrieve | 1 seed + 7 queries across OpenAlex and INSPIRE |
| Snowball | 2 rounds; **559 works dropped as off topic**, 418 admitted |
| Corpus | **697** works after dedup |
| Validation gate | **684 verified**, 13 quarantined |
| Triage | **489 excluded by rule** for free; 208 sent to the model |
| Screening | 9 batches, ~41k tokens -> **48 include**, 19 unsure, 3 unscreened |
| Extraction | 6 papers read in full -> **25 evidence rows** |
| Outputs | `refs.bib` 47 entries, **0 errors**; `evidence.csv` 25 rows, every one quoted |

Outputs land in `$LITSEARCH_OUT_DIR/parametric_gates_bosonic_cavities/`, default
`~/litsearch-runs/...` -- outside the repository, because run outputs are data.

## The answer, in outline

Parametric gates found in superconducting microwave cavities, by interaction:

- **Beam splitter / bilinear exchange** -- the seed's own family. Programmable interference
  between two microwave memories; parity-protected converter; controlled beam splitter
  transparent to ancilla errors; engineered exchange entangling two bosonic modes.
- **Two-mode squeezing** -- observed in the microwave domain (2011); used together with the
  beam splitter to build a bosonic Kitaev chain.
- **Single-mode and controlled squeezing** -- dissipatively stabilised beyond 3 dB; a
  controlled-squeeze gate on a SQUID-terminated resonator.
- **Two-photon drive (Kerr-cat)** -- dynamically protected cat qubits; Kerr-cat
  stabilisation; SQUID-based next-generation Kerr-cat.
- **SNAP** -- efficient cavity control; Floquet-engineered fast SNAP; compilation of SU(N)
  into SNAP and displacement.
- **Conditional displacement (ECD)** -- pulse-shape optimisation; conditional-NOT
  displacement for multi-oscillator control.
- **Higher-order mixing** -- three-photon spontaneous parametric down-conversion;
  drive-activated native cubic interactions; a four-wave-mixing toolbox; multi-loop SQUID
  nonlinearity engineering.
- **Frequency conversion / sideband** -- multimode random access; parametric control of
  normal-mode splitting; fast sideband control of a multimode memory.

`evidence.csv` carries the numbers for the six papers read in full -- 12 beam-splitter
rows, 3 controlled-SWAP, 3 three-photon SPDC, plus frequency conversion and detection --
each with the sentence it came from.

## Recall, measured against an independent list

`gold_set.json` holds 15 parametric-beamsplitter papers compiled by a domain expert for a
review, **without seeing any output of this pipeline**. That is what separates a
measurement from the system agreeing with itself, and it is matched by DOI, so there is no
similarity threshold to argue about.

| Configuration | Recall |
| --- | --- |
| `refs_per_seed=12`, no vocabulary queries | 10/15 (67%) |
| `refs_per_seed=40` + JRM / state-transfer queries | 12/15 (80%) |
| **+ explicit seeds always expanded** | **15/15 (100%)** |

The last step was a bug fix, not a tuning change. `seed_candidates` ranked round-0 works by
citation count, so the 82-citation seed lost its place to 600-citation reviews the queries
dragged in, and **a DOI-seeded search never walked its own seed's graph**. It failed
silently -- the run returned a thousand works and looked healthy. Two gold papers sat at
reference #6 and citer #6 of that unopened seed.

100% on fifteen papers in one subfield is not a general claim about recall. It says the
pipeline can find what an expert expects on the question it was pointed at, which is the
most that a gold set of this size supports.

## Scope decision you should know about

An optical cavity is also a bosonic cavity, and optomechanics is genuinely parametric. The
first run returned a corpus that was **42% optical/optomechanical against 34%
superconducting** -- not drift, a legitimately broader reading of the question. This
example narrows to **superconducting microwave** because that is the seed paper's platform.
To widen it, empty `screen_forbidden` of the optical terms.

## What is not finished

- **6 of 47** included papers have been extracted. The other 41 have task files waiting.
- **19 unsure and 3 unscreened** works sit in `needs_review.md`. They are not in the
  results and need a human decision.
- **Saturation was not reached**: `new_fraction` ran 0.425 -> 0.304, well above the 0.05
  threshold, so the search stopped on the round cap. The corpus is focused but not
  complete. Raising `max_rounds` continues it.
- `known_items` here are papers an earlier run of *this* search found, so they are a
  **regression check** against a change that breaks retrieval -- not an independent test of
  recall, which would need titles chosen by someone who knows the field beforehand.

## Before believing any of it

1. Pick three rows of `evidence.csv` and check the quoted sentence against the paper. This
   is the only real test of the extraction step, and the `source_quote` column exists to
   make it quick.
2. `python -m bibcheck.main <out>/refs.bib --verify` should exit 0 or 1, never 2.
3. Read `quarantine.md`: every held-back work should have a reason you find reasonable.
4. Check the run's screening summary for `misaligned` or `unverified` verdicts. Both mean
   verdicts and corpus have drifted apart.
