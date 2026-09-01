"""Keeping the corpus on topic.

Snowballing is what gives a literature search its recall, and it is also what destroys
its precision. A seed paper's citers and references span everything its authors happened
to cite: a coherence paper cites fabrication, materials science, control theory and
quantum error correction, and two rounds of unfiltered expansion turn a 186-work corpus
about qubit coherence into a 1000-work corpus about condensed matter generally.

Saturation cannot catch this. ``new_fraction`` measures growth, not relevance, and the
neighbourhood of the literature is effectively endless, so the metric keeps reporting
"still finding things" while the things being found get steadily less relevant.

The guard here is deterministic and cheap: a work harvested by snowballing has to look
like the question before it is admitted. Direct query hits bypass it entirely -- the
search engine already ranked those for relevance, and second-guessing it with a keyword
rule would only lose papers.

This is a filter on *expansion*, not a judgement of quality. It runs before screening and
is deliberately generous: its job is to stop the corpus wandering into another field, not
to decide what is worth reading. That decision belongs to the screener, which reads the
abstract properly.
"""

from __future__ import annotations

import re

# Words that carry no topical signal. Kept deliberately short: this is a stoplist for
# query terms, not for prose, so only the words that would match nearly everything.
STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have how in into is it its of on or that the
    their there these this to was were what when where which who why with within without
    using use used via toward towards between across over under more most best better
    new novel recent improved improving high higher low lower long longer large small
    study studies analysis approach method methods result results paper report review
    """.split()
)

TOKEN = re.compile(r"[a-z0-9]+")
MIN_TERM_LENGTH = 3

# How many distinct query terms a snowballed work must show. Two is deliberately lenient:
# it keeps a paper that mentions "transmon" and "coherence" while dropping one that shares
# only the word "quantum".
DEFAULT_MIN_TERM_HITS = 2


def terms_from_queries(queries: list[str]) -> set[str]:
    """The topical vocabulary of a search, taken from its query strings."""
    terms = set()
    for query in queries:
        for token in TOKEN.findall(query.lower()):
            if len(token) >= MIN_TERM_LENGTH and token not in STOPWORDS:
                terms.add(token)
    return terms


def term_hits(text: str, terms: set[str]) -> int:
    """How many distinct topical terms appear in the text."""
    if not terms:
        return 0
    found = set(TOKEN.findall((text or "").lower()))
    return len(found & terms)


def score(work, terms: set[str]) -> int:
    """Distinct query terms present in a work's title and abstract."""
    return term_hits(f"{getattr(work, 'title', '')} {getattr(work, 'abstract', '')}", terms)


def is_on_topic(work, terms: set[str], min_hits: int = DEFAULT_MIN_TERM_HITS) -> bool:
    """Should a snowballed work be admitted to the corpus?

    A work with no abstract is judged on its title alone, which is harsher than it
    deserves; the threshold is low enough that a title naming the subject still passes.
    """
    return score(work, terms) >= min_hits


def filter_on_topic(works: list, terms: set[str], min_hits: int = DEFAULT_MIN_TERM_HITS) -> tuple[list, int]:
    """Split harvested works into (kept, number dropped)."""
    kept = [work for work in works if is_on_topic(work, terms, min_hits)]
    return kept, len(works) - len(kept)


# --------------------------------------------------------------------------- triage
#
# Screening a corpus costs one model call per abstract, and on a real run 60% of those
# calls returned "exclude" for a reason a string match could have found: the paper is
# about a different physical platform. Molecular spin qubits, NV centres, trapped ions
# and photonic encodings all use the words "qubit", "coherence" and "relaxation", so the
# topical guard admits them, and the model then spends a call each to say so.
#
# Triage puts a rule in front of the model. Rules decide the clear-cut cases for free and
# the model adjudicates only the genuine middle. The rules are supplied per search, not
# baked in -- the mechanism is general, the vocabulary is not.

RULE_EXCLUDE = "exclude_rule"
NEEDS_AI = "needs_ai"



def triage(work, required_any: tuple = (), forbidden_any: tuple = ()) -> tuple[str, str]:
    """Decide whether a rule can settle this work, or the model has to read it.

    Returns (verdict, reason) where verdict is RULE_EXCLUDE or NEEDS_AI. There is no
    RULE_INCLUDE on purpose: a rule may cheaply prove a paper is about the wrong subject,
    but it cannot prove a paper reports the measurement -- that needs the abstract read
    properly, and guessing "include" from keywords is how unrelated papers end up cited.

    ``forbidden_any``: any of these phrases present -> excluded. Use for other platforms
    and publication types that can never satisfy the criteria.
    ``required_any``: none of these present -> excluded. Use for the subject's own
    vocabulary, the terms a qualifying paper cannot avoid using.
    """
    haystack = f"{getattr(work, 'title', '')} {getattr(work, 'abstract', '')}".lower()

    for phrase in forbidden_any:
        if phrase.lower() in haystack:
            return RULE_EXCLUDE, f"rule: mentions '{phrase}'"

    if required_any:
        if not any(phrase.lower() in haystack for phrase in required_any):
            return RULE_EXCLUDE, "rule: none of the required subject terms present"

    return NEEDS_AI, ""


def triage_all(works: list, required_any: tuple = (), forbidden_any: tuple = ()) -> tuple[list, list]:
    """Split works into (needing a model call, rule-excluded with reasons attached)."""
    to_model, excluded = [], []
    for work in works:
        verdict, reason = triage(work, required_any, forbidden_any)
        if verdict == RULE_EXCLUDE:
            work.screen = "exclude"
            work.screen_reason = reason
            excluded.append(work)
        else:
            to_model.append(work)
    return to_model, excluded
