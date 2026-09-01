# Example: longest reported T1 and T2 in superconducting qubits

The worked example the harness was designed against. It is a good first test because it
is **quantitative** - the answer is a table of numbers with conditions, not a prose
summary - so a fabricated or mis-transcribed value is something a domain reader can catch
immediately.

## The question

> What are the longest reported T1 and T2 coherence times in superconducting qubits?

## Running it

The configuration lives in the `CONFIG` block at the top of `run_search.py` in the repo
root; this directory holds the notes, not a second copy of the settings.

```bash
python run_search.py
```

A full search is three runs, because the two model-driven stages hand off through files:

| Run | Does | Then |
| --- | --- | --- |
| 1 | retrieve, dedup, snowball, validate; writes `screen/batch_*.json` | the `lit-screener` subagent answers into `screen/verdicts.jsonl` |
| 2 | applies verdicts; writes `extract/task_*.json` for works that were included **and** validated | the `lit-extractor` subagent answers into `extract/rows.jsonl` |
| 3 | validates the rows, writes `evidence.csv` and the final `refs.bib` | done |

Every index response is cached on disk, so runs 2 and 3 do almost no network work.
Outputs land in `runs/t1_t2_superconducting_qubits/` and are gitignored - they are
regenerated, not source.

## Queries

Four, deliberately using **different vocabulary for the same idea**, because keyword
search only finds papers that share your wording:

1. `superconducting qubit coherence time T1 T2 record`
2. `tantalum transmon qubit long coherence`
3. `fluxonium millisecond coherence time`
4. `improving superconducting qubit relaxation time materials`

The first names the quantity, the second a device and material, the third a different
qubit family, the fourth the engineering goal. A set of four rephrasings of "long
coherence times" would have been close to useless.

## Screening criteria

**Include**: reports a measured T1, T2\*, or T2 echo for a superconducting qubit or
superconducting resonator/cavity, with an actual number.

**Exclude**: reviews without new measurements; theory-only papers with no measured
device; non-superconducting platforms (trapped ion, spin, photonic, NV centre).

## Extraction schema

```
qubit_type, material, substrate, T1_us, T2_star_us, T2_echo_us, temperature_mK
```

Every value carries `source_quote`, the sentence it came from. A value that cannot be
quoted is `null`. `litsearch.extract.validate_rows` enforces this on the way in - it drops
any row with an empty quote and flags any row whose quote does not contain the digits of
the value it claims - so the guarantee does not depend on the extractor having behaved.

Unit conversion is tolerated: a paper writing "0.36 ms" supports a table entry of
`T1_us: 360`.

## Known items

**Not configured for this run.** `KNOWN_ITEMS` is empty, so the recall check is skipped.

This is the one gap in the example, and it is deliberate rather than an oversight. The
known-item test works by asserting that specific papers appear, and the titles have to
come from someone who actually knows this literature. A title supplied from a model's
memory is a bad test: when the search "misses" it, you cannot tell whether retrieval
failed or the title was wrong, which is exactly the ambiguity the check exists to remove.

To turn the check on, add 3 to 5 titles you are confident must appear:

```python
KNOWN_ITEMS = [
    "...",
]
```

The run then reports `OK` or `MISS` per item and exits non-zero if any is missing.

## What to check before believing the output

1. `run.json` - does the saturation curve flatten? If `new_fraction` is still high in the
   last round, the search stopped early and the corpus is incomplete.
2. `quarantine.md` - every entry should have a reason you find reasonable. Software and
   dataset records land here routinely because they have no Crossref DOI.
3. `needs_review.md` - the `unsure` and unscreened works. These are **not** in the
   results and are waiting on a human.
4. Pick three rows of `evidence.csv` and check the quoted sentence against the actual
   paper. This is the only real test of the extraction step.
5. `python -m bibcheck.main runs/.../refs.bib --verify` should exit 0 or 1, never 2.

## Known limits

- **Extraction quality is capped by PDF access.** arXiv and other open-access papers are
  read in full; the rest yield abstract-only extraction, recorded as
  `confidence: abstract_only`. Since coherence numbers often live in a table rather than
  the abstract, expect the paywalled fraction to give thin rows.
- **NASA ADS is not enabled** - it needs a free token. It is the strongest physics index
  of the four, so recall here is lower than it could be.
- Semantic Scholar throttles keyless clients, and will sometimes contribute nothing to a
  run. OpenAlex covers the same citation graph, so this degrades recall rather than
  breaking the search.
