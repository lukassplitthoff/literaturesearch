"""Rendering: ASCII console summary, Markdown and JSON reports, plain-text export.

Everything emitted here is pure ASCII, including the report files, so nothing in the
pipeline can trip a cp1252 UnicodeEncodeError on Windows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bibcheck.keys import entry_year, split_authors, strip_latex
from bibcheck.parser import Database, Entry
from bibcheck.rules import EntryReport, Finding
from bibcheck.verify import Verification

LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Report:
    """Everything one run of the checker learned, ready to be rendered."""

    db: Database
    input_path: Path
    entry_reports: list[EntryReport] = field(default_factory=list)
    file_findings: list[Finding] = field(default_factory=list)
    rename_map: dict[str, str] = field(default_factory=dict)
    key_problems: list[tuple[Entry, str]] = field(default_factory=list)
    verifications: dict[str, Verification] = field(default_factory=dict)
    outputs: dict[str, Path] = field(default_factory=dict)
    network_errors: list[str] = field(default_factory=list)

    @property
    def all_findings(self) -> list[Finding]:
        findings = list(self.file_findings)
        for entry_report in self.entry_reports:
            findings.extend(entry_report.findings)
        for verification in self.verifications.values():
            findings.extend(verification.findings)
        return findings

    def count(self, level: str) -> int:
        return sum(1 for finding in self.all_findings if finding.level == level)

    @property
    def exit_code(self) -> int:
        """0 when clean, 1 when only warnings were raised, 2 when anything is an error."""
        if self.count("error"):
            return 2
        if self.count("warning"):
            return 1
        return 0


# ------------------------------------------------------------------- console


def console_summary(report: Report, show_info: bool = False) -> str:
    """Render the run as ASCII lines for stdout."""
    lines: list[str] = []
    entries = report.db.entries
    lines.append(f"bibcheck: {report.input_path}")
    lines.append(f"  entries      : {len(entries)}")
    lines.append(f"  keys renamed : {len(report.rename_map)}")
    if report.key_problems:
        lines.append(f"  keys unbuilt : {len(report.key_problems)} (kept their original key)")
    if report.verifications:
        counts: dict[str, int] = {}
        for verification in report.verifications.values():
            counts[verification.verdict] = counts.get(verification.verdict, 0) + 1
        summary = ", ".join(f"{verdict}={counts[verdict]}" for verdict in sorted(counts))
        lines.append(f"  verification : {summary}")
    lines.append(f'  errors       : {report.count("error")}')
    lines.append(f'  warnings     : {report.count("warning")}')

    if report.rename_map:
        lines.append("")
        lines.append("Key renames (no .tex files are touched; apply these yourself):")
        width = max(len(old) for old in report.rename_map)
        for old in sorted(report.rename_map):
            lines.append(f"  {old.ljust(width)} -> {report.rename_map[old]}")

    findings = [finding for finding in report.all_findings if show_info or finding.level != "info"]
    if findings:
        lines.append("")
        lines.append("Findings:")
        findings.sort(key=lambda f: (LEVEL_ORDER.get(f.level, 9), f.key or "", f.code))
        for finding in findings:
            lines.append("  " + finding.render())

    if report.network_errors:
        lines.append("")
        lines.append(f"Network problems ({len(report.network_errors)}); verification may be incomplete:")
        for message in report.network_errors[:5]:
            lines.append(f"  {message}")

    if report.outputs:
        lines.append("")
        lines.append("Wrote:")
        for label in sorted(report.outputs):
            lines.append(f"  {label:10s} {report.outputs[label]}")

    return "\n".join(lines)


# ------------------------------------------------------------------ markdown


def _finding_table(findings: list[Finding]) -> list[str]:
    rows = ["| level | key | code | message |", "| --- | --- | --- | --- |"]
    for finding in sorted(findings, key=lambda f: (LEVEL_ORDER.get(f.level, 9), f.key or "", f.code)):
        message = finding.message.replace("|", "\\|")
        rows.append(f'| {finding.level} | {finding.key or "-"} | {finding.code} | {message} |')
    return rows


def markdown_report(report: Report) -> str:
    """Render the full human-readable report."""
    lines = [
        f"# bibcheck report: {report.input_path.name}",
        "",
        f"- source: `{report.input_path}`",
        f"- entries: {len(report.db.entries)}",
        f'- errors: {report.count("error")}, warnings: {report.count("warning")}, info: {report.count("info")}',
        "",
    ]

    if report.rename_map:
        lines += [
            "## Key renames",
            "",
            "This tool does not touch `.tex` files. Apply these renames to your `\\cite{}`",
            "commands before using the cleaned bibliography.",
            "",
            "| old key | new key |",
            "| --- | --- |",
        ]
        lines += [f"| `{old}` | `{report.rename_map[old]}` |" for old in sorted(report.rename_map)]
        lines.append("")

    if report.key_problems:
        lines += ["## Entries that could not be re-keyed", "", "| key | reason |", "| --- | --- |"]
        lines += [f"| `{entry.key}` | {reason} |" for entry, reason in report.key_problems]
        lines.append("")

    errors = [finding for finding in report.all_findings if finding.level == "error"]
    warnings = [finding for finding in report.all_findings if finding.level == "warning"]
    infos = [finding for finding in report.all_findings if finding.level == "info"]

    for title, findings in (("Errors", errors), ("Warnings", warnings), ("Automatic changes", infos)):
        if not findings:
            continue
        lines += [f"## {title}", ""] + _finding_table(findings) + [""]

    if report.verifications:
        lines += [
            "## Verification against Crossref / arXiv / OpenAlex",
            "",
            "| key | verdict | source | mismatched fields |",
            "| --- | --- | --- | --- |",
        ]
        for key in sorted(report.verifications):
            verification = report.verifications[key]
            mismatched = ", ".join(verification.mismatched_fields) or "-"
            lines.append(f'| `{key}` | {verification.verdict} | {verification.source or "-"} | {mismatched} |')
        lines.append("")

        detailed = [v for v in report.verifications.values() if v.mismatched_fields]
        if detailed:
            lines += ["### Field-level differences", ""]
            for verification in sorted(detailed, key=lambda v: v.key):
                lines.append(f"**`{verification.key}`** (matched against {verification.source})")
                lines.append("")
                lines += ["| field | local | index |", "| --- | --- | --- |"]
                for comparison in verification.comparisons:
                    if comparison.status != "mismatch":
                        continue
                    lines.append(
                        f'| {comparison.field} | {comparison.local.replace("|", "-")} '
                        f'| {comparison.remote.replace("|", "-")} |'
                    )
                lines.append("")

    lines += [
        "## Independent cross-check",
        "",
        "The plain-text export next to this report can be pasted into a browser-based",
        "reference checker for a second opinion:",
        "",
        "- <https://citeme.app/tools/reference-checker>",
        "- <https://citely.ai/reference-checker>",
        "",
        "Both query the same indexes this tool queries (Crossref, OpenAlex, arXiv), so",
        "they are a confirmation rather than an additional source of truth.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------- json


def json_report(report: Report) -> dict:
    """Render the same content as a machine-readable structure."""
    return {
        "input": str(report.input_path),
        "entry_count": len(report.db.entries),
        "counts": {level: report.count(level) for level in ("error", "warning", "info")},
        "exit_code": report.exit_code,
        "rename_map": report.rename_map,
        "key_problems": [{"key": entry.key, "reason": reason} for entry, reason in report.key_problems],
        "findings": [
            {
                "level": finding.level,
                "code": finding.code,
                "key": finding.key,
                "line": finding.line,
                "message": finding.message,
            }
            for finding in report.all_findings
        ],
        "verifications": {
            key: {
                "verdict": verification.verdict,
                "source": verification.source,
                "reason": verification.reason,
                "record": verification.record.as_dict() if verification.record else None,
                "comparisons": [
                    {
                        "field": comparison.field,
                        "local": comparison.local,
                        "remote": comparison.remote,
                        "status": comparison.status,
                    }
                    for comparison in verification.comparisons
                ],
                "suggestions": verification.suggestions,
            }
            for key, verification in report.verifications.items()
        },
        "outputs": {label: str(path) for label, path in report.outputs.items()},
        "network_errors": report.network_errors,
    }


# ----------------------------------------------------------------- plaintext


def _plain_authors(entry: Entry) -> str:
    """Render the author list the way a printed reference would."""
    authors = split_authors(entry.get("author") or "")
    if not authors:
        return ""
    rendered = [strip_latex(name).strip("{} ") for name in authors]
    rendered = [name for name in rendered if name and name.lower() != "others"]
    truncated = len(rendered) < len(authors)
    if len(rendered) > 6 or truncated:
        return rendered[0] + " et al."
    if len(rendered) == 1:
        return rendered[0]
    return ", ".join(rendered[:-1]) + " and " + rendered[-1]


def plaintext_references(entries: list[Entry]) -> str:
    """Render entries as a numbered plain-text bibliography.

    The output is deliberately plain and reference-manager friendly, so it can be pasted
    straight into CiteMe or Citely for an independent check.
    """
    lines: list[str] = []
    for number, entry in enumerate(sorted(entries, key=lambda e: e.effective_key.lower()), start=1):
        parts: list[str] = []
        authors = _plain_authors(entry)
        if authors:
            parts.append(authors + ".")
        title = strip_latex(entry.get("title") or "").strip(". ")
        if title:
            parts.append(title + ".")

        where = strip_latex(entry.get("journal") or entry.get("booktitle") or entry.get("school") or "")
        volume = (entry.get("volume") or "").strip()
        pages = re.sub(r"--", "-", (entry.get("pages") or "").strip())
        locator = " ".join(part for part in (where, volume) if part)
        if locator and pages:
            locator += f", {pages}"
        elif pages:
            locator = f"pp. {pages}"

        year = entry_year(entry)
        if year:
            locator = f"{locator} ({year})".strip()
        if locator:
            parts.append(locator + ".")

        if entry.has("doi"):
            parts.append(f'https://doi.org/{entry.get("doi").strip()}')
        elif entry.has("eprint"):
            parts.append(f'arXiv:{entry.get("eprint").strip()}')
        elif entry.has("url"):
            parts.append(entry.get("url").strip())

        # 'et al.' followed by the separator period would otherwise read as '..'.
        lines.append(re.sub(r"\.\.+", ".", f"[{number}] " + " ".join(parts)))
    return "\n".join(lines) + "\n"


def write_text(path: Path, text: str) -> Path:
    """Write ASCII text with Unix line endings, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii", errors="backslashreplace", newline="\n")
    return path
