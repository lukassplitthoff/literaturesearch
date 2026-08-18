"""Minimal, byte-faithful BibTeX reader and writer built on the standard library only.

Why not ``bibtexparser``: it is not installed in the ``msmt202q`` environment, and the
poetry resolution for this repo is fragile (see the top-level README). More importantly,
v1 normalises field values, which destroys the LaTeX escapes (``F\\"osel``, ``Lo{\\"i}ck``)
and the brace-protected proper nouns (``{Hamiltonian}``, ``{SNAP}``) that the group's
bibliographies rely on. This parser therefore carries every field value through
*verbatim*: the inner text of a value is never decoded, re-encoded or re-wrapped.

The public surface is deliberately small::

    db = read(path)                 # -> Database
    for entry in db.entries: ...    # -> Entry, with .key, .type, .fields
    write(db, path)                 # canonical layout, values untouched

Round trip fidelity (parse -> write -> parse yields identical field values) is asserted
in ``tests/test_parser.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Entry types that carry metadata rather than a reference. They are preserved verbatim,
# never re-keyed, never sorted, and never checked for completeness. ``@CONTROL`` is the
# JabRef marker found in the group's ``mainNotes.bib`` files.
RAW_ENTRY_TYPES = frozenset({"comment", "preamble", "string", "control"})

# Order fields are emitted in. Anything not listed keeps its original relative order and
# is appended after these. Chosen to read like a printed reference: who, what, where, when.
CANONICAL_FIELD_ORDER = (
    "author",
    "title",
    "journal",
    "booktitle",
    "school",
    "institution",
    "publisher",
    "series",
    "edition",
    "volume",
    "number",
    "pages",
    "month",
    "year",
    "eprint",
    "archivePrefix",
    "primaryClass",
    "doi",
    "url",
    "issn",
    "isbn",
    "note",
)

# A comment block counts as a section banner (rather than a note attached to the next
# entry) when it contains a rule of at least this many consecutive '%' characters.
_BANNER_RULE = re.compile(r"%{20,}")

_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_:.+-]*")
_ENTRY_TYPE = re.compile(r"[A-Za-z]+")

INDENT = "    "

# Invisible characters that arrive with pasted text and must never reach the output:
# BOM / zero-width no-break space, zero-width space, and the zero-width joiners.
ZERO_WIDTH = ("﻿", "​", "‌", "‍", "⁠")


class BibParseError(ValueError):
    """Raised when the input cannot be parsed as BibTeX."""


@dataclass
class RawValue:
    """A field value, stored exactly as it appeared in the source.

    Attributes:
        text: The inner text for ``brace`` and ``quote`` values (delimiters stripped),
            or the literal token for ``bare`` and ``raw`` values.
        delim: One of ``brace``, ``quote``, ``bare`` (an unquoted token such as
            ``2021`` or ``jun``) or ``raw`` (a ``#`` concatenation, kept whole).
    """

    text: str
    delim: str = "brace"

    def rendered(self) -> str:
        """Return the value as it should appear on the right-hand side of ``=``."""
        if self.delim == "brace":
            return "{" + self.text + "}"
        if self.delim == "quote":
            return '"' + self.text + '"'
        return self.text


@dataclass
class CommentBlock:
    """Free text between entries: '%' comment lines and the blank lines around them."""

    text: str
    line: int = 0

    @property
    def is_banner(self) -> bool:
        """True when this block introduces a section rather than annotating one entry."""
        return bool(_BANNER_RULE.search(self.text))


@dataclass
class RawEntry:
    """A ``@comment``/``@preamble``/``@string``/``@CONTROL`` block, preserved verbatim."""

    text: str
    line: int = 0


@dataclass
class Entry:
    """A single reference.

    Attributes:
        type: Entry type, lowercased (``article``, ``software``, ...).
        type_raw: Entry type exactly as written, so ``@Article`` can be reported.
        key: Citation key as written in the source.
        fields: Field name (original case) -> RawValue, in source order.
        line: 1-based line number of the ``@`` character, for reporting.
        lead_comment: A non-banner comment block that immediately preceded this entry.
            It travels with the entry when entries are sorted.
        new_key: Assigned by ``keys.assign_keys``; None until then.
    """

    type: str
    type_raw: str
    key: str
    fields: dict[str, RawValue] = field(default_factory=dict)
    line: int = 0
    lead_comment: CommentBlock | None = None
    new_key: str | None = None

    def get(self, name: str) -> str | None:
        """Return the inner text of a field by case-insensitive name, or None."""
        value = self.get_value(name)
        return None if value is None else value.text

    def get_value(self, name: str) -> RawValue | None:
        """Return the RawValue of a field by case-insensitive name, or None."""
        lowered = name.lower()
        for key, value in self.fields.items():
            if key.lower() == lowered:
                return value
        return None

    def set(self, name: str, text: str, delim: str = "brace") -> None:
        """Set a field, replacing any existing field with the same case-insensitive name."""
        lowered = name.lower()
        for key in list(self.fields):
            if key.lower() == lowered:
                self.fields[key] = RawValue(text, delim)
                return
        self.fields[name] = RawValue(text, delim)

    def pop(self, name: str) -> RawValue | None:
        """Remove and return a field by case-insensitive name, or None if absent."""
        lowered = name.lower()
        for key in list(self.fields):
            if key.lower() == lowered:
                return self.fields.pop(key)
        return None

    def has(self, name: str) -> bool:
        """True when the field exists and its inner text is not blank."""
        text = self.get(name)
        return bool(text and text.strip())

    @property
    def effective_key(self) -> str:
        """The key that will be written out: the new key if assigned, else the original."""
        return self.new_key or self.key


@dataclass
class Section:
    """A banner comment plus the entries that follow it, up to the next banner."""

    banner: CommentBlock | None
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Database:
    """A parsed ``.bib`` file: an ordered list of nodes plus the source path."""

    nodes: list = field(default_factory=list)
    path: Path | None = None
    source: str = ""

    @property
    def entries(self) -> list[Entry]:
        """All reference entries, in source order."""
        return [node for node in self.nodes if isinstance(node, Entry)]

    def sections(self) -> list[Section]:
        """Group entries into banner-delimited sections, preserving order.

        A banner comment block opens a new section. Non-banner comment blocks were
        already attached to the following entry as ``lead_comment`` during parsing,
        so they do not split sections.
        """
        sections: list[Section] = [Section(banner=None)]
        for node in self.nodes:
            if isinstance(node, CommentBlock) and node.is_banner:
                sections.append(Section(banner=node))
            elif isinstance(node, Entry):
                sections[-1].entries.append(node)
        return [sec for sec in sections if sec.banner is not None or sec.entries]

    @property
    def raw_nodes(self) -> list[RawEntry]:
        """``@comment``/``@string``/``@preamble``/``@CONTROL`` blocks, in source order."""
        return [node for node in self.nodes if isinstance(node, RawEntry)]


# --------------------------------------------------------------------------- reading


class _Scanner:
    """Character scanner over the whole file, tracking line numbers for diagnostics."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.n = len(text)

    def line_at(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1

    def eof(self) -> bool:
        return self.pos >= self.n

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < self.n else ""

    def skip_ws(self) -> None:
        while self.pos < self.n and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def match(self, pattern: re.Pattern) -> str | None:
        m = pattern.match(self.text, self.pos)
        if m is None:
            return None
        self.pos = m.end()
        return m.group(0)

    def skip_balanced(self, open_ch: str, close_ch: str) -> int:
        """Consume a balanced ``open_ch`` ... ``close_ch`` run starting at the opener.

        Returns the index just past the closing delimiter. Backslash escapes are
        honoured so ``\\{`` does not affect the depth.
        """
        assert self.text[self.pos] == open_ch
        depth = 0
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch == "\\":
                self.pos += 2
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    self.pos += 1
                    return self.pos
            self.pos += 1
        raise BibParseError(f"unbalanced {open_ch!r} starting at line {self.line_at(self.pos)}")


def _comment_blocks(chunk: str, first_line: int) -> list[CommentBlock]:
    """Split free text into blocks separated by blank lines.

    A run of ``%`` lines with no blank line inside is one block. This keeps a banner
    ('%%%%...' rule plus its caption) separate from a note that happens to sit a blank
    line below it, which is what lets the two be treated differently when sorting.
    """
    blocks: list[CommentBlock] = []
    line = first_line
    for piece in re.split(r"\n[ \t]*\n", chunk):
        stripped = piece.strip("\n")
        if stripped.strip():
            blocks.append(CommentBlock(stripped, line))
        line += piece.count("\n") + 1
    return blocks


def loads(text: str, path: Path | None = None) -> Database:
    """Parse BibTeX source text into a Database.

    The original text is kept in ``Database.source`` so the non-ASCII check can still
    see and report every offending byte, while the text that is actually parsed has
    zero-width characters removed (see ``ZERO_WIDTH``).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    source = text
    # U+FEFF and friends are invisible, carry no meaning, and arrive by the dozen when
    # entries are pasted from a browser. Left in place they survive as one-character
    # "comment blocks" in the output, so they are dropped before parsing. Removing
    # characters without removing newlines keeps every reported line number correct.
    for char in ZERO_WIDTH:
        text = text.replace(char, "")
    scanner = _Scanner(text)
    nodes: list = []
    pending_start = 0

    while not scanner.eof():
        at = text.find("@", scanner.pos)
        if at < 0:
            break
        # Everything since the last node is free text: keep it as comment blocks.
        nodes.extend(_comment_blocks(text[pending_start:at], scanner.line_at(pending_start)))
        scanner.pos = at
        node = _parse_at(scanner)
        nodes.append(node)
        pending_start = scanner.pos

    nodes.extend(_comment_blocks(text[pending_start:], scanner.line_at(pending_start)))

    # Attach non-banner comment blocks to the entry that follows them, so a note about a
    # specific reference stays with that reference when entries are sorted.
    attached: list = []
    for index, node in enumerate(nodes):
        if isinstance(node, CommentBlock) and not node.is_banner:
            following = nodes[index + 1] if index + 1 < len(nodes) else None
            if isinstance(following, Entry) and following.lead_comment is None:
                following.lead_comment = node
                continue
        attached.append(node)

    return Database(nodes=attached, path=path, source=source)


def read(path: str | Path) -> Database:
    """Read and parse a ``.bib`` file.

    UTF-8 is tried first, then cp1252, because some of the group's files were written by
    JabRef on Windows. Undecodable bytes are replaced rather than raising.
    """
    path = Path(path)
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    return loads(text, path=path)


def _parse_at(scanner: _Scanner) -> Entry | RawEntry:
    """Parse one ``@...{...}`` block starting at the ``@``."""
    start = scanner.pos
    line = scanner.line_at(start)
    scanner.pos += 1  # consume '@'
    type_raw = scanner.match(_ENTRY_TYPE)
    if type_raw is None:
        raise BibParseError(f"expected an entry type after @ on line {line}")
    scanner.skip_ws()

    opener = scanner.peek()
    if opener not in "{(":
        raise BibParseError(f"expected {{ or ( after @{type_raw} on line {line}")
    closer = "}" if opener == "{" else ")"

    if type_raw.lower() in RAW_ENTRY_TYPES:
        scanner.skip_balanced(opener, closer)
        return RawEntry(scanner.text[start : scanner.pos], line)

    body_start = scanner.pos
    scanner.skip_balanced(opener, closer)
    body_end = scanner.pos
    inner = _Scanner(scanner.text[body_start + 1 : body_end - 1])
    entry = Entry(type=type_raw.lower(), type_raw=type_raw, key="", line=line)
    _parse_body(inner, entry, line)
    return entry


def _parse_body(scanner: _Scanner, entry: Entry, line: int) -> None:
    """Parse the inside of an entry: the key, then ``name = value`` pairs."""
    scanner.skip_ws()
    key_end = scanner.pos
    while key_end < scanner.n and scanner.text[key_end] != ",":
        key_end += 1
    entry.key = scanner.text[scanner.pos : key_end].strip()
    scanner.pos = min(key_end + 1, scanner.n)

    while True:
        scanner.skip_ws()
        if scanner.eof():
            return
        name = scanner.match(_FIELD_NAME)
        if name is None:
            # A stray token (most often a trailing comma before the closing brace).
            scanner.pos += 1
            continue
        scanner.skip_ws()
        if scanner.peek() != "=":
            raise BibParseError(f"expected = after field {name!r} in entry starting on line {line}")
        scanner.pos += 1
        scanner.skip_ws()
        value = _parse_value(scanner, name, line)
        if name in entry.fields:
            raise BibParseError(f"duplicate field {name!r} in entry {entry.key!r} on line {line}")
        entry.fields[name] = value
        scanner.skip_ws()
        if scanner.peek() == ",":
            scanner.pos += 1


def _parse_value(scanner: _Scanner, name: str, line: int) -> RawValue:
    """Parse a single field value, preserving its inner text exactly."""
    value_start = scanner.pos
    ch = scanner.peek()
    if ch == "{":
        start = scanner.pos
        scanner.skip_balanced("{", "}")
        value = RawValue(scanner.text[start + 1 : scanner.pos - 1], "brace")
    elif ch == '"':
        start = scanner.pos
        scanner.pos += 1
        depth = 0
        while scanner.pos < scanner.n:
            cur = scanner.text[scanner.pos]
            if cur == "\\":
                scanner.pos += 2
                continue
            if cur == "{":
                depth += 1
            elif cur == "}":
                depth -= 1
            elif cur == '"' and depth == 0:
                break
            scanner.pos += 1
        if scanner.pos >= scanner.n:
            raise BibParseError(f"unterminated quoted value for {name!r} on line {line}")
        value = RawValue(scanner.text[start + 1 : scanner.pos], "quote")
        scanner.pos += 1
    else:
        start = scanner.pos
        while scanner.pos < scanner.n and scanner.text[scanner.pos] not in ",}\n":
            scanner.pos += 1
        value = RawValue(scanner.text[start : scanner.pos].strip(), "bare")

    # A '#' concatenation: swallow the remainder and keep the whole expression verbatim,
    # since re-rendering it correctly is not worth the risk of changing its meaning.
    after_first = scanner.pos
    scanner.skip_ws()
    if scanner.peek() == "#":
        while scanner.pos < scanner.n and scanner.text[scanner.pos] != ",":
            cur = scanner.text[scanner.pos]
            if cur == "\\":
                scanner.pos += 2
            elif cur == "{":
                scanner.skip_balanced("{", "}")
            elif cur == '"':
                scanner.pos += 1
                while scanner.pos < scanner.n and scanner.text[scanner.pos] != '"':
                    scanner.pos += 2 if scanner.text[scanner.pos] == "\\" else 1
                scanner.pos += 1
            else:
                scanner.pos += 1
        return RawValue(scanner.text[value_start : scanner.pos].strip(), "raw")
    scanner.pos = after_first
    return value


# --------------------------------------------------------------------------- writing


def _ordered_fields(entry: Entry) -> list[tuple[str, RawValue]]:
    """Return the entry's fields in canonical order, unknown fields appended in source order."""
    lowered = {name.lower(): name for name in entry.fields}
    ordered: list[tuple[str, RawValue]] = []
    used: set[str] = set()
    for canonical in CANONICAL_FIELD_ORDER:
        actual = lowered.get(canonical.lower())
        if actual is not None:
            ordered.append((canonical, entry.fields[actual]))
            used.add(actual)
    for name, value in entry.fields.items():
        if name not in used:
            ordered.append((name, value))
    return ordered


def format_entry(entry: Entry) -> str:
    """Render one entry in the canonical layout."""
    fields = _ordered_fields(entry)
    lines = [f"@{entry.type}{{{entry.effective_key},"]
    width = max((len(name) for name, _ in fields), default=0)
    for name, value in fields:
        lines.append(f"{INDENT}{name.ljust(width)} = {value.rendered()},")
    if len(lines) > 1:
        lines[-1] = lines[-1][:-1]  # no trailing comma on the last field
    lines.append("}")
    return "\n".join(lines)


def dumps(db: Database, sort: str = "sections") -> str:
    """Render a Database back to BibTeX source.

    Args:
        db: The database to render.
        sort: ``sections`` sorts entries by key within each banner-delimited section,
            preserving the banners. ``global`` hoists every banner and raw block to the
            top and emits one flat, key-sorted list. ``none`` keeps source order.
    """
    if sort not in ("sections", "global", "none"):
        raise ValueError(f"unknown sort mode {sort!r}")

    chunks: list[str] = []
    for raw in db.raw_nodes:
        chunks.append(raw.text)

    def render(entry: Entry) -> str:
        body = format_entry(entry)
        if entry.lead_comment is not None:
            return entry.lead_comment.text + "\n" + body
        return body

    def by_key(entry: Entry) -> tuple[str, str]:
        return (entry.effective_key.lower(), entry.effective_key)

    if sort == "none":
        for node in db.nodes:
            if isinstance(node, CommentBlock):
                chunks.append(node.text)
            elif isinstance(node, Entry):
                chunks.append(render(node))
    elif sort == "global":
        for node in db.nodes:
            if isinstance(node, CommentBlock) and node.is_banner:
                chunks.append(node.text)
        for entry in sorted(db.entries, key=by_key):
            chunks.append(render(entry))
    else:
        for section in db.sections():
            if section.banner is not None:
                chunks.append(section.banner.text)
            for entry in sorted(section.entries, key=by_key):
                chunks.append(render(entry))

    return "\n\n".join(chunk.strip("\n") for chunk in chunks if chunk.strip()) + "\n"


def write(db: Database, path: str | Path, sort: str = "sections") -> Path:
    """Write a Database to ``path`` as UTF-8 with Unix line endings."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(db, sort=sort), encoding="utf-8", newline="\n")
    return path
