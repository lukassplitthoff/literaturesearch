"""Stage 7 renderers: what the deterministic spine can emit without a model."""

from __future__ import annotations

import json
from pathlib import Path

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


def write_shortlist(path: Path, works: list, limit: int = 50) -> None:
    """A readable table of what survived the gate, most cited first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = sorted(works, key=lambda w: w.cited_by_count, reverse=True)[:limit]
    lines = [
        "# Validated shortlist",
        "",
        f"{len(works)} works passed the validation gate; the top {len(ranked)} by citation count:",
        "",
        "| Cites | Year | Title | Venue | Validated by | OA PDF |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for work in ranked:
        title = (work.title or "")[:70].replace("|", "/")
        venue = (work.venue or "")[:30].replace("|", "/")
        year = work.year or "-"
        pdf = "yes" if work.oa_pdf_url else "-"
        lines.append(f"| {work.cited_by_count} | {year} | {title} | {venue} | {work.validation_source} | {pdf} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_run_log(
    path: Path,
    cfg: SearchConfig,
    corpus: Corpus,
    rounds: list,
    verdicts: list[Verdict],
    known: list[dict],
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
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
