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
from litsearch import batch
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
    record: object = None  # the index Record that resolved it, when one did

    def as_dict(self) -> dict:
        return {
            "title": self.work.title,
            "doi": self.work.doi,
            "arxiv_id": self.work.arxiv_id,
            "status": self.status,
            "index": self.index,
            "reason": self.reason,
        }


def enrich_from_record(work: Work, record) -> list[str]:
    """Fill the work's empty fields from the index record that verified it.

    The gate has already paid for this request, and the publisher's deposited record is
    more authoritative than a search hit. Without this the bibliography is full of
    "@article has no pages" -- OpenAlex frequently omits pagination that Crossref has.

    Only empty fields are filled; nothing already known is overwritten.
    """
    filled = []
    for work_field, record_field in (
        ("year", "year"),
        ("venue", "journal"),
        ("volume", "volume"),
        ("pages", "pages"),
    ):
        if not getattr(work, work_field, "") and getattr(record, record_field, ""):
            setattr(work, work_field, str(getattr(record, record_field)))
            filled.append(work_field)
    if not work.authors and getattr(record, "authors", None):
        work.authors = list(record.authors)
        filled.append("authors")
    return filled


def year_from_arxiv_id(arxiv_id: str | None) -> str:
    """A modern arXiv id encodes its year: 2301.07848 was submitted in 2023.

    Only used as a last resort, when no index supplied a year at all -- an entry with no
    year is an error in the bibliography, and this is better than dropping the work.
    """
    if not arxiv_id or len(arxiv_id) < 4 or not arxiv_id[:4].isdigit():
        return ""
    prefix = int(arxiv_id[:2])
    # arXiv's YYMM scheme started in 2007; two digits stay unambiguous well past 2030.
    return str(2000 + prefix) if 7 <= prefix <= 99 else ""


def validate(work: Work, client: IndexClient) -> Verdict:
    """Resolve one work against the indexes, cheapest and most authoritative first."""
    if work.doi:
        record = client.crossref_by_doi(work.doi)
        if record is None and is_repository_doi(work.doi):
            record = client.datacite_by_doi(work.doi)
            if record is not None:
                return Verdict(work, VERIFIED, "datacite", record=record)
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
        return Verdict(work, VERIFIED, index, record=record)
    return Verdict(
        work,
        QUARANTINED,
        index,
        f"title disagrees with {index} (similarity {ratio:.2f}): index has '{record.title[:80]}'",
    )


def validate_all(
    works: list[Work],
    client: IndexClient,
    save_every: int = 25,
    progress_every: int = 25,
) -> tuple[list[Work], list[Verdict]]:
    """Returns (works that passed, every verdict). Passed works carry their index.

    Validation is one throttled request per work, so a few hundred works takes a few
    minutes. The cache is flushed every ``save_every`` works: without that, an interrupted
    run throws away everything it fetched and the next run starts from nothing.
    """
    # Resolve every DOI in batches first. This is purely a speed measure: it seeds the
    # cache under the same keys the per-work lookups use, so the loop below is unchanged
    # and simply finds most of its answers already present. A DOI the batch misses is
    # still asked about individually.
    with_doi = [work.doi for work in works if work.doi]
    if with_doi:
        print(f"    prefetching {len(with_doi)} DOIs from Crossref in batches of {batch.BATCH_SIZE}")
        resolved = batch.prefetch_crossref(client, with_doi)
        print(f"    prefetch resolved {resolved} records")

    verdicts = []
    passed = []
    total = len(works)
    for position, work in enumerate(works, start=1):
        verdict = validate(work, client)
        work.validation = verdict.status
        work.validation_source = verdict.index
        verdicts.append(verdict)
        if verdict.status == VERIFIED:
            if verdict.record is not None:
                enrich_from_record(work, verdict.record)
            passed.append(work)
        if not work.year:
            work.year = year_from_arxiv_id(work.arxiv_id)
        if save_every and position % save_every == 0:
            client.save_cache()
        if progress_every and position % progress_every == 0:
            print(f"    validated {position}/{total} ({len(passed)} verified so far)")
    client.save_cache()
    return passed, verdicts
