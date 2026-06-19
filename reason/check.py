"""Check notes for factual errors against textbook passages via RAG.

For each note:
  1. LLM extracts factual claims (statements of fact the student wrote).
  2. For each claim, RAG retrieves relevant textbook passages.
  3. LLM judges whether the claim is correct, wrong, or imprecise.

This is NOT about card proposals — it's about catching mistakes in the notes
themselves (e.g. "OS uses time sharing to share space on the CPU" when
time sharing actually shares CPU *time*, not space).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from rag.query import retrieve
from .llm import chat_json, DEFAULT_MODEL


# ------------------------------------------------------------------
# Data types
# ------------------------------------------------------------------

@dataclass
class ClaimIssue:
    """A single factual claim in a note that has a problem."""
    claim: str                # what the student wrote
    verdict: str              # "wrong", "imprecise"
    correction: str           # what the textbook actually says
    citation: str | None       # e.g. "OSTEP · vm-tlbs · p.7"
    severity: str             # "error" or "warning"


@dataclass
class NoteReport:
    """All issues found in a single note."""
    rel_path: str
    issues: list[ClaimIssue] = field(default_factory=list)


@dataclass(frozen=True)
class CheckCandidate:
    rel_path: str
    path: Path
    content: str
    content_hash: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------

EXTRACT_CLAIMS_SYSTEM = """\
You are a study-note reviewer. Given a student's Markdown note, extract every \
factual claim — statements that could be right or wrong. Focus on definitions, \
cause-effect relationships, mechanisms, distinctions, and process descriptions.

Skip: headings, metadata, opinions, questions, TODOs, and formatting-only lines.

Reply with a JSON object:
{
  "claims": [
    {
      "claim": "<the factual statement as the student wrote it>",
      "query": "<a short question to search the textbook for this topic>"
    }
  ]
}

Rules:
- Extract claims verbatim or as close to the original wording as possible.
- One claim per entry — split compound sentences if they contain multiple facts.
- Include claims even if you think they're correct — the textbook will confirm.\
"""

CHECK_CLAIM_SYSTEM = """\
You are a textbook fact-checker. Given a student's claim and relevant textbook \
passages (with citations), determine whether the claim is:

- "correct" — the textbook confirms it, no issues
- "imprecise" — the claim is not flatly contradicted, but its wording would \
teach the wrong concept because it uses wrong terminology, conflates concepts, \
or materially overstates/understates something
- "wrong" — the textbook directly contradicts the claim
- "not_relevant" — the passages don't cover this topic, or the claim is true \
but outside the scope of the uploaded book

Reply with a JSON object:
{
  "verdict": "correct" | "imprecise" | "wrong" | "not_relevant",
  "correction": "<what the textbook actually says, or null if correct>",
  "explanation": "<one sentence explaining the issue, or null if correct>",
  "best_citation": "<the citation string of the most relevant passage, or null>"
}

Rules:
- Be strict about factual accuracy. If the student says "time sharing shares \
space on the CPU" but the textbook says time sharing shares CPU *time*, that \
is WRONG — not imprecise.
- For "imprecise": the core idea is right but the wording is materially \
misleading in a way that would lose marks or cause confusion. Do not use \
"imprecise" for merely incomplete wording.
- Do NOT mark a claim imprecise for grammar, style, or a close paraphrase. If \
the correction only rewrites the same idea in textbook wording, mark it correct.
- Do NOT mark a claim wrong just because the student used terse note wording. \
Definitions like "scheduled: ready to running" and "scheduled means moved from \
ready to running" have the same factual content.
- Do NOT require a high-level note to list every trigger, example, exception, or \
implementation state from the passage. If the student's statement is compatible \
with the passage but incomplete, mark it correct unless the omission changes the \
meaning or would cause a false answer on an exam.
- Preserve the student's note style. Do NOT flag a claim just because a fuller \
textbook answer would add surrounding mechanisms, trigger conditions, or \
implementation details the student did not assert. Only flag it when the words \
the student actually wrote are false or materially misleading.
- Treat compatible simplifications as correct. If your correction starts by \
agreeing with the student's claim and then adds "however", "but", "separately", \
or API/implementation details, the verdict should usually be "correct".
- Do NOT flag a claim just because the passage gives a more exact mechanism, \
subcase, prerequisite, return value, cleanup rule, or data-structure placement. \
Flag only if the student's claim is incompatible with that information.
- Do NOT flag nonstandard labels such as "initial" or "final" merely because the \
passage uses different labels, unless the passage directly contradicts the \
student's meaning.
- For "not_relevant": use this when the uploaded book passages do not actually \
address the claim. Do not warn just because the claim is outside the book.
- Only mark "wrong" or "imprecise" when the provided passages give direct, \
relevant evidence about the same topic.
- Use ONLY the provided passages as evidence.\
"""


# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------

def extract_claims(
    note_content: str,
    *,
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    """LLM extracts factual claims from a note."""
    result = chat_json([
        {"role": "system", "content": EXTRACT_CLAIMS_SYSTEM},
        {"role": "user", "content": note_content},
    ], model=model)
    return result.get("claims", [])


def check_claim(
    claim: str,
    passages: list[dict],
    *,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Check a single claim against RAG passages."""
    passage_text = "\n\n".join(
        f"[{p['citation']}]\n{p['text']}" for p in passages
    ) if passages else "(no relevant textbook passages found)"

    result = chat_json([
        {"role": "system", "content": CHECK_CLAIM_SYSTEM},
        {"role": "user", "content": (
            f"## Student's claim\n{claim}\n\n"
            f"## Textbook passages\n{passage_text}"
        )},
    ], model=model)
    return result


def _normalized_claim_text(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(text.split())


_PEDANTIC_CORRECTION_PATTERNS = (
    "also mentions",
    "also switches",
    "but the passage specifies",
    "but this",
    "does not explicitly define",
    "doesn't explicitly define",
    "not explicitly define",
    "does not explicitly state",
    "doesn't explicitly state",
    "not explicitly state",
    "exit status",
    "final step",
    "hardware initially saves",
    "however",
    "kernel stack",
    "macros like",
    "must pass a pointer",
    "return value",
    "separately tracks",
    "specific registers",
    "typically part",
    "triggered by events",
)


def _is_close_paraphrase(claim: str, correction: str | None) -> bool:
    """Return true when a correction is just a close paraphrase."""
    if not correction:
        return True

    normalized_claim = _normalized_claim_text(claim)
    normalized_correction = _normalized_claim_text(correction)
    if not normalized_claim or not normalized_correction:
        return False

    if normalized_claim == normalized_correction:
        return True

    similarity = SequenceMatcher(
        None,
        normalized_claim,
        normalized_correction,
    ).ratio()
    return similarity >= 0.74


def _is_pedantic_correction(correction: str | None) -> bool:
    """Return true for textbook-meta warnings that do not identify a contradiction."""
    if not correction:
        return True

    normalized_correction = _normalized_claim_text(correction)
    return any(
        pattern in normalized_correction
        for pattern in _PEDANTIC_CORRECTION_PATTERNS
    )


def _should_suppress_issue(
    verdict: str,
    claim: str,
    correction: str | None,
) -> bool:
    """Suppress issue reports that amount to wording or coverage nitpicks."""
    if verdict not in {"wrong", "imprecise"}:
        return False

    if _is_close_paraphrase(claim, correction):
        return True

    return verdict == "imprecise" and _is_pedantic_correction(correction)


def _is_trivial_imprecision(claim: str, correction: str | None) -> bool:
    """Backward-compatible wrapper for existing tests and callers."""
    return _is_close_paraphrase(claim, correction)


def check_note(
    rel_path: str,
    content: str,
    *,
    model: str = DEFAULT_MODEL,
    config_path: str = "config.toml",
) -> NoteReport:
    """Full check pipeline for one note: extract claims → RAG → verdict."""
    report = NoteReport(rel_path=rel_path)

    # Step 1: extract claims
    print(f"  extracting claims from {rel_path} ...")
    claims = extract_claims(content, model=model)
    if not claims:
        print(f"  no factual claims found in {rel_path}")
        return report

    # Step 2: check each claim
    print(f"  checking {len(claims)} claim(s) ...")
    for claim_data in claims:
        claim_text = claim_data.get("claim", "")
        if not claim_text:
            continue
        query = claim_data.get("query", claim_text)

        passages = retrieve(query, k=5, config_path=config_path)
        result = check_claim(claim_text, passages, model=model)

        verdict = result.get("verdict", "correct")
        if verdict in {"correct", "not_relevant", "unsupported"}:
            continue  # no issue
        correction = result.get("correction") or result.get("explanation", "")
        if _should_suppress_issue(
            verdict,
            claim_text,
            correction,
        ):
            continue

        severity = "error" if verdict == "wrong" else "warning"
        issue = ClaimIssue(
            claim=claim_text,
            verdict=verdict,
            correction=correction,
            citation=result.get("best_citation"),
            severity=severity,
        )
        report.issues.append(issue)

    return report


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")


def check_broken_links(
    vault_path: str | Path,
    *,
    paths: list[str] | None = None,
) -> list[NoteReport]:
    """Find notes with [[wikilinks]] pointing to notes that don't exist.

    Returns NoteReport objects with warning-severity issues for each
    broken link — the referenced note was deleted or never existed.
    """
    from sync.vault import IGNORE_DIRS, IGNORE_FILES

    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"Vault not found: {vault}")

    # Build set of all note stems (case-insensitive, Obsidian-style)
    note_stems: set[str] = set()
    for md_file in vault.rglob("*.md"):
        rel = md_file.relative_to(vault)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if rel.name in IGNORE_FILES:
            continue
        note_stems.add(md_file.stem.lower())

    # Determine which files to scan
    if paths:
        files = [(vault / rel, rel) for rel in paths if (vault / rel).exists()]
    else:
        files = []
        for md_file in sorted(vault.rglob("*.md")):
            rel = md_file.relative_to(vault)
            if any(part in IGNORE_DIRS for part in rel.parts):
                continue
            if rel.name in IGNORE_FILES:
                continue
            files.append((md_file, str(rel).replace("\\", "/")))

    reports: list[NoteReport] = []
    for md_file, rel_str in files:
        content = md_file.read_text(errors="replace")
        links = _WIKILINK_RE.findall(content)
        broken = []
        for link in links:
            target = link.strip()
            if target.lower() not in note_stems:
                broken.append(target)
        if broken:
            # Deduplicate while preserving order
            seen: set[str] = set()
            unique_broken: list[str] = []
            for b in broken:
                if b.lower() not in seen:
                    seen.add(b.lower())
                    unique_broken.append(b)
            report = NoteReport(rel_path=rel_str)
            for target in unique_broken:
                report.issues.append(ClaimIssue(
                    claim=f"[[{target}]]",
                    verdict="broken link",
                    correction=f"[[{target}]] does not exist — this note may need updating",
                    citation=None,
                    severity="warning",
                ))
            reports.append(report)

    return reports


def _all_note_files(vault: Path) -> list[tuple[Path, str]]:
    from sync.vault import IGNORE_DIRS, IGNORE_FILES

    files = []
    for md_file in sorted(vault.rglob("*.md")):
        rel = md_file.relative_to(vault)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if rel.name in IGNORE_FILES:
            continue
        files.append((md_file, str(rel).replace("\\", "/")))
    return files


def _changed_check_candidates(
    vault: Path,
    *,
    state_path: str | Path,
) -> list[CheckCandidate]:
    from sync.index import init_db, state_db_path

    rows = {}
    db_path = state_db_path(state_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_db(conn)
        for row in conn.execute(
            """
            SELECT rel_path, committed_file_hash, pending_file_hash,
                   last_seen_file_hash
              FROM notes
            """
        ).fetchall():
            rows[row[0]] = {
                "committed": row[1] or "",
                "pending": row[2] or "",
                "last_seen": row[3] or "",
            }

    candidates = []
    for md_file, rel_str in _all_note_files(vault):
        content = md_file.read_text(errors="replace")
        content_hash = _sha256(content)
        known = rows.get(rel_str)
        if known and content_hash in {
            known["committed"],
            known["pending"],
            known["last_seen"],
        }:
            continue
        candidates.append(CheckCandidate(
            rel_path=rel_str,
            path=md_file,
            content=content,
            content_hash=content_hash,
        ))
    return candidates


def _specific_check_candidates(vault: Path, paths: list[str]) -> list[CheckCandidate]:
    candidates = []
    for rel in paths:
        full = vault / rel
        if not full.exists():
            print(f"  skipping {rel} (not found)")
            continue
        content = full.read_text(errors="replace")
        candidates.append(CheckCandidate(
            rel_path=rel,
            path=full,
            content=content,
            content_hash=_sha256(content),
        ))
    return candidates


def _mark_checked(
    candidates: list[CheckCandidate],
    reports: list[NoteReport],
    *,
    state_path: str | Path,
) -> None:
    from sync.vault import _resolve_deck
    from sync.index import init_db, state_db_path

    reports_by_path = {report.rel_path: report for report in reports}
    clean = [
        candidate for candidate in candidates
        if not reports_by_path.get(candidate.rel_path, NoteReport(candidate.rel_path)).issues
    ]
    if not clean:
        return

    now = _utc_now()
    db_path = state_db_path(state_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_db(conn)
        for candidate in clean:
            deck = _resolve_deck(candidate.rel_path, candidate.content)
            conn.execute(
                """
                INSERT INTO notes (
                    rel_path, deck, committed_file_hash, pending_file_hash,
                    last_seen_file_hash, last_processed
                )
                VALUES (?, ?, '', NULL, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    deck = excluded.deck,
                    last_seen_file_hash = excluded.last_seen_file_hash,
                    last_processed = excluded.last_processed
                """,
                (candidate.rel_path, deck, candidate.content_hash, now),
            )
        conn.commit()


def check_all_notes(
    vault_path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    config_path: str = "config.toml",
    state_path: str | Path | None = None,
    paths: list[str] | None = None,
) -> list[NoteReport]:
    """Check all (or specific) notes in the vault for factual errors.

    If paths is given, only check those vault-relative paths.
    Otherwise, check notes whose current content hash has not already been
    committed, proposed, or successfully checked.
    """
    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"Vault not found: {vault}")

    reports = []

    if paths:
        candidates = _specific_check_candidates(vault, paths)
    else:
        if state_path is None:
            from sync.config import load_app_config

            state_path = load_app_config().state_path
        candidates = _changed_check_candidates(vault, state_path=state_path)

    if not candidates:
        print("  no note content changes to check")
        return reports

    for candidate in candidates:
        report = check_note(
            candidate.rel_path,
            candidate.content,
            model=model,
            config_path=config_path,
        )
        reports.append(report)

    if state_path is not None:
        _mark_checked(candidates, reports, state_path=state_path)

    return reports
