"""How are strongly driven cQED devices and couplers simulated with Floquet methods?

A topic search, not a related-work search. Three reference papers define the subject --
strong-drive limits in Josephson circuits, frequency collisions under parametric
modulation, and a linear coupler designed against drive-induced spurious processes -- but
all three are arXiv records that OpenAlex holds no citation graph for, so seeding from
them would expand nothing. They are the gold set instead: recall against them measures
whether the queries alone find this literature.

Run from the repository root:

    python examples/floquet_cqed/search.py
"""

from __future__ import annotations

import pathlib

from litsearch.extract import Column
from litsearch.pipeline import SearchSpec, run

HERE = pathlib.Path(__file__).resolve().parent

SPEC = SearchSpec(
    name="floquet_cqed",
    question=(
        "How are strongly driven circuit-QED devices and couplers simulated with Floquet and "
        "Floquet-Markov methods, and what drive-induced effects do those simulations predict?"
    ),
    # Different communities name the same physics differently. The method (Floquet,
    # Floquet-Markov, quasienergy), the symptom (measurement-induced transitions,
    # ionization, chaos), the device (parametric coupler, SNAIL, flux modulation) and the
    # engineering problem (frequency collisions, drive limits) each find their own cluster.
    queries=[
        "Floquet-Markov master equation superconducting qubit drive",
        "Floquet analysis strongly driven transmon",
        "quasienergy spectrum driven Josephson circuit",
        "measurement-induced state transitions transmon readout",
        "transmon ionization chaos strong drive",
        "parametric coupler drive-induced spurious transitions superconducting",
        "frequency collisions parametrically modulated superconducting circuit",
        "strong drive limits Josephson circuit parametric process",
    ],
    # Added after the first run's gold check: posted to arXiv two weeks before this search,
    # this paper had no citers and no index graph yet, and no query ranked it in the top
    # 50. A seed puts it in the corpus to be screened; it expands nothing. The other two
    # gold papers were found by the queries alone.
    seed_dois=("10.48550/arXiv.2609.04704",),
    year_from=2005,
    sources=("openalex", "inspire"),  # semanticscholar throttles keyless clients to nothing
    # Modest, because OpenAlex is metered and this run has no key: every seed costs one
    # citers query plus refs_per_seed lookups.
    max_rounds=2,
    seeds_per_round=8,
    refs_per_seed=15,
    # The three papers the question was posed with, by title. The gold set below checks
    # the same papers by DOI; the title check also catches a journal version whose DOI
    # differs from the arXiv one.
    known_items=[
        "Strong-Drive Limits in Josephson Circuits: From Chaos to an Unbound-Resonance Threshold",
        "Frequency collisions in parametrically modulated superconducting circuits",
        "A Linear Quantum Coupler for Clean Bosonic Control",
    ],
    gold_set=str(HERE / "gold_set.json"),
    # Screened on the PHYSICS, not the method. A first pass required a Floquet-type analysis
    # to be named, and abstracts rarely name the tool: it excluded one of the three gold
    # papers and left 43 papers on measurement-induced ionization and drive limits "unsure",
    # method unconfirmed. Whether a paper uses Floquet or Floquet-Markov simulation is a
    # full-text question, so it is the `method` extraction column instead.
    inclusion_criteria=(
        "Studies a strongly or periodically DRIVEN superconducting circuit -- a qubit, coupler, "
        "resonator, mixer or parametric device under a microwave drive or flux modulation -- and "
        "its drive-induced effects BEYOND the intended interaction: unwanted or measurement-"
        "induced state transitions, transmon ionization, chaos, multiphoton resonances, "
        "parasitic mixing products, frequency collisions, drive-strength limits, or dressed-state "
        "/ quasienergy structure. Theory, simulation methods (Floquet, Floquet-Markov, branch "
        "analysis or otherwise) and experiment all count, whether or not the abstract names the "
        "method. Device designs whose stated purpose is to suppress such effects count too."
    ),
    exclusion_criteria=(
        "Driven physics on other platforms (cold atoms, optical lattices, trapped ions, spins, "
        "solid-state Floquet topological phases); papers that only USE a drive for its intended "
        "gate, readout or amplification and do not study effects beyond it; discrete time "
        "crystals and Floquet phases of matter on qubit arrays, whose subject is the many-body "
        "phase rather than the driven device; noise, thermal-switching or Josephson-junction "
        "escape studies with no coherent periodic drive."
    ),
    screen_forbidden=(
        "cold atom",
        "optical lattice",
        "trapped ion",
        "trapped-ion",
        "nitrogen-vacancy",
        "graphene",
        "topological insulator",
        "high-harmonic",
        "bose-einstein",
    ),
    # Superconducting-circuit vocabulary a qualifying paper cannot avoid.
    screen_required=(
        "superconduct",
        "transmon",
        "fluxonium",
        "josephson",
        "snail",
        "squid",
        "circuit qed",
        "cqed",
        "circuit quantum electrodynamics",
    ),
    # Typed and defined after wave 1: "compared_to_experiment" came back as free text
    # ("True", "yes -- predicted...", "partly: ..."), and one "critical_photon_number" column
    # held avoided-crossing positions, ionization onsets and the dispersive n_crit.
    extraction_schema=(
        Column("device", "text", "the driven circuit, e.g. transmon, fluxonium, SNAIL coupler, SQUID"),
        Column("drive", "text", "what drives it -- readout tone, parametric pump, flux modulation -- and its role"),
        Column(
            "method",
            "text",
            "the simulation or analysis method as the paper names it, e.g. Floquet-Markov master "
            "equation, Floquet branch analysis, quasienergy spectrum, semiclassical, Lindblad",
        ),
        Column("phenomenon", "text", "the drive-induced effect studied: ionization, MIST, chaos, collisions..."),
        Column(
            "onset_photon_number",
            "number",
            "resonator photon number at which the paper states the drive-induced effect sets in "
            "(e.g. ionization onset); not an avoided-crossing position, not the dispersive n_crit",
        ),
        Column(
            "dispersive_ncrit",
            "number",
            "the dispersive critical photon number n_crit = (Delta/2g)^2, only if the paper states it",
        ),
        Column("drive_frequency_GHz", "number", "the drive frequency in GHz, only where stated as a number"),
        Column(
            "compared_to_experiment",
            "choice",
            "yes: this result is checked against measured data shown in the paper; qualitative: "
            "compared in words or with another group's data; no: simulation or theory only",
            ("yes", "qualitative", "no"),
        ),
    ),
    # A methods question: the papers worth reading in full first are the ones that build or
    # apply the simulation, not the ones that only measure. The default ranking (experiments
    # first, terms from the column names) put two of the three gold papers outside wave 1,
    # and column names like "compared_to_experiment" contributed terms every abstract has.
    priority_role_points={"theory": 3, "method": 3, "primary": 2, "review": 1, "": 1},
    priority_terms=(
        "floquet",
        "markov",
        "quasienergy",
        "branch",
        "chaos",
        "chaotic",
        "ionization",
        "multiphoton",
        "parasitic",
        "collisions",
    ),
    summary_focus=(
        "which Floquet methods are applied to which driven devices, what drive-induced effects "
        "they predict, and where the predictions have been checked against experiment"
    ),
    summary_group_by="theme",
)


if __name__ == "__main__":
    raise SystemExit(run(SPEC))
