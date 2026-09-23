"""Stage 7 renderers: what the deterministic spine can emit without a model."""

from __future__ import annotations

import json
from pathlib import Path

from litsearch import rank
from litsearch.config import SearchConfig
from litsearch.corpus import Corpus, title_similarity
from litsearch.gate import VERIFIED, Verdict

KNOWN_ITEM_RATIO = 0.80


def known_item_results(corpus: Corpus, known_items: list[str]) -> list[dict]:
    """Did retrieval find the papers you already know must be there?"""
    results = []
    for wanted in known_items:
        best_ratio = 0.0
        best_title = ""
        for work in corpus.works:
            ratio = title_similarity(wanted.lower(), work.title.lower())
            if ratio > best_ratio:
                best_ratio = ratio
                best_title = work.title
        results.append(
            {
                "wanted": wanted,
                "found": best_ratio >= KNOWN_ITEM_RATIO,
                "best_match": best_title,
                "similarity": round(best_ratio, 3),
            }
        )
    return results


def write_quarantine(path: Path, verdicts: list[Verdict]) -> int:
    """Write the held-back works. Returns how many there were."""
    held = [v for v in verdicts if v.status != VERIFIED]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Quarantine", ""]
    if not held:
        lines.append("Every work resolved against an index. Nothing held back.")
    else:
        lines.append(f"{len(held)} works did not pass the validation gate. None of them reached")
        lines.append("the outputs. Each needs a human decision -- software and dataset entries")
        lines.append("commonly land here because they carry no Crossref DOI.")
        lines.append("")
        lines.append("| Title | DOI | arXiv | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for verdict in held:
            title = (verdict.work.title or "(no title)")[:70].replace("|", "/")
            doi = verdict.work.doi or "-"
            arxiv = verdict.work.arxiv_id or "-"
            lines.append(f"| {title} | {doi} | {arxiv} | {verdict.reason} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(held)


def write_shortlist(
    path: Path, works: list, limit: int = 50, now_year: int | None = None, unscreened: int = 0
) -> None:
    """A readable table of the screened-in, validated works, best first.

    ``works`` must be the works screening INCLUDED. This used to be every work that passed
    the gate, which made it a popularity ranking of the whole snowballed corpus: on a real
    run its top rows were a mobile-edge-computing survey and an optics review, because the
    snowball admits highly cited neighbours and citations per year favours them. The gate
    decides what is real; only screening decides what is relevant. So with screening
    unfinished there is no shortlist, only a note saying so.

    Ordered by citations per year rather than by raw citation count. The raw count is a
    function of age as much as of impact, so sorting on it puts the oldest papers at the
    top of every search and pushes the current state of the art -- usually the reason for
    the search -- off the end of the table. Both numbers are shown, so the ordering can be
    disagreed with rather than merely trusted.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if unscreened or not works:
        reason = (
            f"{unscreened} work(s) still have no screening verdict"
            if unscreened
            else "screening included no validated work"
        )
        path.write_text(
            f"# Shortlist\n\nNo shortlist: {reason}. Relevance is decided by screening.\n", encoding="utf-8"
        )
        return
    ranked = rank.rank(works, now_year=now_year)[:limit]
    lines = [
        "# Shortlist",
        "",
        f"{len(works)} works were screened in and passed the validation gate; the top {len(ranked)} by "
        "citations per year. A ranking by attention, not by relevance -- every row already passed "
        "screening, and priority.md orders the same works for reading in full.",
        "",
        "`Cited here` is how many other works in this corpus cite this one -- a landmark signal",
        "local to this question. It counts only references the search actually fetched, so a zero",
        "means the citation was not retrieved rather than that it does not exist.",
        "",
        "| Cites/yr | Cites | Cited here | Year | Role | Title | Venue | Validated by | OA PDF |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for work, signal in ranked:
        title = (work.title or "")[:70].replace("|", "/")
        venue = (work.venue or "")[:30].replace("|", "/")
        year = work.year or "-"
        pdf = "yes" if work.oa_pdf_url else "-"
        lines.append(
            f"| {signal['citations_per_year']} | {work.cited_by_count} | {signal['in_corpus_citations']} "
            f"| {year} | {work.role or '-'} | {title} | {venue} | {work.validation_source} | {pdf} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_run_log(
    path: Path,
    cfg: SearchConfig,
    corpus: Corpus,
    rounds: list,
    verdicts: list[Verdict],
    known: list[dict],
    gold: dict | None = None,
) -> None:
    """Everything needed to reproduce or audit the run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "question": cfg.question,
        "queries": cfg.queries,
        "sources": list(cfg.sources),
        "year_from": cfg.year_from,
        "year_to": cfg.year_to,
        "corpus_size": len(corpus),
        "verified": sum(1 for v in verdicts if v.status == VERIFIED),
        "quarantined": sum(1 for v in verdicts if v.status != VERIFIED),
        "saturation_curve": [r.as_dict() for r in rounds],
        "known_items": known,
        "gold_set": gold,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ gold-set recall


def load_gold_set(path) -> list[dict]:
    """Read a gold set: papers a domain expert says must be found.

    Distinct from ``known_items``, which are titles an earlier run produced and therefore
    only guard against regression. A gold set is chosen by someone who has not seen the
    output, so recall against it is a measurement rather than the system agreeing with
    itself. Matched on DOI, then arXiv id -- both exact, no fuzzy title comparison to argue about.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["papers"] if isinstance(data, dict) else list(data)


def gold_recall(corpus, gold: list[dict]) -> dict:
    """Which gold papers the corpus contains, and what happened to each."""
    from litsearch.sources.base import clean_arxiv_id, clean_doi

    by_doi = {work.doi: work for work in corpus.works if work.doi}
    # A preprint and its journal version are one paper with two DOIs, and dedup keeps the
    # journal one. The arXiv id survives the merge, so it is the second exact key: without
    # it, a gold preprint that retrieval found as its published version counts as missed.
    by_arxiv = {work.arxiv_id: work for work in corpus.works if work.arxiv_id}
    rows = []
    for paper in gold:
        doi = clean_doi(paper.get("doi"))
        arxiv_id = clean_arxiv_id(paper.get("arxiv") or doi)
        work = by_doi.get(doi) or (by_arxiv.get(arxiv_id) if arxiv_id else None)
        rows.append(
            {
                "key": paper.get("key", doi),
                "doi": doi,
                "title": paper.get("title", ""),
                "found": work is not None,
                # A paper found but screened out is a different failure from one never found,
                # and conflating them hides which half of the pipeline needs work.
                "screen": (work.screen or "unscreened") if work else "",
                "validation": (work.validation or "") if work else "",
            }
        )
    found = sum(1 for r in rows if r["found"])
    return {
        "total": len(rows),
        "found": found,
        "recall_pct": round(100 * found / len(rows), 1) if rows else 0.0,
        "missed": [r["key"] for r in rows if not r["found"]],
        "found_but_screened_out": [r["key"] for r in rows if r["found"] and r["screen"] in ("exclude", "unsure")],
        "rows": rows,
    }
