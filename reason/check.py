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

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from rag.query import retrieve
from .llm import chat_json, DEFAULT_MODEL


# ------------------------------------------------------------------
# Data types
# ------------------------------------------------------------------

@dataclass
class ClaimIssue:
    """A single factual claim in a note that has a problem."""
    claim: str                # what the student wrote
    verdict: str              # "wrong", "imprecise", "unsupported"
    correction: str           # what the textbook actually says
    citation: Optional[str]   # e.g. "OSTEP · vm-tlbs · p.7"
    severity: str             # "error" or "warning"


@dataclass
class NoteReport:
    """All issues found in a single note."""
    rel_path: str
    issues: List[ClaimIssue] = field(default_factory=list)


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
- "imprecise" — partially right but uses wrong terminology, conflates concepts, \
or overstates/understates something
- "wrong" — the textbook directly contradicts the claim
- "unsupported" — the passages don't cover this topic (not enough evidence)

Reply with a JSON object:
{
  "verdict": "correct" | "imprecise" | "wrong" | "unsupported",
  "correction": "<what the textbook actually says, or null if correct>",
  "explanation": "<one sentence explaining the issue, or null if correct>",
  "best_citation": "<the citation string of the most relevant passage, or null>"
}

Rules:
- Be strict about factual accuracy. If the student says "time sharing shares \
space on the CPU" but the textbook says time sharing shares CPU *time*, that \
is WRONG — not imprecise.
- For "imprecise": the core idea is right but the wording is misleading or \
sloppy in a way that would lose marks or cause confusion.
- For "unsupported": only use this when the passages genuinely don't cover the \
topic. Don't use it as a cop-out when the claim is clearly wrong.
- Use ONLY the provided passages as evidence.\
"""


# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------

def extract_claims(
    note_content: str,
    *,
    model: str = DEFAULT_MODEL,
) -> List[Dict]:
    """LLM extracts factual claims from a note."""
    result = chat_json([
        {"role": "system", "content": EXTRACT_CLAIMS_SYSTEM},
        {"role": "user", "content": note_content},
    ], model=model)
    return result.get("claims", [])


def check_claim(
    claim: str,
    passages: List[Dict],
    *,
    model: str = DEFAULT_MODEL,
) -> Dict:
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


def check_note(
    rel_path: str,
    content: str,
    *,
    model: str = DEFAULT_MODEL,
    config_path: str = "books.yaml",
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
        claim_text = claim_data["claim"]
        query = claim_data.get("query", claim_text)

        passages = retrieve(query, k=5, config_path=config_path)
        result = check_claim(claim_text, passages, model=model)

        verdict = result.get("verdict", "correct")
        if verdict == "correct":
            continue  # no issue

        severity = "error" if verdict == "wrong" else "warning"
        issue = ClaimIssue(
            claim=claim_text,
            verdict=verdict,
            correction=result.get("correction") or result.get("explanation", ""),
            citation=result.get("best_citation"),
            severity=severity,
        )
        report.issues.append(issue)

    return report


def check_all_notes(
    vault_path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    config_path: str = "books.yaml",
    paths: Optional[List[str]] = None,
) -> List[NoteReport]:
    """Check all (or specific) notes in the vault for factual errors.

    If paths is given, only check those vault-relative paths.
    Otherwise, check all .md files.
    """
    from sync.vault import IGNORE_DIRS, IGNORE_FILES

    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"Vault not found: {vault}")

    reports = []

    if paths:
        # Check specific notes
        for rel in paths:
            full = vault / rel
            if not full.exists():
                print(f"  skipping {rel} (not found)")
                continue
            content = full.read_text(errors="replace")
            report = check_note(rel, content, model=model, config_path=config_path)
            reports.append(report)
    else:
        # Check all notes
        for md_file in sorted(vault.rglob("*.md")):
            rel = md_file.relative_to(vault)
            if any(part in IGNORE_DIRS for part in rel.parts):
                continue
            if rel.name in IGNORE_FILES:
                continue
            rel_str = str(rel).replace("\\", "/")
            content = md_file.read_text(errors="replace")
            report = check_note(
                rel_str, content, model=model, config_path=config_path
            )
            reports.append(report)

    return reports
