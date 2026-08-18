"""Completeness and hygiene rules, plus the safe in-place normalisations.

Two distinct operations, kept separate so that checking stays pure:

* ``normalize_entry`` rewrites things that are unambiguously wrong in a way that has a
  single correct answer: ``@Article`` -> ``@article``, ``journal = {arXiv:2004.14256}``
  -> ``eprint`` + ``archivePrefix``, ``https://doi.org/10.1/x`` -> ``10.1/x``,
  ``705-710`` -> ``705--710``.
* ``check_entry`` reports what is missing or suspicious but changes nothing.

Every finding carries a level: ``error`` for something that would break or mislead a
reader, ``warning`` for something a referee would ask about, ``info`` for a change that
was made automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lib.utils.bibcheck.keys import (
    entry_year,
    first_author_surname,
    has_placeholder,
    latexify,
    normalize_title,
    split_authors,
)
from lib.utils.bibcheck.parser import ZERO_WIDTH, Database, Entry

# Fields without which the entry is not a usable reference.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "article": ("author", "title", "year"),
    "book": ("author", "title", "publisher", "year"),
    "inbook": ("author", "title", "publisher", "year"),
    "incollection": ("author", "title", "booktitle", "year"),
    "inproceedings": ("author", "title", "booktitle", "year"),
    "conference": ("author", "title", "booktitle", "year"),
    "mastersthesis": ("author", "title", "school", "year"),
    "phdthesis": ("author", "title", "school", "year"),
    "techreport": ("author", "title", "institution", "year"),
    "misc": ("author", "title", "year"),
    "software": ("author", "title", "year"),
    "dataset": ("author", "title", "year"),
    "unpublished": ("author", "title", "note"),
}

# Fields a referee would expect but whose absence is not fatal.
RECOMMENDED_FIELDS: dict[str, tuple[str, ...]] = {
    "article": ("journal", "volume", "pages", "doi"),
    "book": ("doi",),
    "incollection": ("pages", "doi"),
    "inproceedings": ("pages", "doi"),
    "conference": ("pages", "doi"),
}

# At least one of each group must be present.
ONE_OF_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "software": (("url", "doi", "eprint"),),
    "dataset": (("url", "doi"),),
}

# Same idea, but reported as a warning: BibTeX's @misc requires no fields at all, and
# a private communication legitimately has nothing to link to.
ONE_OF_ADVISORY: dict[str, tuple[tuple[str, ...], ...]] = {
    "misc": (("url", "doi", "eprint", "note", "howpublished"),),
    # A thesis needs to be findable, but a repository DOI does that as well as a link.
    "phdthesis": (("doi", "url"),),
    "mastersthesis": (("doi", "url"),),
    "techreport": (("doi", "url"),),
}

# arXiv identifiers: post-2007 (2503.10623v2) and pre-2007 (quant-ph/0510027) forms.
ARXIV_NEW = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
ARXIV_OLD = re.compile(r"\b([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
ARXIV_CATEGORY = re.compile(r"\[([a-z-]+(?:\.[A-Za-z]{2})?)\]")

DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/\S+")

# Trailing words that mark an author field as an organisation rather than a person.
ORGANISATION_WORDS = frozenset(
    {
        "software",
        "inc",
        "ltd",
        "llc",
        "gmbh",
        "corp",
        "corporation",
        "company",
        "technologies",
        "technology",
        "systems",
        "solutions",
        "university",
        "institute",
        "laboratory",
        "laboratories",
        "collaboration",
        "consortium",
        "foundation",
        "team",
    }
)

PAGE_RANGE_BAD = re.compile(r"^(\w+)\s*(?:-|–|—)\s*(\w+)$")
YEAR_EXACT = re.compile(r"^\d{4}$")


@dataclass
class Finding:
    """One reported problem or automatic change.

    Attributes:
        level: ``error``, ``warning`` or ``info``.
        code: Stable machine-readable slug, e.g. ``missing-field``.
        message: ASCII description for the console and the Markdown report.
        key: Citation key the finding belongs to, or None for file-level findings.
        line: Source line of the entry, 0 when unknown.
    """

    level: str
    code: str
    message: str
    key: str | None = None
    line: int = 0

    def render(self) -> str:
        """One-line ASCII rendering used by the console report."""
        where = f"{self.key}" if self.key else "<file>"
        return f"[{self.level:7s}] {where}: {self.message}"


@dataclass
class EntryReport:
    """Everything known about one entry after normalisation and checking."""

    entry: Entry
    findings: list[Finding] = field(default_factory=list)
    arxiv_id: str | None = None
    doi: str | None = None
    is_preprint: bool = False


# ----------------------------------------------------------------- normalisation


def _find_arxiv_id(text: str) -> str | None:
    """Return a bare arXiv id found anywhere in ``text``, without the version suffix."""
    match = ARXIV_NEW.search(text)
    if match:
        return match.group(1)
    match = ARXIV_OLD.search(text)
    if match:
        return match.group(1)
    return None


def normalize_entry(entry: Entry, ascii_only: bool = False) -> list[Finding]:
    """Apply the unambiguous fixes to ``entry`` in place and describe what changed.

    Args:
        entry: The entry to fix, mutated in place.
        ascii_only: Also rewrite non-ASCII characters in every field as LaTeX escapes.
            Off by default, because it is the one normalisation that changes bytes the
            author typed rather than only their arrangement.
    """
    findings: list[Finding] = []

    def note(code: str, message: str, level: str = "info") -> None:
        findings.append(Finding(level, code, message, entry.effective_key, entry.line))

    if entry.type_raw != entry.type:
        note("entry-type-case", f"entry type @{entry.type_raw} normalised to @{entry.type}")

    _normalize_arxiv(entry, note)
    _normalize_doi(entry, note)
    _normalize_pages(entry, note)
    _normalize_whitespace(entry)
    if ascii_only:
        _normalize_ascii(entry, note)
    return findings


def _normalize_arxiv(entry: Entry, note) -> None:
    """Move an arXiv id out of ``note``/``journal`` into the proper eprint fields."""
    eprint = entry.get("eprint")
    if eprint and eprint.strip():
        bare = _find_arxiv_id(eprint)
        if bare is None:
            # 'eprint' holds something that is not a preprint identifier -- most often a
            # publisher PDF link pasted from a browser. Treating it as an arXiv id makes
            # the entry look like a preprint and sends a malformed query to the arXiv API.
            _relocate_bad_eprint(entry, eprint.strip(), note)
            return
        if bare != eprint.strip():
            entry.set("eprint", bare)
            note("arxiv-eprint", f"eprint normalised to bare arXiv id {bare}")
        if not entry.has("archivePrefix"):
            entry.set("archivePrefix", "arXiv")
            note("arxiv-eprint", "added archivePrefix = {arXiv}")
        return

    for source in ("journal", "note", "url", "howpublished"):
        text = entry.get(source)
        if not text or "arxiv" not in text.lower():
            continue
        arxiv_id = _find_arxiv_id(text)
        if arxiv_id is None:
            continue
        entry.set("eprint", arxiv_id)
        if not entry.has("archivePrefix"):
            entry.set("archivePrefix", "arXiv")
        category = ARXIV_CATEGORY.search(text)
        if category and not entry.has("primaryClass"):
            entry.set("primaryClass", category.group(1))
        # The source field held nothing but the arXiv reference: drop it rather than
        # leaving 'journal = {arXiv:2004.14256}', which typesets as a journal name.
        residue = text.replace(category.group(0), "") if category else text
        residue = re.sub(r"(?i)arxiv[:\s]*", "", residue)
        residue = re.sub(re.escape(arxiv_id) + r"(v\d+)?", "", residue).strip(" .,:;[]")
        if not residue:
            entry.pop(source)
            note("arxiv-relocated", f"moved arXiv id {arxiv_id} from {source} into eprint/archivePrefix")
        else:
            note("arxiv-relocated", f"copied arXiv id {arxiv_id} from {source} into eprint/archivePrefix")
        return


def _relocate_bad_eprint(entry: Entry, eprint: str, note) -> None:
    """Deal with an ``eprint`` field that does not hold an arXiv identifier."""
    if not eprint.lower().startswith(("http://", "https://", "www.")):
        note("bad-eprint", f"eprint {eprint!r} is not an arXiv identifier", level="warning")
        return
    entry.pop("eprint")
    entry.pop("archivePrefix")
    entry.pop("primaryClass")
    if entry.has("url") or entry.has("doi"):
        note("bad-eprint", "dropped a publisher PDF link from eprint; the entry already has a url/doi")
    else:
        entry.set("url", eprint)
        note("bad-eprint", "moved a publisher PDF link from eprint into url")


def _normalize_doi(entry: Entry, note) -> None:
    """Strip URL/`doi:` prefixes from the doi field and recover a DOI hidden in url."""
    doi = entry.get("doi")
    if doi:
        cleaned = DOI_PREFIX.sub("", doi).strip().rstrip(".")
        if cleaned != doi:
            entry.set("doi", cleaned)
            note("doi-prefix", f"stripped resolver prefix from doi -> {cleaned}")
        return
    url = entry.get("url") or ""
    if "doi.org/" in url.lower():
        match = DOI_IN_TEXT.search(url)
        if match:
            entry.set("doi", match.group(0))
            note("doi-from-url", f"recovered doi {match.group(0)} from the url field")


def _normalize_pages(entry: Entry, note) -> None:
    """Rewrite a single-hyphen or en-dash page range as the BibTeX double hyphen."""
    pages = entry.get("pages")
    if not pages:
        return
    match = PAGE_RANGE_BAD.match(pages.strip())
    if match:
        fixed = f"{match.group(1)}--{match.group(2)}"
        entry.set("pages", fixed)
        note("page-range", f"page range {pages.strip()} normalised to {fixed}")


def _normalize_whitespace(entry: Entry) -> None:
    """Collapse the line breaks that hand-wrapped author lists leave inside values."""
    for name, value in entry.fields.items():
        if value.delim in ("brace", "quote") and "\n" in value.text:
            entry.fields[name].text = re.sub(r"\s*\n\s*", " ", value.text).strip()


# ----------------------------------------------------------------------- checking


def check_entry(entry: Entry) -> EntryReport:
    """Check one already-normalised entry for completeness and suspicious values."""
    report = EntryReport(entry=entry)

    def add(level: str, code: str, message: str) -> None:
        report.findings.append(Finding(level, code, message, entry.effective_key, entry.line))

    report.doi = entry.get("doi")
    report.arxiv_id = entry.get("eprint")
    report.is_preprint = bool(report.arxiv_id) and not entry.has("journal")

    if entry.type not in REQUIRED_FIELDS:
        add("warning", "unknown-type", f"unrecognised entry type @{entry.type}; completeness not checked")
        return report

    for name in REQUIRED_FIELDS[entry.type]:
        if not entry.has(name):
            add("error", "missing-field", f"@{entry.type} is missing required field {name}")

    for name in RECOMMENDED_FIELDS.get(entry.type, ()):
        if entry.has(name):
            continue
        if report.is_preprint and name in ("journal", "volume", "pages"):
            continue  # a preprint legitimately has no journal, volume or pages
        add("warning", "missing-recommended", f"@{entry.type} has no {name}")

    for group in ONE_OF_FIELDS.get(entry.type, ()):
        if not any(entry.has(name) for name in group):
            add("error", "missing-field", f'@{entry.type} needs at least one of: {", ".join(group)}')

    for group in ONE_OF_ADVISORY.get(entry.type, ()):
        if not any(entry.has(name) for name in group):
            add("warning", "missing-recommended", f'@{entry.type} has none of: {", ".join(group)}')

    if report.is_preprint:
        add(
            "warning",
            "preprint",
            f"preprint arXiv:{report.arxiv_id} with no journal; run with --verify to check for a published version",
        )

    _check_author(entry, add)
    _check_year(entry, add)
    _check_doi(entry, add)
    _check_url(entry, add)
    _check_title(entry, add)
    _check_placeholders(entry, add)
    return report


def _check_author(entry: Entry, add) -> None:
    author = entry.get("author")
    if not author:
        return
    authors = split_authors(author)
    if any(name.strip().lower() in ("others", "{others}") for name in authors):
        add(
            "warning",
            "truncated-authors",
            'author list is truncated with "and others"; --fix-from-index can expand it',
        )
    if not first_author_surname(author) and not has_placeholder(author):
        add("error", "bad-author", "cannot parse a first-author surname from the author field")

    # An unbraced organisation is read by BibTeX as a person: 'Sonnet Software' becomes
    # the surname 'Software', which then propagates into the citation key.
    single = authors[0].strip() if len(authors) == 1 else ""
    if single and "," not in single and not single.startswith("{"):
        words = single.split()
        if len(words) > 1 and words[-1].lower().strip(".") in ORGANISATION_WORDS:
            add(
                "warning",
                "unbraced-organisation",
                f"author {single!r} looks like an organisation; brace it as {{{{{single}}}}} "
                f"so {words[-1]!r} is not treated as a surname",
            )


def _check_placeholders(entry: Entry, add) -> None:
    """Report editing placeholders such as 'TO VERIFY' or 'TBD' left in any field.

    These are invisible in a rendered bibliography until they are not, so they are
    errors rather than warnings. ``url`` is skipped: query strings legitimately contain
    tokens like 'xxx'.
    """
    for name, value in entry.fields.items():
        if name.lower() == "url" or not has_placeholder(value.text):
            continue
        add("error", "placeholder", f"{name} contains an editing placeholder: {value.text.strip()!r}")


def _check_year(entry: Entry, add) -> None:
    raw = entry.get("year")
    if raw is None:
        return
    if not YEAR_EXACT.match(raw.strip()):
        if entry_year(entry):
            add("warning", "bad-year", f"year field {raw.strip()!r} is not a bare 4-digit year")
        else:
            add("error", "bad-year", f"year field {raw.strip()!r} contains no 4-digit year")


def _check_doi(entry: Entry, add) -> None:
    doi = entry.get("doi")
    if doi and not DOI_SHAPE.match(doi.strip()):
        add("error", "bad-doi", f"doi {doi.strip()!r} does not look like a DOI (expected 10.NNNN/suffix)")


def _check_url(entry: Entry, add) -> None:
    url = entry.get("url")
    doi = entry.get("doi")
    if url and doi and doi.strip() in url:
        add("info", "redundant-url", "url only restates the doi; consider dropping it")


def _check_title(entry: Entry, add) -> None:
    title = entry.get("title")
    if title and not normalize_title(title):
        add("error", "bad-title", "title contains no readable text after LaTeX stripping")


# ------------------------------------------------------------------- file level


def check_non_ascii(db: Database, ascii_only: bool = False) -> list[Finding]:
    """Report non-ASCII characters in the source, which break cp1252 tooling on Windows.

    Zero-width characters are always removed by the parser, and with ``ascii_only`` the
    rest are rewritten as LaTeX escapes, so in both cases the finding records a change
    that was made rather than a problem left behind.
    """
    findings: list[Finding] = []
    for number, text in enumerate(db.source.split("\n"), start=1):
        offenders = sorted({ch for ch in text if ord(ch) > 127})
        if not offenders:
            continue
        zero_width = [ch for ch in offenders if ch in ZERO_WIDTH]
        visible = [ch for ch in offenders if ch not in ZERO_WIDTH]
        if zero_width:
            names = ", ".join(f"U+{ord(ch):04X}" for ch in zero_width)
            findings.append(Finding("info", "zero-width", f"line {number}: removed invisible character(s) ({names})"))
        if visible:
            names = ", ".join(f"U+{ord(ch):04X}" for ch in visible)
            if ascii_only:
                findings.append(
                    Finding("info", "non-ascii", f"line {number}: rewrote non-ASCII character(s) ({names}) as LaTeX")
                )
            else:
                findings.append(
                    Finding(
                        "warning",
                        "non-ascii",
                        f"line {number} contains non-ASCII characters ({names}); use LaTeX escapes or --ascii",
                    )
                )
    return findings


def check_duplicate_keys(entries: list[Entry]) -> list[Finding]:
    """Report citation keys that appear more than once in the source."""
    seen: dict[str, Entry] = {}
    findings: list[Finding] = []
    for entry in entries:
        first = seen.get(entry.key)
        if first is not None:
            findings.append(
                Finding(
                    "error",
                    "duplicate-key",
                    f"key repeats the one on line {first.line}",
                    entry.effective_key,
                    entry.line,
                )
            )
        else:
            seen[entry.key] = entry
    return findings


def check_duplicate_entries(entries: list[Entry]) -> list[Finding]:
    """Report distinct keys that point at the same work, by DOI or by author/year/title."""
    findings: list[Finding] = []
    by_doi: dict[str, Entry] = {}
    by_work: dict[tuple[str, str, str], Entry] = {}
    already_reported: set[int] = set()

    for entry in entries:
        doi = (entry.get("doi") or "").strip().lower()
        if doi:
            first = by_doi.get(doi)
            if first is not None:
                findings.append(
                    Finding(
                        "error",
                        "duplicate-entry",
                        f"same doi {doi} as entry {first.effective_key} (line {first.line})",
                        entry.effective_key,
                        entry.line,
                    )
                )
                already_reported.add(id(entry))
            else:
                by_doi[doi] = entry

        title = normalize_title(entry.get("title") or "")
        signature = (first_author_surname(entry.get("author")).lower(), entry_year(entry), title)
        if not title or not signature[0]:
            continue
        first = by_work.get(signature)
        if first is None:
            by_work[signature] = entry
        elif id(entry) not in already_reported:
            findings.append(
                Finding(
                    "error",
                    "duplicate-entry",
                    f"same author/year/title as entry {first.effective_key} (line {first.line})",
                    entry.effective_key,
                    entry.line,
                )
            )
            already_reported.add(id(entry))

    return findings


def check_database(db: Database, ascii_only: bool = False) -> tuple[list[EntryReport], list[Finding]]:
    """Normalise and check every entry, plus the file-level checks.

    Args:
        db: Parsed database. Entries are normalised in place.

    Returns:
        A ``(entry_reports, file_findings)`` pair. Normalisation findings are merged
        into each entry's report.
    """
    reports: list[EntryReport] = []
    for entry in db.entries:
        normalisation = normalize_entry(entry, ascii_only=ascii_only)
        report = check_entry(entry)
        report.findings = normalisation + report.findings
        reports.append(report)

    file_findings = check_non_ascii(db, ascii_only=ascii_only)
    file_findings += check_duplicate_keys(db.entries)
    file_findings += check_duplicate_entries(db.entries)
    return reports, file_findings


def _normalize_ascii(entry: Entry, note) -> None:
    """Rewrite non-ASCII characters in every field value as LaTeX escapes."""
    for name, value in entry.fields.items():
        if value.text.isascii():
            continue
        converted = latexify(value.text)
        if converted == value.text:
            continue
        entry.fields[name].text = converted
        note("ascii", f"{name}: rewrote non-ASCII characters as LaTeX escapes")
