"""Check Obsidian notes for factual errors against textbooks via RAG.

Extracts claims from notes, cross-checks each against the textbook index,
and inserts Obsidian callouts above the lines where errors are found.

Usage:
    uv run anki-check-notes [vault_path] [config_path]
    uv run anki-check-notes [vault_path] [config_path] path/to/note.md ...
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from cli.check_markers import BROKEN_LINK_MARKER, CALLOUT_MARKER
from reason.check import check_all_notes, check_broken_links, ClaimIssue, NoteReport
from sync.config import load_app_config

# ANSI colors for terminal output
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# Regex for YAML frontmatter block at the top of a note.
_FRONTMATTER_RE = re.compile(r"\A(---\s*\n.*?\n---\n?)", re.DOTALL)

# Backwards-compatible name for tests and internal callers.
_CALLOUT_MARKER = CALLOUT_MARKER

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


def _normalized_text(text: str) -> str:
    """Normalize Markdown-ish text enough to match extracted claims to lines."""
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.lower().split())


def _find_claim_line(lines: list[str], claim: str, start_idx: int) -> int | None:
    """Return the best matching line index for an extracted claim."""
    best_idx = None
    best_score = 0.0
    for idx in range(start_idx, len(lines)):
        score = _claim_line_score(lines[idx], claim)
        if score > best_score:
            best_idx = idx
            best_score = score

    return best_idx if best_score >= 0.55 else None


def _claim_line_score(line: str, claim: str) -> float:
    """Score how likely a note line is to contain an extracted claim."""
    if claim in line:
        return 1.0

    normalized_line = _normalized_text(line)
    normalized_claim = _normalized_text(claim)
    if not normalized_claim or not normalized_line:
        return 0.0
    if normalized_claim in normalized_line:
        return 0.98

    claim_tokens = set(normalized_claim.split())
    line_tokens = set(normalized_line.split())
    if not claim_tokens or not line_tokens:
        return 0.0

    shared_tokens = claim_tokens & line_tokens
    claim_coverage = len(shared_tokens) / len(claim_tokens)
    line_coverage = len(shared_tokens) / len(line_tokens)
    token_score = (claim_coverage * 0.75) + (line_coverage * 0.25)
    sequence_score = SequenceMatcher(None, normalized_claim, normalized_line).ratio()
    return max(token_score, sequence_score)


def _strip_old_callouts(content: str) -> str:
    """Remove all callout blocks from previous check runs."""
    markers = {_CALLOUT_MARKER, BROKEN_LINK_MARKER}
    lines = content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        if lines[i].strip() in markers:
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
    """Insert callout blocks above matching claim lines when possible."""
    note_file = vault_path / report.rel_path
    content = note_file.read_text(errors="replace")

    # Strip any old check callouts first
    content = _strip_old_callouts(content)

    # Avoid inserting generated callouts inside YAML frontmatter.
    m = _FRONTMATTER_RE.match(content)
    body_start_idx = content[:m.end()].count("\n") if m else 0

    lines = content.splitlines(keepends=True)
    issues_by_line: dict[int, list[ClaimIssue]] = {}
    fallback_issues: list[ClaimIssue] = []

    for issue in report.issues:
        match_idx = _find_claim_line(lines, issue.claim, body_start_idx)

        if match_idx is None:
            fallback_issues.append(issue)
        else:
            issues_by_line.setdefault(match_idx, []).append(issue)

    if fallback_issues:
        issues_by_line.setdefault(body_start_idx, []).extend(fallback_issues)

    if not lines:
        new_content = "\n\n".join(_build_callout(issue) for issue in report.issues) + "\n"
    else:
        output: list[str] = []
        for idx, line in enumerate(lines):
            if idx in issues_by_line:
                output.append("\n\n".join(
                    _build_callout(issue) for issue in issues_by_line[idx]
                ) + "\n\n")
            output.append(line)

        if len(lines) in issues_by_line:
            output.append("\n\n".join(
                _build_callout(issue) for issue in issues_by_line[len(lines)]
            ) + "\n\n")

        new_content = "".join(output)

    note_file.write_text(new_content)


def _build_broken_link_callout(issues: list[ClaimIssue]) -> str:
    """Build a single callout listing all broken wikilinks in a note."""
    links = ", ".join(issue.claim for issue in issues)
    lines = [
        BROKEN_LINK_MARKER,
        f"> [!warning] Broken link{'s' if len(issues) > 1 else ''}: {links}",
        f"> Linked note{'s were' if len(issues) > 1 else ' was'} deleted — this note may need updating or removal",
    ]
    return "\n".join(lines)


def _inject_broken_link_callouts(
    vault_path: Path,
    report: NoteReport,
) -> None:
    """Insert a broken-link callout at the top of the note body."""
    note_file = vault_path / report.rel_path
    content = note_file.read_text(errors="replace")

    callout = _build_broken_link_callout(report.issues)

    # Insert after frontmatter if present
    m = _FRONTMATTER_RE.match(content)
    if m:
        insert_at = m.end()
        before = content[:insert_at]
        after = content[insert_at:]
        # Ensure blank line separation
        if after and not after.startswith("\n"):
            after = "\n" + after
        new_content = before + "\n" + callout + "\n" + after
    else:
        new_content = callout + "\n\n" + content

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
    vault_path: str | Path | None = None,
    config_path: str | Path | None = None,
    note_paths: list[str] | None = None,
) -> None:
    """Run the check pipeline."""
    app_config = load_app_config()
    vault_path = vault_path or app_config.vault_path
    config_path = config_path or app_config.books_config

    vault_path = Path(vault_path).resolve()
    print(f"Vault:  {vault_path}")
    print(f"Books:  {config_path}")
    if note_paths:
        print(f"Notes:  {len(note_paths)} specified")
    else:
        print("Notes:  all")

    # Check for broken wikilinks (no LLM needed)
    link_reports = check_broken_links(vault_path, paths=note_paths)
    link_injected = 0
    for report in link_reports:
        if report.issues:
            _inject_broken_link_callouts(vault_path, report)
            link_injected += 1

    broken_link_count = sum(len(r.issues) for r in link_reports)
    if broken_link_count:
        print(f"  {broken_link_count} broken link(s) in {link_injected} note(s)")

    # Check for factual errors (LLM + RAG)
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
    total += broken_link_count
    notes_checked = len(reports)

    print(f"\n{_BOLD}{'─' * 50}{_RESET}")
    print(f"Checked {notes_checked} note(s)")

    if total == 0:
        print(f"{_GREEN}No issues found.{_RESET}")
    else:
        errors = sum(1 for r in reports for i in r.issues if i.severity == "error")
        warnings = total - errors + broken_link_count
        parts = []
        if errors:
            parts.append(f"{_RED}{errors} error(s){_RESET}")
        if warnings:
            parts.append(f"{_YELLOW}{warnings} warning(s){_RESET}")
        print(f"Found {', '.join(parts)} — inserted callouts into {injected + link_injected} note(s)")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    app_config = load_app_config()
    vault = args[0] if len(args) > 0 else app_config.vault_path
    config = args[1] if len(args) > 1 else app_config.books_config
    note_paths = args[2:] if len(args) > 2 else None

    check(vault, config, note_paths)


if __name__ == "__main__":
    main()
