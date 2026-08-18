"""BibTeX checker: unify citation keys, sort, check completeness, verify against public indexes.

See README_BIBCHECK.md for usage. Entry point: ``python -m lib.utils.bibcheck.main INPUT.bib``.
"""

from lib.utils.bibcheck.keys import assign_keys, first_author_surname, fold_latex_accents, make_key
from lib.utils.bibcheck.parser import CommentBlock, Database, Entry, RawValue, dumps, loads, read, write
from lib.utils.bibcheck.rules import Finding, check_database, check_entry, normalize_entry

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
