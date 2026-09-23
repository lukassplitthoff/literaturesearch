# How are strongly driven cQED devices simulated with Floquet methods?

The second worked example, and the first run of the full screen -> overview -> waves
sequence. Everything below is from a real run on 2026-09-23, including what the run got
wrong and how each failure was caught.

```bash
python examples/floquet_cqed/search.py
```

## The question, and why the search is shaped this way

> How are strongly driven circuit-QED devices and couplers simulated with Floquet and
> Floquet-Markov methods, and what drive-induced effects do those simulations predict?

The question came with three reference papers: strong-drive limits in Josephson circuits
(arXiv 2609.04704), frequency collisions under parametric modulation (arXiv 2511.05031),
and a linear coupler designed against drive-induced parasitic processes (arXiv 2501.18025).

**They are the gold set, not seeds.** All three are arXiv records, and OpenAlex holds no
references and no citers for any of them -- seeding from them would expand nothing, and
would also make recall against them trivially 100%. So eight queries carry the search,
each in a different community's vocabulary: the method (Floquet-Markov, quasienergy), the
symptom (measurement-induced transitions, ionization, chaos), the device (parametric
coupler) and the engineering problem (frequency collisions, drive limits). Snowballing
then walks the citation graphs of the published papers the queries found.

## What the run produced

| Stage | Result |
| --- | --- |
| Retrieve | 8 queries across OpenAlex and INSPIRE, plus 1 seed -> 338 works |
| Snowball | 2 rounds from query hits; **567 works dropped as off topic** |
| Corpus | **569** works; **563 verified**, 6 quarantined |
| Gold set | **3/3 found** -- after one seed, see below |
| Triage | **244 excluded by rule** for free; 325 sent to the screener |
| Screening | 13 batches -> **67 include** (66 verified), 244 exclude, 14 unsure |
| Overview | 3 packets, 66 abstracts -> `overview.md`, six themes, 65 of 66 papers cited |
| Extraction | Wave 1 of 20 papers issued; **not yet read** -- see "Next" |

Wall clock: about 16 minutes for the first run, almost all of it throttled requests at one
per second; about 2.5 minutes for every re-run after that, from the cache. Model cost,
as reported by the subagents: about 225k tokens per full screen (two Sonnet screeners),
about 200k for the overview including two repair rounds.

Outputs land in `$LITSEARCH_OUT_DIR/floquet_cqed/`, default `~/litsearch-runs/...`.

## What the checks caught

This run is worth reading mostly for its failures, because each one was caught by a check
rather than by luck -- and four of them led to fixes in the package.

**1. The newest gold paper was not findable.** arXiv 2609.04704 was posted two weeks before
the run. No query ranked it in the top 50, and it has no citers yet, so the known-item
check reported a MISS. It is now a `seed_dois` entry, commented as added after the gold
check. It expands nothing; it only puts the paper in front of the screener.

**2. A gold paper found as its journal version counted as missed.** arXiv 2501.18025 is in
the corpus as its PRX Quantum version, with a different DOI. The gold check matched on
DOI only and called it missing. *Package fix:* `report.gold_recall` now also matches on
the arXiv id, which survives the preprint-to-journal merge.

**3. The screening criteria asked something an abstract cannot answer.** The first
criteria required a Floquet-type analysis. The screener applied them faithfully: 41
included, 43 unsure -- nearly all "Floquet method unconfirmed" -- and one gold paper
*excluded*, because its abstract describes drive-induced transitions and parasitic mixing
but never names its simulation method. Methods usually live in the body, not the abstract.
The criteria now screen on the **physics** (strongly driven superconducting devices and
their drive-induced effects) and leave the **method** to the extraction column that reads
the full text. Re-screened: 67 included, 14 unsure, all three gold papers in. The first
verdicts are kept in the run directory as `screen/verdicts_strict_criteria.jsonl`.
*Package fix:* the gold-set report now runs after screening, so it shows each paper's real
verdict and warns on a gold paper that was screened out -- before, it printed
"unscreened" for every paper and the exclusion was invisible.

**4. The overview checker refused two drafts.** The first quoted an abstract as
`"perform[s] gate operations..."` -- an editorial bracket inside quote marks, so not
verbatim. The second had tool markup (`</content>`) glued under the last paragraph, which
slipped past the citation rule because it read as part of a cited paragraph. *Package fix:*
the checker now refuses markup lines. Both drafts went back to the summarizer with
`problems.txt`; neither was hand-edited.

**5. The default wave ranking suited the wrong kind of question.** Wave 1 is ranked by
role and terms, and the default puts experiments first -- right for "what was measured",
wrong for "how is it simulated". It left two gold papers outside wave 1. *Package fix:*
`priority_role_points` and `priority_terms` on the SearchSpec; this search puts theory and
method papers first and ranks on terms like `floquet`, `markov`, `ionization`.

## The answer so far, in outline

From `overview.md`, abstract level only. Six themes:

- **Dressed-state and multiphoton spectroscopy** -- Autler-Townes and Mollow physics,
  Landau-Zener-Stuckelberg interferometry and Bloch-Siegert shifts, several modelled with
  two-mode Floquet or Floquet-Born-Markov master equations and compared with spectroscopy.
- **Measurement-induced state transitions and transmon ionization** -- the largest theme:
  mechanisms, offset-charge dependence, Floquet branch analysis against measured transition
  maps, and extensions to fluxonium, balanced couplings and multi-qubit chips.
- **Squeeze-driven Kerr oscillators and Kerr-cats** -- static effective Hamiltonians beyond
  the RWA, and a Floquet-Markov diagnosis of multimode breakdown of noise bias.
- **Parametric couplers and amplifiers** -- beamsplitter converters, Floquet-mode
  travelling-wave amplifiers, and mixers engineered against parasitic processes.
- **Chaos and strong-drive breakdown** -- chaotic transmon spectra under off-resonant
  drives, coupler ionization read from the instantaneous Floquet spectrum, strong-drive
  thresholds with experimental confirmation.
- **Simulation methodology** -- diagrammatic effective Hamiltonians benchmarked against
  exact Floquet diagonalization, Floquet eigenproblems for parametric gates, and
  electromagnetic-simulation-derived time-dependent Hamiltonians.

## Next

Wave 1 -- the 20 papers at the top of `priority.md`, all three gold papers among them --
is issued and waiting. It is the expensive step: each task is one extractor reading one
full paper. Its columns are `device`, `drive`, `method`, `phenomenon`,
`critical_photon_number`, `drive_frequency_GHz` and `compared_to_experiment`, so the
`method` column is where "which of these actually use Floquet-Markov" gets answered with a
quote.
