"""Stage 6, deterministic half: fetch each paper's PDF and turn it into text.

On the first run with full-text extraction, the extractor agents did this themselves --
curl, a PDF converter, arXiv lookups -- and every quality problem of that run traced back
to it: a converter that dropped Greek letters, a layout mode that interleaved two columns
so sentences could not be quoted, publisher "PDF" links that returned HTML pages, and
helper scripts that collided in a shared scratch folder. It also broke the house rule that
the model never makes an HTTP request.

So Python does it, once, the same way for every paper: arXiv first (open, and the same
work), found by id or else by title; the open-access link after that; every download
checked to really be a PDF. The text goes to ``extract/text/<key>.txt``, and the extractor
reads that file and nothing else. The same text is what ``quote_found`` checks quotes
against, so a quote either occurs in what the extractor was given or it is refused.

Conversion is pdftotext in reading order when it is installed, pypdf otherwise. On the 89
verified quotes of that first run, ``pdftotext -enc UTF-8`` kept 65 intact and pypdf 40;
``pdftotext -layout`` kept 9, because it interleaves columns. Both are licence-clean here:
pdftotext runs as a separate program, and pypdf is BSD.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pypdf

from litsearch.sources.base import Work, clean_arxiv_id

PDF_DIR = "pdf"
TEXT_DIR = "text"
ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"

# Hyphen-like characters removed before comparing a quote with the text. A line-break
# hyphen ("Flo-\nquet") and a compound hyphen ("Floquet-Markov") cannot be told apart
# reliably, so neither side keeps any; the en dash is kept, because "1-2" and "12" differ.
_HYPHENS = dict.fromkeys((ord("-"), 0x00AD, 0x2010, 0x2011), None)


@dataclass
class FullText:
    """Where one paper's text came from, or why there is none."""

    key: str
    text_path: str = ""  # relative to the extract directory; "" when no text was obtained
    source: str = ""  # "arxiv", "arxiv-by-title" or "open-access"
    note: str = ""


def canonical(text: str) -> str:
    """The form quotes are compared in: case, whitespace and hyphenation ignored.

    What remains -- letters, digits, symbols and their order -- is what makes a quote
    verbatim. A changed word, number or unit still fails.
    """
    text = unicodedata.normalize("NFKC", text or "").translate(_HYPHENS).lower()
    return "".join(text.split())


def quote_found(quote: str, text: str) -> bool:
    """Does the quote occur in the text, up to case, whitespace and hyphenation?"""
    needle = canonical(quote)
    return bool(needle) and needle in canonical(text)


def pdf_to_text(pdf_path: Path) -> str:
    """Extract a PDF's text in reading order: pdftotext when installed, else pypdf."""
    pdf_path = Path(pdf_path)
    if shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.decode("utf-8", errors="replace")
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def valid_arxiv_id(value: str | None) -> str | None:
    """The id if it can be a real arXiv id, else None.

    Index records sometimes carry a malformed one -- a real run had "2026.10043", whose
    month would be 26 -- and trusting it meant a failed download with no fallback.
    """
    arxiv_id = clean_arxiv_id(value)
    if not arxiv_id:
        return None
    new_style = re.match(r"^(\d{2})(\d{2})\.\d{4,5}$", arxiv_id)
    if new_style:
        return arxiv_id if 1 <= int(new_style.group(2)) <= 12 else None
    return arxiv_id if "/" in arxiv_id else None  # old style, e.g. cond-mat/0501001


def _candidates(client, work: Work):
    """(source, url) pairs to try, best first. Lazy: a later source costs nothing unless
    an earlier one failed -- the title search in particular is a request of its own."""
    arxiv_id = valid_arxiv_id(work.arxiv_id)
    if arxiv_id:
        yield "arxiv", ARXIV_PDF.format(arxiv_id=arxiv_id)
    record = client.arxiv_search_title(work.title)
    found = valid_arxiv_id(record.url) if record else None
    if found and found != arxiv_id:
        yield "arxiv-by-title", ARXIV_PDF.format(arxiv_id=found)
    if work.oa_pdf_url:
        yield "open-access", work.oa_pdf_url


def fetch_text(client, work: Work, key: str, extract_dir: Path) -> FullText:
    """Get one paper's text onto disk. Never raises: a failure is a note, not an error.

    ``client`` is a bibcheck IndexClient -- the one HTTP layer, with its throttle and
    retries. An existing text file is reused, so a re-run fetches nothing twice.
    """
    extract_dir = Path(extract_dir)
    text_rel = f"{TEXT_DIR}/{key}.txt"
    text_path = extract_dir / text_rel
    if text_path.exists() and text_path.stat().st_size > 0:
        return FullText(key, text_rel, "cached")

    reasons = []
    for source, url in _candidates(client, work):
        pdf_path = extract_dir / PDF_DIR / f"{key}.pdf"
        failure = client.download(url, pdf_path)
        if failure:
            reasons.append(f"{source}: {failure}")
            continue
        try:
            text = pdf_to_text(pdf_path)
        except Exception as exc:  # a malformed PDF must not stop the other papers
            reasons.append(f"{source}: conversion failed ({type(exc).__name__})")
            pdf_path.unlink(missing_ok=True)
            continue
        if len(text.split()) < 200:
            reasons.append(f"{source}: too little text, likely a scanned or image-only PDF")
            pdf_path.unlink(missing_ok=True)
            continue
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        return FullText(key, text_rel, source)
    return FullText(key, "", "", "; ".join(reasons) or "no arXiv id, no arXiv match and no open-access link")


def load_texts(extract_dir: Path) -> dict[str, str]:
    """Every paper text on disk, by cite key."""
    folder = Path(extract_dir) / TEXT_DIR
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.txt"))}
