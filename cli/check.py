"""Check Obsidian notes for factual errors against textbooks via RAG.

Extracts claims from notes, cross-checks each against the textbook index,
and inserts Obsidian callouts at the top of notes where errors are found.

Usage:
    uv run anki-check-notes [vault_path] [books_config]
    uv run anki-check-notes [vault_path] [books_config] path/to/note.md ...
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reason.check import check_all_notes, ClaimIssue, NoteReport

# ANSI colors for terminal output
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# Regex for YAML frontmatter block at the top of a note.
_FRONTMATTER_RE = re.compile(r"\A(---\s*\n.*?\n---\n?)", re.DOTALL)

# Marker so we can strip old callouts on re-runs.
_CALLOUT_MARKER = "<!-- anki-check -->"

_CALLOUT_TYPE = {
    "error": "danger",
    "warning": "warning",
}


def _build_callout(issue: ClaimIssue) -> str:
    """Build an Obsidian callout block for one issue."""
    callout_type = _CALLOUT_TYPE.get(issue.severity, "warning")
    title = f"Factual {issue.verdict}"
    lines = [
        _CALLOUT_MARKER,
        f"> [!{callout_type}] {title}",
        f"> **You wrote:** {issue.claim}",
        f"> **Correction:** {issue.correction}",
    ]
    if issue.citation:
        lines.append(f"> **Source:** {issue.citation}")
    return "\n".join(lines)


def _strip_old_callouts(content: str) -> str:
    """Remove all callout blocks from previous check runs."""
    lines = content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == _CALLOUT_MARKER:
            # Skip the marker and all following `> ` lines
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                i += 1
            # Skip trailing blank line after callout
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        result.append(lines[i])
        i += 1
    return "\n".join(result)


def _inject_callouts(
    vault_path: Path,
    report: NoteReport,
) -> None:
    """Insert callout blocks at the top of a note (after frontmatter)."""
    note_file = vault_path / report.rel_path
    content = note_file.read_text(errors="replace")

    # Strip any old check callouts first
    content = _strip_old_callouts(content)

    # Build the callout block
    callout_block = "\n\n".join(
        _build_callout(issue) for issue in report.issues
    ) + "\n\n"

    # Insert after frontmatter if present, otherwise at the top
    m = _FRONTMATTER_RE.match(content)
    if m:
        insert_pos = m.end()
        # Ensure there's a newline between frontmatter and callout
        if not content[insert_pos:].startswith("\n"):
            callout_block = "\n" + callout_block
        new_content = content[:insert_pos] + callout_block + content[insert_pos:].lstrip("\n")
    else:
        new_content = callout_block + content.lstrip("\n")

    note_file.write_text(new_content)


def _print_report(reports: list[NoteReport]) -> int:
    """Print the check report to terminal. Returns total issue count."""
    total = 0
    for report in reports:
        if not report.issues:
            continue
        print(f"\n{_BOLD}── {report.rel_path}{_RESET}")
        for issue in report.issues:
            tag = f"{_RED}{_BOLD}ERROR{_RESET}" if issue.severity == "error" else f"{_YELLOW}WARNING{_RESET}"
            print(f"  {tag}  {issue.claim}")
            total += 1
    return total


def check(
    vault_path: str | Path = "/Users/root1/Obsidian/quant",
    config_path: str = "books.yaml",
    note_paths: list[str] | None = None,
) -> None:
    """Run the check pipeline."""
    vault_path = Path(vault_path).resolve()
    print(f"Vault:  {vault_path}")
    print(f"Books:  {config_path}")
    if note_paths:
        print(f"Notes:  {len(note_paths)} specified")
    else:
        print("Notes:  all")

    reports = check_all_notes(
        vault_path,
        config_path=config_path,
        paths=note_paths,
    )

    # Inject callouts into notes with issues
    injected = 0
    for report in reports:
        if report.issues:
            _inject_callouts(vault_path, report)
            injected += 1

    # Print summary
    total = _print_report(reports)
    notes_checked = len(reports)

    print(f"\n{_BOLD}{'─' * 50}{_RESET}")
    print(f"Checked {notes_checked} note(s)")

    if total == 0:
        print(f"{_GREEN}No issues found.{_RESET}")
    else:
        errors = sum(1 for r in reports for i in r.issues if i.severity == "error")
        warnings = total - errors
        parts = []
        if errors:
            parts.append(f"{_RED}{errors} error(s){_RESET}")
        if warnings:
            parts.append(f"{_YELLOW}{warnings} warning(s){_RESET}")
        print(f"Found {', '.join(parts)} — inserted callouts into {injected} note(s)")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    vault = args[0] if len(args) > 0 else "/Users/root1/Obsidian/quant"
    config = args[1] if len(args) > 1 else "books.yaml"
    note_paths = args[2:] if len(args) > 2 else None

    check(vault, config, note_paths)


if __name__ == "__main__":
    main()
