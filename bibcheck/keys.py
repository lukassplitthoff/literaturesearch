"""Citation key generation in the ``LastnameYEAR`` convention, plus text normalisation.

The key convention is ``<Surname><Year>`` with no topic slug: ``Cahill1969``,
``Acharya2025``, ``Ansys2021``. When several entries produce the same base key, *every*
member of the colliding group gets a letter suffix (``Wang2019a``, ``Wang2019b``) so a
bare key never sits next to a suffixed one.

Surnames are folded to ASCII, because keys must be typeable and because everything this
package prints has to survive cp1252 (see CLAUDE.md). ``Lo{\\"i}ck Le Guevel`` becomes
``LeGuevel``, ``F\\"osel`` becomes ``Fosel``, ``Mandr{\\`a}`` becomes ``Mandra``.
"""

from __future__ import annotations

import re
import string
import unicodedata

from bibcheck.parser import Entry

# Single-token LaTeX commands that stand for a letter rather than modify one.
_LATEX_LIGATURES = {
    r"\ss": "ss",
    r"\AA": "A",
    r"\aa": "a",
    r"\AE": "AE",
    r"\ae": "ae",
    r"\OE": "OE",
    r"\oe": "oe",
    r"\O": "O",
    r"\o": "o",
    r"\L": "L",
    r"\l": "l",
    r"\i": "i",
    r"\j": "j",
}

# Accent commands whose argument is the letter to keep: \"o, \'{E}, \v{s}, \c{c}, ...
_ACCENT_SYMBOL = re.compile(r'\\[\'`"^~=.]\s*\{?\s*([A-Za-z])\s*\}?')
_ACCENT_LETTER = re.compile(r"\\(?:u|v|H|c|k|r|b|d|t)\s*\{\s*([A-Za-z])\s*\}")
_REMAINING_COMMAND = re.compile(r"\\[A-Za-z]+\s*")

# Longest first, and never match a prefix of a longer command (\l must not eat \ldots).
# The alternation must be grouped: without the (?:...) the lookahead would bind to the
# last branch only, leaving every other command unguarded.
_LIGATURE_RE = re.compile(
    "(?:"
    + "|".join(re.escape(command) for command in sorted(_LATEX_LIGATURES, key=len, reverse=True))
    + r")(?![A-Za-z])"
)

# Name particles that belong to the surname when it is given in "First von Last" order.
_PARTICLES = frozenset(
    {"van", "von", "de", "del", "della", "der", "den", "di", "da", "du", "la", "le", "ten", "ter", "dos", "af", "zu"}
)

# Editing placeholders that must never survive into a submitted bibliography. A field
# containing one of these is unusable, so it must not be used to build a key either.
PLACEHOLDER_MARKERS = (
    "to verify",
    "to be verified",
    "not confirmed",
    "to be confirmed",
    "tbd",
    "todo",
    "fixme",
    "placeholder",
    "fill in",
    "xxx",
    "???",
)


def has_placeholder(text: str | None) -> bool:
    """True when the text contains an editing placeholder such as 'TO VERIFY' or 'TBD'."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


_YEAR = re.compile(r"\b((?:1[6-9]|2[0-9])\d{2})\b")

# The BibTeX author separator. Whitespace rather than a literal ' and ', because author
# lists are routinely hand-wrapped across lines in the group's .bib files.
_AND_SEPARATOR = re.compile(r"\s+and\s+")


# Letters with no Unicode decomposition, which the NFKD pass therefore cannot reduce.
# Without these they would be dropped outright by the ascii-ignore step, so a name
# spelled with a slashed o would come back missing that letter entirely.
_UNICODE_FALLBACK = {
    "ø": "o",
    "Ø": "O",
    "ß": "ss",
    "æ": "ae",
    "Æ": "AE",
    "œ": "oe",
    "Œ": "OE",
    "ł": "l",
    "Ł": "L",
    "đ": "d",
    "Đ": "D",
    "ð": "d",
    "þ": "th",
    "ı": "i",
    "ȷ": "j",
}


def strip_latex(text: str) -> str:
    """Reduce LaTeX-escaped text to plain ASCII letters, digits, spaces and punctuation.

    Applied to Unicode input as well, so a file saved with a literal 'o with umlaut'
    folds the same way as one using ``\\"o``.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("{}", "")
    text = _LIGATURE_RE.sub(lambda m: _LATEX_LIGATURES[m.group(0)], text)
    text = _ACCENT_LETTER.sub(r"\1", text)
    text = _ACCENT_SYMBOL.sub(r"\1", text)
    text = _REMAINING_COMMAND.sub(" ", text)
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    for char, replacement in _UNICODE_FALLBACK.items():
        text = text.replace(char, replacement)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


# Letters that have no base-letter-plus-accent decomposition and need a named command.
_STANDALONE_LATEX = {
    "ß": r"{\ss}",
    "æ": r"{\ae}",
    "Æ": r"{\AE}",
    "œ": r"{\oe}",
    "Œ": r"{\OE}",
    "ø": r"{\o}",
    "Ø": r"{\O}",
    "å": r"{\aa}",
    "Å": r"{\AA}",
    "ł": r"{\l}",
    "Ł": r"{\L}",
    "đ": r"{\dj}",
    "Đ": r"{\DJ}",
    "ð": r"{\dh}",
    "þ": r"{\th}",
    "ı": r"{\i}",
    "ȷ": r"{\j}",
}

# Combining marks -> the LaTeX accent command that produces them.
_COMBINING_LATEX = {
    "́": "'",
    "̀": "`",
    "̈": '"',
    "̂": "^",
    "̃": "~",
    "̄": "=",
    "̇": ".",
    "̆": "u",
    "̌": "v",
    "̊": "r",
    "̋": "H",
    "̧": "c",
    "̨": "k",
    "̣": "d",
    "̱": "b",
}

# Typographic characters that have a plain TeX spelling.
_PUNCTUATION_LATEX = {
    "–": "--",
    "—": "---",
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "…": r"\ldots{}",
    " ": "~",
    " ": " ",
    "−": "-",
    "×": r"$\times$",
    "°": r"$^\circ$",
    "±": r"$\pm$",
    "­": "",
}


def latexify(text: str) -> str:
    """Rewrite non-ASCII characters as LaTeX escapes, leaving ASCII untouched.

    ``Theo Sepulcre`` spelled with accents becomes ``Th{\\'e}o S{\\'e}pulcre``; an en
    dash becomes ``--``. Characters with no known spelling are left in place, so the
    non-ASCII check still reports anything this cannot handle rather than silently
    dropping it.
    """
    if text.isascii():
        return text
    out: list[str] = []
    for char in text:
        if char.isascii():
            out.append(char)
            continue
        if char in _PUNCTUATION_LATEX:
            out.append(_PUNCTUATION_LATEX[char])
            continue
        if char in _STANDALONE_LATEX:
            out.append(_STANDALONE_LATEX[char])
            continue
        decomposed = unicodedata.normalize("NFD", char)
        base = decomposed[0]
        marks = [_COMBINING_LATEX.get(mark) for mark in decomposed[1:]]
        if base.isascii() and base.isalpha() and marks and all(marks):
            rendered = base
            for command in reversed(marks):
                # A letter command must brace its argument: '\vS' reads as the undefined
                # control sequence \vS, whereas the symbol commands may abut ("\'e").
                if command.isalpha() or len(rendered) > 1:
                    rendered = "{\\%s{%s}}" % (command, rendered)
                else:
                    rendered = "{\\%s%s}" % (command, rendered)
            out.append(rendered)
            continue
        out.append(char)
    return "".join(out)


def fold_latex_accents(text: str) -> str:
    """Return ``text`` as ASCII letters only, suitable for use inside a citation key."""
    return re.sub(r"[^A-Za-z]", "", strip_latex(text))


def normalize_title(text: str) -> str:
    """Normalise a title for comparison: ASCII, lowercase, alphanumerics and single spaces."""
    plain = strip_latex(text).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", plain)).strip()


def split_authors(author_field: str) -> list[str]:
    """Split a BibTeX ``author`` value on ' and ' at brace depth zero."""
    parts: list[str] = []
    depth = 0
    token = []
    index = 0
    text = author_field
    while index < len(text):
        ch = text[index]
        if ch == "\\":
            token.append(text[index : index + 2])
            index += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        separator = _AND_SEPARATOR.match(text, index) if depth == 0 else None
        if separator is not None:
            parts.append("".join(token).strip())
            token = []
            index = separator.end()
            continue
        token.append(ch)
        index += 1
    tail = "".join(token).strip()
    if tail:
        parts.append(tail)
    return [part for part in parts if part]


def first_author_surname(author_field: str | None) -> str:
    """Extract the first author's surname from a BibTeX ``author`` value.

    Handles ``Last, First``, ``First Last``, ``First von Last``, and corporate authors
    written as a single braced group (``{Ansys}``, ``{Sonnet Software}``, ``{CSC}``),
    for which the first word is used. Returns '' when nothing usable is present.
    """
    if not author_field or not author_field.strip() or has_placeholder(author_field):
        return ""
    authors = split_authors(author_field)
    if not authors:
        return ""
    name = authors[0].strip()
    if name.lower() == "others":
        return ""

    # Corporate author: the whole name is one braced group, e.g. {Sonnet Software}.
    if name.startswith("{") and name.endswith("}") and name.count("{") == 1:
        inner = strip_latex(name[1:-1]).strip()
        return inner.split()[0] if inner else ""

    if "," in name:
        surname = name.split(",", 1)[0]
    else:
        tokens = strip_latex(name).split()
        if not tokens:
            return ""
        surname_tokens = [tokens[-1]]
        index = len(tokens) - 2
        while index >= 1 and tokens[index].lower() in _PARTICLES:
            surname_tokens.insert(0, tokens[index])
            index -= 1
        surname = " ".join(surname_tokens)
    return fold_latex_accents(surname)


def entry_year(entry: Entry) -> str:
    """Return the entry's 4-digit year, or '' when it is missing or unparseable."""
    for name in ("year", "date"):
        raw = entry.get(name)
        if raw:
            match = _YEAR.search(raw)
            if match:
                return match.group(1)
    return ""


def make_key(entry: Entry) -> tuple[str, str]:
    """Build the base ``LastnameYEAR`` key for an entry.

    Returns:
        A ``(key, problem)`` pair. ``problem`` is '' on success, otherwise an ASCII
        description of why no key could be built, in which case ``key`` is ''.
    """
    surname = first_author_surname(entry.get("author"))
    if not surname:
        return "", "cannot determine a first-author surname from the author field"
    year = entry_year(entry)
    if not year:
        return "", "no 4-digit year found in the year field"
    return surname[0].upper() + surname[1:] + year, ""


def _suffix(index: int) -> str:
    """Return 'a'..'z', then 'aa', 'ab', ... for groups larger than 26."""
    letters = string.ascii_lowercase
    if index < len(letters):
        return letters[index]
    return letters[index // len(letters) - 1] + letters[index % len(letters)]


def _tiebreak(entry: Entry) -> tuple[str, str, str]:
    """Deterministic ordering within a colliding group, independent of source order."""
    return (normalize_title(entry.get("title") or ""), (entry.get("doi") or "").lower(), entry.key)


def assign_keys(entries: list[Entry]) -> tuple[dict[str, str], list[tuple[Entry, str]]]:
    """Assign ``new_key`` to every entry that can get one, disambiguating collisions.

    Entries for which no key can be built keep their original key and are returned as
    problems, so the caller can report them rather than silently mangling the entry.

    Args:
        entries: Entries to key, mutated in place via ``Entry.new_key``.

    Returns:
        A ``(rename_map, problems)`` pair. ``rename_map`` maps old key -> new key and
        omits unchanged keys. ``problems`` is a list of ``(entry, reason)``.
    """
    problems: list[tuple[Entry, str]] = []
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        base, problem = make_key(entry)
        if problem:
            entry.new_key = entry.key
            problems.append((entry, problem))
            continue
        groups.setdefault(base, []).append(entry)

    for base, members in groups.items():
        if len(members) == 1:
            members[0].new_key = base
            continue
        for index, entry in enumerate(sorted(members, key=_tiebreak)):
            entry.new_key = base + _suffix(index)

    rename_map = {entry.key: entry.new_key for entry in entries if entry.new_key and entry.new_key != entry.key}
    return rename_map, problems
