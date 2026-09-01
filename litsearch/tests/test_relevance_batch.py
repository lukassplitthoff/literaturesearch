"""The topical guard, and batched DOI resolution."""

from __future__ import annotations

import json

import pytest

from bibcheck.verify import IndexClient
from litsearch import batch, relevance
from litsearch.sources.base import Work

QUERIES = [
    "superconducting qubit coherence time T1 T2 record",
    "tantalum transmon qubit long coherence",
    "fluxonium millisecond coherence time",
]


# ------------------------------------------------------------------------ relevance


def test_query_terms_drop_stopwords_and_short_tokens():
    terms = relevance.terms_from_queries(QUERIES)
    assert "superconducting" in terms and "transmon" in terms and "fluxonium" in terms
    assert "time" in terms, "a topical noun must survive even if common"
    for absent in ("the", "long", "and", "t1"[:1]):
        assert absent not in terms


def test_an_on_topic_paper_passes():
    terms = relevance.terms_from_queries(QUERIES)
    work = Work(title="Millisecond coherence in a tantalum transmon qubit",
                abstract="We measure T1 and T2 of a superconducting device.")
    assert relevance.is_on_topic(work, terms)


def test_the_papers_that_caused_the_drift_are_dropped():
    """These titles are verbatim from the run where the corpus wandered off topic."""
    terms = relevance.terms_from_queries(QUERIES)
    for title in (
        "Magnetic Domain-Wall Logic",
        "Doping semiconductor nanocrystals",
        "Exchange bias in nanostructures",
        "First-principles study of spontaneous polarization",
        "On lattices, learning with errors, random linear codes",
    ):
        assert not relevance.is_on_topic(Work(title=title), terms), f"should be off topic: {title}"


def test_a_single_shared_word_is_not_enough():
    """'quantum' alone matches half of physics; the threshold exists for this case."""
    terms = relevance.terms_from_queries(["quantum superconducting qubit coherence"])
    assert not relevance.is_on_topic(Work(title="Quantum field theory of gravity"), terms)


def test_filter_reports_how_many_it_dropped():
    terms = relevance.terms_from_queries(QUERIES)
    works = [
        Work(title="Transmon qubit coherence improvements"),
        Work(title="Exchange bias in nanostructures"),
        Work(title="Fluxonium coherence measurement"),
    ]
    kept, dropped = relevance.filter_on_topic(works, terms)
    assert len(kept) == 2 and dropped == 1


def test_the_guard_can_be_disabled():
    terms = relevance.terms_from_queries(QUERIES)
    kept, dropped = relevance.filter_on_topic([Work(title="Totally unrelated")], terms, min_hits=0)
    assert len(kept) == 1 and dropped == 0


def test_no_terms_means_nothing_is_admitted_rather_than_everything():
    """An empty query set is a caller bug; failing closed makes it visible."""
    assert not relevance.is_on_topic(Work(title="Anything at all"), set())


# ---------------------------------------------------------------------------- batch


@pytest.fixture
def client(tmp_path):
    def build(cache: dict | None = None, offline: bool = True) -> IndexClient:
        path = tmp_path / "cache.json"
        path.write_text(json.dumps(cache or {}), encoding="utf-8")
        return IndexClient(cache_path=path, offline=offline)

    return build


def test_batch_key_matches_the_per_work_key():
    """The whole design rests on this: a prefetch must satisfy crossref_by_doi."""
    assert batch.crossref_cache_key("10.1038/S41467-021-22030-5") == "crossref:doi:10.1038/s41467-021-22030-5"


def test_prefetch_is_a_noop_offline(client):
    c = client()
    assert batch.prefetch_crossref(c, ["10.1/a", "10.1/b"]) == 0


def test_prefetch_skips_dois_already_cached(client, monkeypatch):
    cached = {batch.crossref_cache_key("10.1/a"): {"payload": {"message": {"DOI": "10.1/a"}}}}
    c = client(cached, offline=False)
    calls = []
    monkeypatch.setattr(c, "_get", lambda *a, **k: calls.append(a) or None)
    batch.prefetch_crossref(c, ["10.1/a"], verbose=False)
    assert calls == [], "an already-cached DOI must not be re-requested"


def test_prefetch_populates_the_cache_in_the_per_work_shape(client, monkeypatch):
    c = client(offline=False)
    payload = {"message": {"items": [
        {"DOI": "10.1/a", "title": ["Paper A"]},
        {"DOI": "10.1/B", "title": ["Paper B"]},
    ]}}
    monkeypatch.setattr(c, "_get", lambda *a, **k: payload)
    found = batch.prefetch_crossref(c, ["10.1/a", "10.1/b"], verbose=False)
    assert found == 2
    # Stored lowercased, under the per-work key, wrapped exactly as _get returns it.
    assert c.cache[batch.crossref_cache_key("10.1/a")]["payload"]["message"]["title"] == ["Paper A"]
    assert batch.crossref_cache_key("10.1/b") in c.cache

    # The point of the exercise: a per-work lookup now answers from the seeded cache with
    # no request at all. Checked through a fresh OFFLINE client reading the saved cache,
    # so the stub _get above cannot be what satisfies it.
    c.save_cache()
    fresh = IndexClient(cache_path=c.cache_path, offline=True)
    record = fresh.crossref_by_doi("10.1/B")
    assert record is not None and record.title == "Paper B"


def test_a_doi_the_batch_misses_is_not_cached_as_absent(client, monkeypatch):
    """Negative caching here would let one bad batch quarantine real papers."""
    c = client(offline=False)
    monkeypatch.setattr(c, "_get", lambda *a, **k: {"message": {"items": []}})
    batch.prefetch_crossref(c, ["10.1/missing"], verbose=False)
    assert batch.crossref_cache_key("10.1/missing") not in c.cache


def test_batches_are_chunked(client, monkeypatch):
    c = client(offline=False)
    seen = []
    monkeypatch.setattr(c, "_get", lambda key, url, params=None, **k: seen.append(params) or {"message": {"items": []}})
    batch.prefetch_crossref(c, [f"10.1/{i}" for i in range(120)], batch_size=50, verbose=False)
    assert len(seen) == 3, "120 DOIs at 50 per request is 3 requests, not 120"


def test_duplicate_dois_are_requested_once(client, monkeypatch):
    c = client(offline=False)
    seen = []
    monkeypatch.setattr(c, "_get", lambda key, url, params=None, **k: seen.append(params) or {"message": {"items": []}})
    batch.prefetch_crossref(c, ["10.1/a"] * 40, batch_size=50, verbose=False)
    assert len(seen) == 1
    assert seen[0]["filter"].count("doi:") == 1


# ----------------------------------------------------------------------- triage funnel

FORBIDDEN = ("nitrogen-vacancy", "molecular spin", "vanadyl", "trapped ion")
REQUIRED = ("superconduct", "transmon", "fluxonium", "josephson")


def test_a_forbidden_platform_is_excluded_without_a_model_call():
    work = Work(title="Room-temperature coherence in vanadyl phthalocyanine spin qubits",
                abstract="We report magnetic relaxation and quantum coherence.")
    verdict, reason = relevance.triage(work, REQUIRED, FORBIDDEN)
    assert verdict == relevance.RULE_EXCLUDE
    assert "vanadyl" in reason


def test_a_paper_missing_every_subject_term_is_excluded():
    work = Work(title="Noise-adaptive compiler mappings for quantum computers",
                abstract="A compiler pass for qubit mapping.")
    verdict, _ = relevance.triage(work, REQUIRED, FORBIDDEN)
    assert verdict == relevance.RULE_EXCLUDE


def test_an_on_topic_paper_still_goes_to_the_model():
    """Rules may prove a paper is off subject; they must never assert it qualifies."""
    work = Work(title="Enhanced coherence of all-nitride superconducting qubits",
                abstract="Improving the coherence of superconducting qubits.")
    verdict, _ = relevance.triage(work, REQUIRED, FORBIDDEN)
    assert verdict == relevance.NEEDS_AI, "only a read abstract can confirm a measurement"


def test_forbidden_beats_required():
    """A superconducting resonator hosting spin qubits is still the wrong platform."""
    work = Work(title="Vanadyl spin qubit arrays on superconducting resonators",
                abstract="2D vanadyl porphyrin layers with superior spin coherence.")
    verdict, reason = relevance.triage(work, REQUIRED, FORBIDDEN)
    assert verdict == relevance.RULE_EXCLUDE and "vanadyl" in reason


def test_triage_all_stamps_reasons_and_splits():
    works = [
        Work(title="Transmon coherence measurement", abstract="superconducting qubit T1"),
        Work(title="NV magnetometry", abstract="nitrogen-vacancy centre in diamond"),
    ]
    to_model, excluded = relevance.triage_all(works, REQUIRED, FORBIDDEN)
    assert len(to_model) == 1 and len(excluded) == 1
    assert excluded[0].screen == "exclude"
    assert excluded[0].screen_reason.startswith("rule:")


def test_no_rules_means_everything_reaches_the_model():
    works = [Work(title="Anything"), Work(title="Anything else")]
    to_model, excluded = relevance.triage_all(works)
    assert len(to_model) == 2 and excluded == []


def test_rule_verdicts_survive_applying_model_verdicts():
    """Triage runs before the batches; its verdicts must not be wiped on the way back."""
    from litsearch.corpus import Corpus
    from litsearch import screen

    corpus = Corpus()
    corpus.add_all([Work(title="Transmon paper", doi="10.1/a", abstract="superconducting"),
                    Work(title="NV paper", doi="10.1/b", abstract="nitrogen-vacancy centre")])
    relevance.triage_all(corpus.works, REQUIRED, FORBIDDEN)
    counts = screen.apply_verdicts(corpus, {0: {"verdict": "include", "reason": "on topic"}})
    assert counts["include"] == 1
    assert counts["exclude"] == 1 and counts["by_rule"] == 1
    assert counts["unscreened"] == 0, "a rule-settled work is not unscreened"
