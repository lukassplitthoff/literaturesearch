"""Which other parametric gates have been studied in bosonic cavities?

Seeded from Chapman et al., "High-On-Off-Ratio Beam-Splitter Interaction for Gates on
Bosonically Encoded Qubits", PRX Quantum 4, 020355 (2023) -- a SNAIL-based programmable
beam splitter between two bosonic modes.

The question is explicitly "what OTHER gates", which makes this a related-work search
rather than a topic search: the reference paper defines the neighbourhood, and its
citation graph maps that neighbourhood better than any set of keywords can. The queries
are here only to catch gates that neither cite nor are cited by the seed.

Run from the repository root:

    python examples/parametric_gates_bosonic_cavities/search.py

See README.md in this directory for what a completed run produced.
"""

from __future__ import annotations

import pathlib

from litsearch.pipeline import SearchSpec, run

HERE = pathlib.Path(__file__).resolve().parent

SPEC = SearchSpec(
    name="parametric_gates_bosonic_cavities",
    question="Which other parametric gates have been studied in bosonic cavities?",
    # The reference paper. Its 71 references and 82 citers are the primary evidence.
    seed_dois=("10.1103/PRXQuantum.4.020355",),
    # Deliberately spanning the different parametric interactions: each community names
    # its gate differently, so a single phrasing would find only one of them.
    queries=[
        "parametric beam splitter interaction bosonic cavity microwave",
        "two-mode squeezing gate superconducting microwave resonator",
        "frequency conversion parametric coupler bosonic modes",
        "SNAP gate selective number-dependent arbitrary phase cavity",
        "echoed conditional displacement gate bosonic qubit",
        "SNAIL ATS parametric coupler three-wave mixing cavity",
        "controlled-SWAP exponential-SWAP bosonic mode gate",
        # Added after a gold-set check: the JRM and remote-state-transfer literature
        # neither cites nor is cited by the seed, so the citation graph cannot reach it
        # and only its own vocabulary will.
        "Josephson ring modulator parametric frequency conversion microwave",
        "on-demand quantum state transfer remote microwave cavity memories",
    ],
    year_from=2010,
    sources=("openalex", "inspire"),  # semanticscholar throttles keyless clients to nothing
    max_rounds=2,
    seeds_per_round=10,
    # The seed has 71 references and 85 citers. An earlier run took only 12 refs per seed
    # and lost three gold-set papers that were sitting in that list -- by far the largest
    # single recall loss found. Expansion is throttled to 1 req/s, so this costs roughly
    # seeds_per_round * (1 + refs_per_seed) seconds per round.
    refs_per_seed=40,
    # Found by an earlier run of this search, validated against Crossref and screened in --
    # NOT supplied from memory. That makes this a regression check against a change that
    # breaks retrieval, not an independent test of recall, which would need titles chosen
    # by someone who knows the field before the search is run.
    known_items=[
        "High-On-Off-Ratio Beam-Splitter Interaction for Gates on Bosonically Encoded Qubits",
        "Programmable Interference between Two Microwave Quantum Memories",
        "Observation of Two-Mode Squeezing in the Microwave Frequency Domain",
        "Efficient cavity control with SNAP gates",
        "Stabilization and operation of a Kerr-cat qubit",
    ],
    # Papers a domain expert listed for a beamsplitter review, WITHOUT seeing any output
    # of this pipeline. Recall against them is a measurement, not self-agreement.
    gold_set=str(HERE / "gold_set.json"),
    inclusion_criteria=(
        "Reports a parametric gate or parametric interaction between bosonic modes -- a "
        "microwave cavity, resonator or bosonic-encoded qubit. Beam splitter, two-mode "
        "squeezing, frequency conversion, SNAP, conditional displacement, controlled-SWAP "
        "and similar count. Both experiment and gate-design theory are in scope. "
        "The interaction must be DRIVE-ACTIVATED: a pump or flux modulation turns it on. "
        "An always-on static coupling (bare cross-Kerr, bare dispersive shift) does not "
        "qualify on its own, but engineering such a term with a drive does. A parametric "
        "drive between two TWO-LEVEL qubits does not qualify either -- at least one side "
        "must be a bosonic mode."
    ),
    exclusion_criteria=(
        "Gates between two-level qubits only, with no bosonic mode; non-superconducting "
        "platforms (trapped ion, optical photonics, spin, NV centre); readout, sensing or "
        "metrology papers with no gate; reviews with no new gate. "
        "On amplifiers specifically: a Josephson parametric AMPLIFIER used to amplify a "
        "signal is out of scope, but a parametric interaction that squeezes or entangles "
        "the modes of a resonator is in scope even when the device is called an amplifier "
        "-- the interaction is the subject here, not the packaging. Higher levels of a "
        "transmon are not a bosonic mode; the mode must be a cavity or resonator."
    ),
    # Scope: SUPERCONDUCTING MICROWAVE bosonic cavities only.
    #
    # An optical cavity is also a bosonic cavity and optomechanics is genuinely parametric,
    # so an earlier run legitimately returned a large optical set -- 42% of the corpus
    # against 34% superconducting. Narrowing is a scope decision, not drift correction,
    # and this is the scope the seed paper and this group work in.
    screen_forbidden=(
        "optomechanic", "opto-mechanic", "magnon", "nanophotonic", "photonic crystal",
        "silicon photonic", "optical fiber", "optical fibre", "optical parametric oscillator",
        "telecom wavelength", "cold atom", "atomic ensemble", "bose-einstein",
        "nitrogen-vacancy", "nv centre", "nv center", "trapped ion", "trapped-ion",
        "molecular spin", "single-ion magnet", "vanadyl",
    ),
    # Superconducting-circuit vocabulary a qualifying paper is unlikely to avoid. A bare
    # "cavity" was tried first and admitted the whole of nonlinear optics.
    screen_required=(
        "superconduct", "transmon", "fluxonium", "josephson", "snail",
        "circuit qed", "cqed", "microwave cavity", "microwave resonator",
        "coaxial cavity", "3d cavity", "cooper pair", "bosonic mode", "bosonic qubit",
    ),
    extraction_schema=(
        "gate_type",
        "interaction_order",
        "coupler",
        "modes",
        "fidelity_pct",
        "gate_time_ns",
        "on_off_ratio",
        "platform",
    ),
)


if __name__ == "__main__":
    raise SystemExit(run(SPEC))
