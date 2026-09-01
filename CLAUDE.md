# literaturesearch -- house rules

## Environment

Python `>=3.10`. Runtime dependency: `requests`. Dev: `pytest`, `pre-commit`.

```bash
pip install -e ".[dev]"
python -m pytest
```

On this machine the interpreter is the `msmt202q` conda environment. Do **not** hardcode an
interpreter path in a committed file -- put it in the gitignored `CLAUDE.local.md`.

## Style

- All imports at the top of the file, grouped stdlib / third-party / local, sorted within
  each group. Never `from x import *`.
- **ASCII only in anything printable** -- print statements, log messages, f-strings,
  docstrings. Use `phi` not the Greek letter, `->` not an arrow, `+/-` not the sign,
  `--` not an em dash. Windows consoles are cp1252 and raise `UnicodeEncodeError` otherwise.
  This does not apply to `.bib` fixture content, which is deliberately non-ASCII to test the
  parser.
- Never hardcode an absolute path in a committed file. Use `Path(__file__).parent.resolve()`.
- black, line length 119. isort with the black profile. PEP 8, Google-style docstrings.
- New code: put configuration in named constants at the top of the file, changed by editing
  them. Do not add `argparse` unless asked. `bibcheck/main.py` is a deliberate exception --
  it is a working, tested CLI that predates this repo.

## Architecture rules

- **Deterministic code and the language model never mix.** Python does HTTP, parsing, dedup,
  caching and validation, and contains no model call. Claude does query expansion, screening
  and extraction, and never makes an HTTP request. Stage boundaries are files on disk.
- **One HTTP layer.** `bibcheck/verify.py` already provides a cached, rate-limited,
  offline-replayable index client with retry/backoff, polite-pool `mailto` and fuzzy title
  matching. Reuse it. Do not write a second one.
- **The validation gate is not optional.** No work reaches a `litsearch` output unless
  `bibcheck` resolved it against an index. Unresolved works go to quarantine with a stated
  reason -- never silently dropped, never silently included.
- **No extracted value without a source quote.** If it cannot be quoted, it is `null`.

## Testing

- No test opens a socket. Network-facing code is tested with `offline=True` against a
  pre-seeded cache of recorded payloads -- see `bibcheck/tests/test_verify.py` for the pattern.
- Run artefacts (`*_checked.bib`, reports, caches, `out/`, `runs/`) are gitignored. Never
  commit search output.

## Secrets

API tokens live in `.env`, which is gitignored. This is a **public** repository -- never
commit a token, an email address other than the maintainer's own, or a cached API response
containing credentials.
