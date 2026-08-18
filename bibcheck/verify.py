"""Verify entries against the public metadata indexes: Crossref, arXiv and OpenAlex.

These are the same indexes the browser-based reference checkers use underneath -- CiteMe
lists "OpenAlex, CrossRef, Semantic Scholar, PubMed" and Citely lists "CrossRef, PubMed,
arXiv & OpenAlex" -- but neither exposes a public API, so this module queries the indexes
directly. That is keyless, unmetered, cacheable and unit-testable, and it produces the
same field-level verdicts.

Lookup is tiered, cheapest and most authoritative first:

1. ``doi`` present            -> Crossref ``/works/{doi}``, the publisher's own record.
2. arXiv id present           -> the arXiv Atom API, which also reports the published
                                 DOI once a preprint has appeared in a journal.
3. neither                    -> OpenAlex title search, confirmed against Crossref.

Every response is cached on disk, so a second run is free and ``--offline`` works.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import requests

from lib.utils.bibcheck.keys import entry_year, first_author_surname, normalize_title, strip_latex
from lib.utils.bibcheck.rules import EntryReport, Finding

CROSSREF_WORK = "https://api.crossref.org/works/{doi}"
CROSSREF_SEARCH = "https://api.crossref.org/works"
ARXIV_QUERY = "http://export.arxiv.org/api/query"
OPENALEX_SEARCH = "https://api.openalex.org/works"
DATACITE_WORK = "https://api.datacite.org/dois/{doi}"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Minimum seconds between outbound requests. Crossref's polite pool and the arXiv API
# both ask for roughly this; it also keeps a 100-entry file to under two minutes.
MIN_INTERVAL_S = 1.0
MAX_RETRIES = 3
TIMEOUT_S = 20

# Titles rarely match character for character (LaTeX braces, hyphenation, subtitles), so
# accept a high similarity rather than exact equality.
TITLE_MATCH_RATIO = 0.92

VERDICT_VERIFIED = "verified"
VERDICT_MISMATCHED = "mismatched"
VERDICT_NOT_FOUND = "not_found"
VERDICT_SKIPPED = "skipped"

_PAGE_SEP = re.compile(r"\s*(?:--|-|–|—)\s*")

# arXiv mints a DataCite DOI for every preprint. Crossref and OpenAlex index those, so a
# title search for a preprint reliably finds one. It is NOT evidence of journal
# publication, and must not be reported as such.
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.", re.IGNORECASE)

# DOI prefixes registered with DataCite rather than Crossref.
REPOSITORY_DOI_PREFIXES = frozenset({"10.5281", "10.48550", "10.3929", "10.5061", "10.17632", "10.15468"})


# Author entries that name a group rather than a person.
_COLLABORATION = re.compile(r"collaborat|consortium|\bteam\b|\bgroup\b|quantum ai", re.IGNORECASE)


def is_arxiv_doi(doi: str | None) -> bool:
    """True for arXiv's own preprint DOI (10.48550/arXiv.NNNN), which is not a journal DOI."""
    return bool(doi and ARXIV_DOI.match(doi.strip()))


def is_repository_doi(doi: str | None) -> bool:
    """True for a DOI registered with DataCite rather than Crossref.

    Zenodo (10.5281), arXiv (10.48550) and institutional repositories such as ETH
    Zurich (10.3929) are not in the Crossref index, so failing to find one there is
    expected rather than evidence that the DOI is wrong.
    """
    return bool(doi and doi.strip().split("/", 1)[0] in REPOSITORY_DOI_PREFIXES)


@dataclass
class Record:
    """A normalised metadata record from any of the indexes."""

    source: str
    doi: str | None = None
    title: str = ""
    authors: list[str] = field(default_factory=list)
    first_surname: str = ""
    year: str = ""
    journal: str = ""
    volume: str = ""
    pages: str = ""
    url: str = ""
    published_doi: str | None = None
    published_journal: str = ""

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "doi": self.doi,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "journal": self.journal,
            "volume": self.volume,
            "pages": self.pages,
            "published_doi": self.published_doi,
        }

    def surnames(self) -> list[str]:
        """Every author surname in the record, lowercased.

        Used instead of ``first_surname`` alone when checking whether the entry's first
        author appears at all: publishers routinely deposit a collaboration name
        ("Google Quantum AI and Collaborators") ahead of the named authors, which would
        otherwise make every large-collaboration paper look like a mismatch.
        """
        return [surname for surname in (first_author_surname(name).lower() for name in self.authors) if surname]

    def has_author(self, surname: str) -> bool:
        """True when ``surname`` matches any author in the record."""
        return bool(surname) and surname.lower() in self.surnames()


@dataclass
class FieldComparison:
    """Result of comparing one field between the entry and the index record."""

    field: str
    local: str
    remote: str
    status: str  # 'match', 'mismatch', 'missing-local', 'missing-remote'


@dataclass
class Verification:
    """The outcome of verifying one entry."""

    key: str
    verdict: str
    source: str = ""
    record: Record | None = None
    comparisons: list[FieldComparison] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    suggestions: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    @property
    def mismatched_fields(self) -> list[str]:
        return [comparison.field for comparison in self.comparisons if comparison.status == "mismatch"]


# ------------------------------------------------------------------------ client


class IndexClient:
    """Throttled, cached HTTP client for Crossref, arXiv and OpenAlex."""

    def __init__(self, cache_path: Path | None = None, mailto: str = "", offline: bool = False) -> None:
        """Create a client.

        Args:
            cache_path: JSON file used to memoise lookups across runs.
            mailto: Contact address. Crossref and OpenAlex give faster, more reliable
                service to requests that identify themselves ("polite pool").
            offline: Serve from cache only; never open a connection.
        """
        self.cache_path = Path(cache_path) if cache_path else None
        self.mailto = mailto
        self.offline = offline
        self.cache: dict[str, dict | None] = {}
        self.network_errors: list[str] = []
        self._last_request = 0.0
        self._session = requests.Session()
        agent = "bibcheck/1.0 (https://bitbucket.org/qtlteam/qt-codebase)"
        if mailto:
            agent += f" mailto:{mailto}"
        self._session.headers["User-Agent"] = agent
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.cache = {}

    def save_cache(self) -> None:
        """Persist the cache. Failures are ignored: the cache is an optimisation."""
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            pass

    def _get(self, cache_key: str, url: str, params: dict | None = None, as_text: bool = False):
        """Fetch a URL with throttling, retries and caching. Returns None on failure."""
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            return None if cached is None else cached.get("payload")
        if self.offline:
            return None

        payload = None
        for attempt in range(MAX_RETRIES):
            delay = MIN_INTERVAL_S - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            try:
                response = self._session.get(url, params=params, timeout=TIMEOUT_S)
            except requests.RequestException as exc:
                self.network_errors.append(f"{url}: {type(exc).__name__}")
                self._last_request = time.monotonic()
                time.sleep(2**attempt)
                continue
            self._last_request = time.monotonic()
            if response.status_code == 404:
                break
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** (attempt + 1))
                continue
            if not response.ok:
                self.network_errors.append(f"{url}: HTTP {response.status_code}")
                break
            payload = response.text if as_text else response.json()
            break

        self.cache[cache_key] = None if payload is None else {"payload": payload}
        return payload

    # ------------------------------------------------------------- per index

    def crossref_by_doi(self, doi: str) -> Record | None:
        """Look up a DOI in Crossref."""
        quoted = urllib.parse.quote(doi.strip(), safe="")
        payload = self._get(f"crossref:doi:{doi.strip().lower()}", CROSSREF_WORK.format(doi=quoted))
        if not payload:
            return None
        return _record_from_crossref(payload.get("message", {}))

    def crossref_search(self, title: str, surname: str, year: str) -> Record | None:
        """Search Crossref by bibliographic string and return the best title match."""
        query = " ".join(part for part in (title, surname, year) if part)
        params = {"query.bibliographic": query, "rows": 5}
        if self.mailto:
            params["mailto"] = self.mailto
        payload = self._get(f"crossref:search:{normalize_title(query)}", CROSSREF_SEARCH, params=params)
        if not payload:
            return None
        items = payload.get("message", {}).get("items", [])
        return _best_title_match([_record_from_crossref(item) for item in items], title)

    def arxiv_by_id(self, arxiv_id: str) -> Record | None:
        """Look up an arXiv id, including whether it now has a published DOI."""
        params = {"id_list": arxiv_id.strip(), "max_results": 1}
        text = self._get(f"arxiv:{arxiv_id.strip()}", ARXIV_QUERY, params=params, as_text=True)
        if not text:
            return None
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None
        node = root.find("atom:entry", ATOM_NS)
        if node is None:
            return None
        return _record_from_arxiv(node)

    def openalex_search(self, title: str) -> Record | None:
        """Search OpenAlex by title and return the best match."""
        normalized = normalize_title(title)
        if not normalized:
            return None
        params = {"filter": f"title.search:{normalized}", "per_page": 5}
        if self.mailto:
            params["mailto"] = self.mailto
        payload = self._get(f"openalex:search:{normalized}", OPENALEX_SEARCH, params=params)
        if not payload:
            return None
        return _best_title_match([_record_from_openalex(item) for item in payload.get("results", [])], title)

    def datacite_by_doi(self, doi: str) -> Record | None:
        """Look up a DOI in DataCite.

        Zenodo (10.5281), arXiv (10.48550) and university repositories (e.g. ETH's
        10.3929) register with DataCite, not Crossref. Software, datasets and theses in
        a bibliography therefore resolve here and nowhere else.
        """
        quoted = urllib.parse.quote(doi.strip(), safe="")
        payload = self._get(f"datacite:doi:{doi.strip().lower()}", DATACITE_WORK.format(doi=quoted))
        if not payload:
            return None
        attributes = (payload.get("data") or {}).get("attributes") or {}
        return _record_from_datacite(attributes)


# ----------------------------------------------------------------- normalisers


def _joined(value) -> str:
    """Crossref returns several fields as one-element lists; flatten them to a string."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _strip_markup(text: str) -> str:
    """Remove XML/HTML markup from an index title.

    Crossref deposits maths as inline MathML, so a PRL title comes back containing
    ``<mml:math ...><mml:mi>Z</mml:mi></mml:math>``. Comparing that to the entry's
    ``$Z$`` would report a mismatch on every paper with a symbol in its title.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _representative_surname(authors: list[str]) -> str:
    """First author surname, skipping a leading collaboration or group name.

    Publishers deposit entries like 'Google Quantum AI and Collaborators' ahead of the
    named authors; that is not a person and must not be treated as the first author.
    """
    for name in authors:
        surname = first_author_surname(name)
        if surname and not _COLLABORATION.search(name):
            return surname
    return first_author_surname(authors[0]) if authors else ""


def _record_from_crossref(message: dict) -> Record:
    authors = []
    for person in message.get("author") or []:
        family = person.get("family") or person.get("name") or ""
        given = person.get("given") or ""
        authors.append(f"{family}, {given}".strip().strip(","))
    issued = (message.get("issued") or {}).get("date-parts") or [[]]
    year = str(issued[0][0]) if issued and issued[0] else ""
    return Record(
        source="crossref",
        doi=message.get("DOI"),
        title=_strip_markup(_joined(message.get("title"))),
        authors=authors,
        first_surname=_representative_surname(authors),
        year=year,
        journal=_joined(message.get("container-title")),
        volume=str(message.get("volume") or ""),
        pages=str(message.get("page") or ""),
        url=message.get("URL") or "",
    )


def _record_from_datacite(attributes: dict) -> Record:
    authors = []
    for creator in attributes.get("creators") or []:
        family = creator.get("familyName") or creator.get("name") or ""
        given = creator.get("givenName") or ""
        authors.append(f"{family}, {given}".strip().strip(","))
    titles = attributes.get("titles") or []
    container = attributes.get("container") or {}
    return Record(
        source="datacite",
        doi=attributes.get("doi"),
        title=_strip_markup(str(titles[0].get("title", "")) if titles else ""),
        authors=authors,
        first_surname=_representative_surname(authors),
        year=str(attributes.get("publicationYear") or ""),
        journal=str(container.get("title") or attributes.get("publisher") or ""),
        volume=str(attributes.get("volume") or ""),
        pages=str(attributes.get("firstPage") or ""),
        url=attributes.get("url") or "",
    )


def _record_from_arxiv(node: ET.Element) -> Record:
    def text_of(path: str) -> str:
        found = node.find(path, ATOM_NS)
        return (found.text or "").strip() if found is not None and found.text else ""

    authors = []
    for person in node.findall("atom:author/atom:name", ATOM_NS):
        full = (person.text or "").strip()
        if not full:
            continue
        parts = full.split()
        authors.append(f'{parts[-1]}, {" ".join(parts[:-1])}'.strip().strip(","))
    published = text_of("atom:published")
    doi = text_of("arxiv:doi")
    journal_ref = text_of("arxiv:journal_ref")
    return Record(
        source="arxiv",
        doi=None,
        title=re.sub(r"\s+", " ", text_of("atom:title")),
        authors=authors,
        first_surname=first_author_surname(authors[0]) if authors else "",
        year=published[:4],
        journal="",
        url=text_of("atom:id"),
        published_doi=doi or None,
        published_journal=journal_ref,
    )


def _record_from_openalex(item: dict) -> Record:
    authors = []
    for authorship in item.get("authorships") or []:
        name = (authorship.get("author") or {}).get("display_name") or ""
        if not name:
            continue
        parts = name.split()
        authors.append(f'{parts[-1]}, {" ".join(parts[:-1])}'.strip().strip(","))
    doi = item.get("doi") or ""
    biblio = item.get("biblio") or {}
    first_page, last_page = biblio.get("first_page") or "", biblio.get("last_page") or ""
    pages = f"{first_page}-{last_page}" if first_page and last_page else first_page
    source = ((item.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    return Record(
        source="openalex",
        doi=doi.replace("https://doi.org/", "") or None,
        title=_strip_markup(item.get("display_name") or item.get("title") or ""),
        authors=authors,
        first_surname=_representative_surname(authors),
        year=str(item.get("publication_year") or ""),
        journal=source,
        volume=str(biblio.get("volume") or ""),
        pages=pages,
        url=item.get("id") or "",
    )


def _title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()


def _best_title_match(records: list[Record], title: str) -> Record | None:
    best, best_ratio = None, 0.0
    for record in records:
        ratio = _title_similarity(record.title, title)
        if ratio > best_ratio:
            best, best_ratio = record, ratio
    return best if best_ratio >= TITLE_MATCH_RATIO else None


# ------------------------------------------------------------------ comparison


def _same_pages(local: str, remote: str) -> bool:
    return _PAGE_SEP.sub("-", local.strip()) == _PAGE_SEP.sub("-", remote.strip())


def _same_journal(local: str, remote: str) -> bool:
    """Compare journal names tolerantly, since 'Phys. Rev. Lett.' abbreviates the full name."""
    left, right = normalize_title(local), normalize_title(remote)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    # Compare initials: 'phys rev lett' vs 'physical review letters' -> 'prl' both ways.
    initials = lambda text: "".join(word[0] for word in text.split())  # noqa: E731
    return initials(left) == initials(right)


def _compare(entry_report: EntryReport, record: Record) -> list[FieldComparison]:
    """Compare the fields an entry and a record have in common."""
    entry = entry_report.entry
    comparisons: list[FieldComparison] = []

    def add(name: str, local: str, remote: str, same: bool) -> None:
        if not local.strip() and not remote.strip():
            return
        if not local.strip():
            status = "missing-local"
        elif not remote.strip():
            status = "missing-remote"
        else:
            status = "match" if same else "mismatch"
        comparisons.append(FieldComparison(name, local.strip(), remote.strip(), status))

    local_title = entry.get("title") or ""
    add(
        "title",
        strip_latex(local_title),
        record.title,
        _title_similarity(local_title, record.title) >= TITLE_MATCH_RATIO,
    )

    # Match against the whole author list, not just the first name: publishers deposit
    # collaboration names and reorder group authorships, and a paper is still the same
    # paper if its first author appears third in the index record.
    local_surname = first_author_surname(entry.get("author"))
    add("author", local_surname, record.first_surname, record.has_author(local_surname))

    local_year = entry_year(entry)
    same_year = local_year == record.year or (
        local_year.isdigit() and record.year.isdigit() and abs(int(local_year) - int(record.year)) <= 1
    )
    add("year", local_year, record.year, same_year)

    add(
        "journal",
        entry.get("journal") or "",
        record.journal,
        _same_journal(entry.get("journal") or "", record.journal),
    )
    add(
        "volume",
        entry.get("volume") or "",
        record.volume,
        (entry.get("volume") or "").strip() == record.volume.strip(),
    )
    add("pages", entry.get("pages") or "", record.pages, _same_pages(entry.get("pages") or "", record.pages))
    if record.doi:
        local_doi = (entry.get("doi") or "").strip()
        add("doi", local_doi, record.doi, local_doi.lower() == record.doi.lower())
    return comparisons


def _suggestions(entry_report: EntryReport, record: Record) -> dict[str, str]:
    """Fields the index can fill in that the entry is missing or has truncated."""
    entry = entry_report.entry
    out: dict[str, str] = {}
    # A repository record's "journal" is the repository itself ("arXiv (Cornell
    # University)", "Zenodo"), and software has no journal at all. Neither may be
    # proposed as a journal name.
    journal = record.journal
    if (
        is_arxiv_doi(record.doi)
        or record.source == "datacite"
        or entry.type in ("software", "dataset", "misc")
        or "arxiv" in normalize_title(record.journal)
    ):
        journal = ""
    for name, value in (
        ("journal", journal),
        ("volume", record.volume),
        ("pages", _PAGE_SEP.sub("--", record.pages.strip()) if record.pages else ""),
        ("doi", record.doi or ""),
        ("year", record.year),
    ):
        if value and not entry.has(name):
            out[name] = value
    local_authors = entry.get("author") or ""
    if "others" in local_authors.lower() and len(record.authors) > 1:
        out["author"] = " and ".join(record.authors)
    return out


def _is_repository_release(entry, record: Record) -> bool:
    """True for a software/dataset entry matched against a DataCite repository record."""
    return record.source == "datacite" and entry.type in ("software", "dataset", "misc")


# -------------------------------------------------------------------- driver


def verify_entry(entry_report: EntryReport, client: IndexClient) -> Verification:
    """Verify one entry against the indexes, tiered by what identifiers it carries."""
    entry = entry_report.entry
    key = entry.effective_key
    findings: list[Finding] = []

    def add(level: str, code: str, message: str) -> None:
        findings.append(Finding(level, code, message, key, entry.line))

    title = entry.get("title") or ""
    record: Record | None = None

    # Tier 2 first for preprints: the arXiv record tells us whether it has been published.
    if entry_report.arxiv_id:
        arxiv = client.arxiv_by_id(entry_report.arxiv_id)
        if arxiv is not None:
            if arxiv.published_doi and not entry.has("doi"):
                detail = f" ({arxiv.published_journal})" if arxiv.published_journal else ""
                add(
                    "warning",
                    "preprint-published",
                    f"arXiv:{entry_report.arxiv_id} is now published as doi {arxiv.published_doi}{detail}; upgrade the entry",
                )
            record = arxiv

    local_surname = first_author_surname(entry.get("author"))

    doi = entry_report.doi or (record.published_doi if record else None)
    if doi:
        resolved = client.crossref_by_doi(doi)
        if resolved is None:
            # Zenodo, arXiv and repository DOIs are registered with DataCite, not
            # Crossref; a Crossref miss alone says nothing about their validity.
            resolved = client.datacite_by_doi(doi)
        if resolved is not None:
            record = resolved
        elif entry_report.doi:
            add(
                "error" if not is_repository_doi(entry_report.doi) else "warning",
                "doi-unresolved",
                f"doi {entry_report.doi} resolved in neither Crossref nor DataCite",
            )

    if record is None or record.source == "arxiv":
        if title:
            fallback = client.crossref_search(title, local_surname, entry_year(entry))
            if fallback is None:
                fallback = client.openalex_search(title)
            # A title-only match that names a different first author is a different
            # paper, however similar the titles look. Discard it rather than report
            # every one of its fields as a mismatch.
            if fallback is not None and local_surname and fallback.authors and not fallback.has_author(local_surname):
                fallback = None
            if fallback is not None and record is None:
                record = fallback
            elif fallback is not None and record is not None and fallback.doi and not is_arxiv_doi(fallback.doi):
                add(
                    "warning",
                    "preprint-published",
                    f"a published record with doi {fallback.doi} matches this preprint title; upgrade the entry",
                )

    if record is None:
        reason = "no matching record in Crossref, arXiv or OpenAlex"
        add("warning", "not-found", reason)
        return Verification(key=key, verdict=VERDICT_NOT_FOUND, findings=findings, reason=reason)

    if _is_repository_release(entry, record):
        # A Zenodo software record titles itself after the GitHub release
        # ("qiskit-community/qiskit-metal: v0.7.0 - ..."), lists the contributor roster
        # as authors, and -- for a concept DOI -- describes the newest release rather
        # than the one cited. None of that can be compared against a hand-written
        # software entry; that the DOI resolves at all is the whole of the check.
        add(
            "info",
            "repository-release",
            f"doi confirmed in DataCite; title/author/year not compared "
            f"(repository records describe the {record.year or 'latest'} release)",
        )
        return Verification(
            key=key,
            verdict=VERDICT_VERIFIED,
            source=record.source,
            record=record,
            findings=findings,
            suggestions=_suggestions(entry_report, record),
        )

    comparisons = _compare(entry_report, record)
    mismatched = [comparison for comparison in comparisons if comparison.status == "mismatch"]
    verdict = VERDICT_MISMATCHED if mismatched else VERDICT_VERIFIED
    for comparison in mismatched:
        add(
            "error" if comparison.field in ("title", "author", "doi") else "warning",
            "field-mismatch",
            f"{comparison.field}: local {comparison.local!r} != {record.source} {comparison.remote!r}",
        )

    return Verification(
        key=key,
        verdict=verdict,
        source=record.source,
        record=record,
        comparisons=comparisons,
        findings=findings,
        suggestions=_suggestions(entry_report, record),
    )


def verify_all(entry_reports: list[EntryReport], client: IndexClient) -> list[Verification]:
    """Verify every entry, saving the cache once at the end."""
    results = [verify_entry(report, client) for report in entry_reports]
    client.save_cache()
    return results


def apply_suggestions(entry_report: EntryReport, verification: Verification) -> list[Finding]:
    """Fill empty fields from the index record.

    The only value that is ever replaced rather than added is a truncated ``author``
    list ending in ``and others``, and that replacement is reported like any other change.
    """
    entry = entry_report.entry
    findings: list[Finding] = []
    for name, value in verification.suggestions.items():
        if name == "author":
            entry.set("author", value)
            findings.append(
                Finding(
                    "info",
                    "fixed-from-index",
                    "expanded truncated author list from the index",
                    entry.effective_key,
                    entry.line,
                )
            )
            continue
        if entry.has(name):
            continue
        entry.set(name, value)
        findings.append(
            Finding(
                "info",
                "fixed-from-index",
                f"filled {name} = {{{value}}} from {verification.source}",
                entry.effective_key,
                entry.line,
            )
        )
    return findings
