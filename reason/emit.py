"""Write New cards.md and Changed cards.md for the cloze-style card pipeline.

The format is the contract between the propose step and the commit parser.

Run standalone:  python -m reason.emit  (uses dummy data for testing)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .crosscheck import CardProposal, NoteProposals


def _deck_label(deck: str) -> str:
    if not deck:
        return "← pick"
    return deck


# ------------------------------------------------------------------
# New cards.md
# ------------------------------------------------------------------

def emit_new_cards(
    proposals: List[NoteProposals],
    vault_path: str | Path,
    *,
    timestamp: str | None = None,
) -> str:
    """Generate the content of New cards.md."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    new_notes = [p for p in proposals if p.is_new_note]
    n_notes = len(new_notes)
    total_cards = sum(len(p.proposals) for p in new_notes)

    lines = [
        "# New Cards — pending approval",
        f"# run {ts} · {total_cards} card(s) from {n_notes} note(s) · "
        "edit freely, then run `commit`",
        "",
    ]

    for np in new_notes:
        note_title = Path(np.rel_path).stem
        deck_str = _deck_label(np.deck)

        for proposal in np.proposals:
            lines.append(
                f"## ++ from [[{note_title}]]      deck: {deck_str}"
            )
            lines.append(f"Q: {proposal.question}")
            lines.append(f"A: {proposal.answer}")
            lines.append(f'source: "{proposal.source}"')
            lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Changed cards.md
# ------------------------------------------------------------------

def emit_changed_cards(
    proposals: List[NoteProposals],
    vault_path: str | Path,
    *,
    timestamp: str | None = None,
) -> str:
    """Generate the content of Changed cards.md.

    For now, changed notes are treated the same as new notes — all their
    generated cards are emitted as additions. The commit step handles
    diffing against the index to decide what to add/delete.
    """
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    changed_notes = [p for p in proposals if not p.is_new_note]
    total_cards = sum(len(p.proposals) for p in changed_notes)

    lines = [
        "# Changed Cards — pending approval",
        f"# run {ts} · {total_cards} card(s) from changed notes",
        "# old cards for these notes will be deleted and replaced",
        "",
    ]

    for np in changed_notes:
        note_title = Path(np.rel_path).stem
        deck_str = _deck_label(np.deck)

        for proposal in np.proposals:
            lines.append(
                f"## ++ from [[{note_title}]]      deck: {deck_str}"
            )
            lines.append(f"Q: {proposal.question}")
            lines.append(f"A: {proposal.answer}")
            lines.append(f'source: "{proposal.source}"')
            lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Write to vault
# ------------------------------------------------------------------

def write_diff_files(
    proposals: List[NoteProposals],
    vault_path: str | Path,
    *,
    timestamp: str | None = None,
) -> None:
    """Write New cards.md and Changed cards.md into the vault root."""
    vault = Path(vault_path)

    new_content = emit_new_cards(proposals, vault_path, timestamp=timestamp)
    changed_content = emit_changed_cards(proposals, vault_path, timestamp=timestamp)

    new_file = vault / "New cards.md"
    changed_file = vault / "Changed cards.md"

    new_file.write_text(new_content)
    changed_file.write_text(changed_content)

    print(f"  wrote {new_file}")
    print(f"  wrote {changed_file}")


# ------------------------------------------------------------------
# CLI smoke test with dummy data
# ------------------------------------------------------------------

if __name__ == "__main__":
    dummy = [
        NoteProposals(
            rel_path="Virtualisation/The Abstraction/Processes.md",
            deck="Virtualisation",
            is_new_note=True,
            proposals=[
                CardProposal(
                    target="Virtualisation",
                    question="What is time sharing a form of?",
                    answer="Time sharing is a form of virtualisation, used by the OS to share the CPU across multiple processes.",
                    source="The [[OS]] uses [[Virtualisation]] to share the [[CPU]] across multiple [[Processes]]",
                ),
                CardProposal(
                    target="CPU",
                    question="What does the OS share across multiple processes via virtualisation?",
                    answer="The OS shares the CPU across multiple processes using virtualisation (time sharing).",
                    source="The [[OS]] uses [[Virtualisation]] to share the [[CPU]] across multiple [[Processes]]",
                ),
            ],
        ),
    ]

    print("=== New cards.md ===")
    print(emit_new_cards(dummy, "/tmp/vault"))
    print("\n=== Changed cards.md ===")
    print(emit_changed_cards(dummy, "/tmp/vault"))
