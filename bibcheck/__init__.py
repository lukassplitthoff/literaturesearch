"""BibTeX checker: unify citation keys, sort, check completeness, verify against public indexes.

See README_BIBCHECK.md for usage. Entry point: ``python -m bibcheck.main INPUT.bib``.
"""

from bibcheck.keys import assign_keys, first_author_surname, fold_latex_accents, make_key
from bibcheck.parser import CommentBlock, Database, Entry, RawValue, dumps, loads, read, write
from bibcheck.rules import Finding, check_database, check_entry, normalize_entry

__all__ = [
    "CommentBlock",
    "Database",
    "Entry",
    "Finding",
    "RawValue",
    "assign_keys",
    "check_database",
    "check_entry",
    "dumps",
    "first_author_surname",
    "fold_latex_accents",
    "loads",
    "make_key",
    "normalize_entry",
    "read",
    "write",
]
