"""Check a BibTeX file and write a cleaned, publication-ready copy beside it.

What it does, in order:

1. Parses the ``.bib`` file, preserving every field value verbatim.
2. Normalises what has only one right answer: entry-type case, arXiv ids buried in
   ``note``/``journal``, DOI resolver prefixes, page-range dashes.
3. Re-keys every entry to the ``LastnameYEAR`` convention, suffixing whole collision
   groups with a/b/c.
4. Checks completeness per entry type and reports duplicates and non-ASCII bytes.
5. Optionally verifies each entry against Crossref, arXiv and OpenAlex.
6. Writes ``<stem>_checked.bib`` plus Markdown, JSON and plain-text reports.

The input file is never modified: the resolved output path is refused if it equals the
input. Cite keys in ``.tex`` files are NOT rewritten; the rename map is reported instead.

Examples::

    python -m bibcheck.main ../sideband/manuscript/refs.bib --no-write
    python -m bibcheck.main ../sideband/manuscript/refs.bib \\
        --verify --mailto you@example.com --out-dir ./bibcheck_out
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from bibcheck import parser as bibparser
from bibcheck import report as reporting
from bibcheck.keys import assign_keys
from bibcheck.rules import check_database
from bibcheck.verify import IndexClient, apply_suggestions, verify_all

DEFAULT_SORT = "sections"
SUFFIX_BIB = "_checked.bib"
SUFFIX_MD = "_bibcheck_report.md"
SUFFIX_JSON = "_bibcheck_report.json"
SUFFIX_TXT = "_refs_plaintext.txt"
SUFFIX_CACHE = "_bibcheck_cache.json"


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m bibcheck.main",
        description="Unify keys, sort, check completeness and verify a BibTeX file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("input", help="path to the .bib file to check (never modified)")
    ap.add_argument(
        "--out", default=None, help=f"output .bib path (default: <input stem>{SUFFIX_BIB} beside the input)"
    )
    ap.add_argument("--out-dir", default=None, help="directory for all outputs (default: alongside the input)")
    ap.add_argument("--force", action="store_true", help="overwrite output files that already exist")
    ap.add_argument(
        "--key-style",
        default="lastnameyear",
        choices=["lastnameyear"],
        help="citation key convention (default: %(default)s, e.g. Cahill1969)",
    )
    ap.add_argument(
        "--sort",
        default=DEFAULT_SORT,
        choices=["sections", "global", "none"],
        help="sections keeps %%%% banner blocks and sorts within them; global emits one flat sorted list (default: %(default)s)",
    )
    ap.add_argument("--verify", action="store_true", help="query Crossref, arXiv and OpenAlex (network access)")
    ap.add_argument("--offline", action="store_true", help="with --verify, use only the on-disk cache")
    ap.add_argument("--mailto", default="", help="contact address sent to Crossref/OpenAlex for polite-pool service")
    ap.add_argument(
        "--fix-from-index",
        action="store_true",
        help='with --verify, fill empty fields and expand "and others" from the index records',
    )
    ap.add_argument(
        "--ascii",
        action="store_true",
        help="rewrite non-ASCII characters in field values as LaTeX escapes (e.g. e-acute -> {'e})",
    )
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors in the exit code")
    ap.add_argument("--no-write", action="store_true", help="report only; write no files")
    ap.add_argument("--show-info", action="store_true", help="include automatic changes in the console output")
    return ap


def _resolve_outputs(args: argparse.Namespace, input_path: Path) -> dict[str, Path]:
    out_dir = Path(args.out_dir).resolve() if args.out_dir else input_path.parent
    stem = input_path.stem
    bib_path = Path(args.out).resolve() if args.out else out_dir / f"{stem}{SUFFIX_BIB}"
    return {
        "bib": bib_path,
        "markdown": out_dir / f"{stem}{SUFFIX_MD}",
        "json": out_dir / f"{stem}{SUFFIX_JSON}",
        "plaintext": out_dir / f"{stem}{SUFFIX_TXT}",
        "cache": out_dir / f"{stem}{SUFFIX_CACHE}",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        print(f"error: no such file: {input_path}")
        return 2

    outputs = _resolve_outputs(args, input_path)
    if outputs["bib"] == input_path:
        print(f"error: refusing to overwrite the input file: {input_path}")
        print("       choose a different --out or --out-dir")
        return 2

    db = bibparser.read(input_path)
    # Keys first, so every finding is reported under the key the output file will use.
    rename_map, key_problems = assign_keys(db.entries)
    entry_reports, file_findings = check_database(db, ascii_only=args.ascii)

    report = reporting.Report(
        db=db,
        input_path=input_path,
        entry_reports=entry_reports,
        file_findings=file_findings,
        rename_map=rename_map,
        key_problems=key_problems,
    )
    for entry, reason in key_problems:
        report.file_findings.append(
            reporting.Finding("error", "unkeyable", f"kept original key: {reason}", entry.effective_key, entry.line)
        )

    if args.verify:
        client = IndexClient(cache_path=outputs["cache"], mailto=args.mailto, offline=args.offline)
        verifications = verify_all(entry_reports, client)
        report.verifications = {verification.key: verification for verification in verifications}
        report.network_errors = client.network_errors
        if args.fix_from_index:
            for entry_report in entry_reports:
                verification = report.verifications.get(entry_report.entry.effective_key)
                if verification is not None:
                    entry_report.findings.extend(apply_suggestions(entry_report, verification))
    elif args.fix_from_index:
        print("note: --fix-from-index has no effect without --verify")

    if not args.no_write:
        existing = [path for label, path in outputs.items() if label != "cache" and path.exists()]
        if existing and not args.force:
            print("error: output files already exist; pass --force to overwrite:")
            for path in existing:
                print(f"       {path}")
            return 2
        bibparser.write(db, outputs["bib"], sort=args.sort)
        report.outputs["bib"] = outputs["bib"]
        report.outputs["plaintext"] = reporting.write_text(
            outputs["plaintext"], reporting.plaintext_references(db.entries)
        )
        report.outputs["markdown"] = reporting.write_text(outputs["markdown"], reporting.markdown_report(report))
        report.outputs["json"] = reporting.write_text(
            outputs["json"], json.dumps(reporting.json_report(report), indent=2, sort_keys=True) + "\n"
        )

    print(reporting.console_summary(report, show_info=args.show_info))

    exit_code = report.exit_code
    if args.strict and exit_code == 1:
        exit_code = 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
