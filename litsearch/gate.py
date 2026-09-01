"""The validation gate.

Nothing reaches a litsearch output that has not been resolved against an authoritative
index. This is not a quality heuristic -- it is what makes a fabricated reference
impossible to publish: an invented paper has no DOI Crossref knows, no arXiv id that
resolves, and no title OpenAlex can confirm, so it fails by construction rather than by
the model choosing to behave.

Verdicts:
    verified    resolved against an index; may enter the outputs
    quarantined resolved to nothing, or to something that disagrees; reported, never
                silently dropped and never silently included

Software and dataset entries routinely land in quarantine because they carry no Crossref
DOI. That is expected and is a decision for a human, not a bug to code around.
"""

from __future__ import annotations

from dataclasses import dataclass

from bibcheck.verify import IndexClient, is_repository_doi
from litsearch.corpus import TITLE_MATCH_RATIO, title_similarity
from litsearch.sources.base import Work

VERIFIED = "verified"
QUARANTINED = "quarantined"


@dataclass
class Verdict:
    work: Work
    status: str
    index: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "title": self.work.title,
            "doi": self.work.doi,
            "arxiv_id": self.work.arxiv_id,
            "status": self.status,
            "index": self.index,
            "reason": self.reason,
        }


def validate(work: Work, client: IndexClient) -> Verdict:
    """Resolve one work against the indexes, cheapest and most authoritative first."""
    if work.doi:
        record = client.crossref_by_doi(work.doi)
        if record is None and is_repository_doi(work.doi):
            record = client.datacite_by_doi(work.doi)
            if record is not None:
                return Verdict(work, VERIFIED, "datacite")
        if record is not None:
            return _confirm_title(work, record, "crossref")
        return Verdict(work, QUARANTINED, reason=f"DOI {work.doi} not found in Crossref or DataCite")

    if work.arxiv_id:
        record = client.arxiv_by_id(work.arxiv_id)
        if record is not None:
            return _confirm_title(work, record, "arxiv")
        return Verdict(work, QUARANTINED, reason=f"arXiv id {work.arxiv_id} does not resolve")

    if work.title:
        record = client.openalex_search(work.title)
        if record is not None:
            return _confirm_title(work, record, "openalex")
    return Verdict(work, QUARANTINED, reason="no DOI, no arXiv id, and no index match on title")


def _confirm_title(work: Work, record, index: str) -> Verdict:
    """An index hit still has to be the same paper."""
    from bibcheck.keys import normalize_title

    ratio = title_similarity(work.norm_title, normalize_title(record.title))
    if ratio >= TITLE_MATCH_RATIO:
        return Verdict(work, VERIFIED, index)
    return Verdict(
        work,
        QUARANTINED,
        index,
        f"title disagrees with {index} (similarity {ratio:.2f}): index has '{record.title[:80]}'",
    )


def validate_all(works: list[Work], client: IndexClient) -> tuple[list[Work], list[Verdict]]:
    """Returns (works that passed, every verdict). Passed works carry their index."""
    verdicts = []
    passed = []
    for work in works:
        verdict = validate(work, client)
        work.validation = verdict.status
        work.validation_source = verdict.index
        verdicts.append(verdict)
        if verdict.status == VERIFIED:
            passed.append(work)
    return passed, verdicts
