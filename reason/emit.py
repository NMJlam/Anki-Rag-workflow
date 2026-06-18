"""Write New/Changed/Deleted cards.md for the cloze-style card pipeline.

The format is the contract between the propose step and the commit parser.

Run standalone:  python -m reason.emit  (uses dummy data for testing)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from sync.index import NoteEntry

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
    """Generate the content of Changed cards.md."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    changed_notes = [p for p in proposals if not p.is_new_note]
    total_cards = sum(len(p.proposals) for p in changed_notes)

    lines = [
        "# Changed Cards — pending approval",
        f"# run {ts} · {total_cards} card(s) from changed notes",
        "# unchanged cards are omitted",
        "# replacement callouts show the old card that will be deleted",
        "",
    ]

    for np in changed_notes:
        note_title = Path(np.rel_path).stem
        deck_str = _deck_label(np.deck)

        for proposal in np.proposals:
            if proposal.action == "replace" and proposal.replaces_anki_note_id:
                lines.append("> [!warning] Replace existing card")
                lines.append(
                    f"> -- card {proposal.replaces_anki_note_id} from "
                    f"[[{note_title}]]      deck: {deck_str}"
                )
                if proposal.replaces_front:
                    lines.append(f'> old Q: "{proposal.replaces_front}"')
                if proposal.replaces_answer:
                    lines.append(f'> old A: "{proposal.replaces_answer}"')
                lines.append(">")
                lines.append(
                    f"## ++ replace from [[{note_title}]]      deck: {deck_str}"
                )
            else:
                lines.append(
                    f"## ++ add from [[{note_title}]]      deck: {deck_str}"
                )
            lines.append(f"Q: {proposal.question}")
            lines.append(f"A: {proposal.answer}")
            lines.append(f'source: "{proposal.source}"')
            lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Deleted cards.md
# ------------------------------------------------------------------

def emit_deleted_cards(
    deleted_notes: List[Tuple[str, NoteEntry]],
    *,
    timestamp: str | None = None,
) -> str:
    """Generate the content of Deleted cards.md."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    total_cards = sum(len(entry.cards) for _, entry in deleted_notes)

    lines = [
        "# Deleted Cards — pending approval",
        f"# run {ts} · {total_cards} card(s) from {len(deleted_notes)} deleted note(s) · "
        "remove entries to keep specific cards, then run `commit`",
        "",
    ]

    for rel_path, entry in deleted_notes:
        note_title = Path(rel_path).stem
        deck_str = entry.deck or "unknown"
        for card in entry.cards:
            lines.append(
                f"## -- delete card {card.anki_note_id} from "
                f"[[{note_title}]]      deck: {deck_str}"
            )
            lines.append(f"Q: {card.front}")
            lines.append(f"A: {card.answer}")
            lines.append("")

    return "\n".join(lines)


def write_deleted_file(
    deleted_notes: List[Tuple[str, NoteEntry]],
    vault_path: str | Path,
    *,
    timestamp: str | None = None,
) -> None:
    """Write Deleted cards.md into the vault root."""
    content = emit_deleted_cards(deleted_notes, timestamp=timestamp)
    deleted_file = Path(vault_path) / "Deleted cards.md"
    deleted_file.write_text(content)
    print(f"  wrote {deleted_file}")


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

    new_notes = [p for p in proposals if p.is_new_note and p.proposals]
    changed_notes = [p for p in proposals if not p.is_new_note and p.proposals]

    new_file = vault / "New cards.md"
    changed_file = vault / "Changed cards.md"

    if new_notes:
        new_file.write_text(new_content)
        print(f"  wrote {new_file}")
    elif new_file.exists():
        new_file.unlink()

    if changed_notes:
        changed_file.write_text(changed_content)
        print(f"  wrote {changed_file}")
    elif changed_file.exists():
        changed_file.unlink()


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
