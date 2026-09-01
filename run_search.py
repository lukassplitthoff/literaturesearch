"""Template for a new literature search. Copy it, edit the SPEC, run it.

    python run_search.py

A worked, completed search is in examples/parametric_gates_bosonic_cavities/.

A full run is three invocations, because the two model-driven stages hand off through
files (nothing here ever calls a model):

    python run_search.py     # retrieve, validate, write screening batches
    #   an agent answers <out>/screen/verdicts.jsonl
    python run_search.py     # apply verdicts, write extraction tasks
    #   an agent answers <out>/extract/rows.jsonl
    python run_search.py     # write evidence.csv and the final refs.bib

Outputs go to $LITSEARCH_OUT_DIR/<name>/, defaulting to ~/litsearch-runs/<name>/ --
outside the repository, because search results are data and must never be committed.
"""

from __future__ import annotations

from litsearch.pipeline import SearchSpec, run

SPEC = SearchSpec(
    name="my_search",
    question="<the precise question, including the quantity or claim you want>",
    # Vary the VOCABULARY, not the phrasing. Different communities name the same thing
    # differently, and keyword search only finds papers that share your wording. Four
    # rewordings of one phrase find one cluster; four genuinely different terms find four.
    queries=[
        "<the physical quantity>",
        "<the device or material that carries it>",
        "<the technique that improves it>",
        "<the application that cares about it>",
    ],
    # Optional but powerful: name papers that are definitively on topic and let their
    # citation graph do the work. This sidesteps the vocabulary problem entirely.
    seed_dois=(),
    year_from=None,
    # Snowballing is throttled to one request per second, so these set the wall clock:
    # roughly seeds_per_round * (1 + refs_per_seed) seconds per round. Start small.
    max_rounds=2,
    seeds_per_round=8,
    refs_per_seed=5,
    # Papers you are confident must appear. The run fails loudly if one is missing, which
    # is the only real check on whether retrieval worked. Choose them BEFORE the first run.
    known_items=[],
    # Stage 4. Concrete enough to apply from a title and abstract alone: "relevant to X"
    # is not a criterion, "reports a measured X, with a number" is.
    inclusion_criteria="<what a qualifying paper must report>",
    exclusion_criteria="<platforms, publication types and near-misses to reject>",
    # Rules applied BEFORE any model call, so the model only sees what a rule cannot
    # settle. A phrase in `forbidden` excludes outright; a work matching none of
    # `required` is excluded. Note there is no rule that INCLUDES: a rule can cheaply
    # prove a paper is off subject, but only a read abstract can confirm it qualifies.
    screen_forbidden=(),
    screen_required=(),
    # Stage 6 columns. Every one must be quotable from the paper or it is recorded null.
    extraction_schema=(),
    mailto="",  # your address puts Crossref/OpenAlex requests in the polite pool
)


if __name__ == "__main__":
    raise SystemExit(run(SPEC))
