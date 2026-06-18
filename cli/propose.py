"""Orchestrator — the nightly propose job.

Scans the vault, diffs against sync_index.json, generates cloze-style cards
from propositions with [[wikilink]] and ==highlight== targets, and writes
New cards.md + Changed cards.md into the vault.

Usage:
    uv run anki-propose [vault_path] [index_path] [books_config]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from reason.crosscheck import process_all_notes
from reason.emit import write_diff_files
from sync.index import NoteEntry, load_index, save_index
from sync.vault import scan_vault


def propose(
    vault_path: str | Path = "/Users/root1/Obsidian/quant",
    index_path: str | Path = "data/sync_index.json",
    config_path: str = "books.yaml",
) -> None:
    """Run the full propose pipeline."""
    vault_path = Path(vault_path).resolve()
    print(f"Vault:  {vault_path}")
    print(f"Index:  {index_path}")
    print(f"Books:  {config_path}")

    # Brick 2 — scan vault
    index = load_index(index_path)
    diff = scan_vault(vault_path, index)
    print(f"  {diff.summary()}")

    if not diff.changed:
        print("  nothing changed — skipping")
        return

    # Generate cards from propositions
    print(f"  processing {len(diff.changed)} note(s) ...")
    proposals = process_all_notes(diff, index, config_path=config_path)

    if not proposals:
        print("  no cards to propose")
        return

    total_cards = sum(len(p.proposals) for p in proposals)
    print(f"  {total_cards} card(s) proposed across {len(proposals)} note(s)")

    # Write diff files
    write_diff_files(proposals, vault_path)

    # Mark notes as proposed (don't update hash — that happens at commit)
    now = datetime.now(timezone.utc).isoformat()
    for note in diff.changed:
        entry = index.get_note(note.rel_path)
        if entry is None:
            entry = NoteEntry(hash="", last_processed=now, deck=note.deck,
                              proposed_hash=note.content_hash)
            index.upsert_note(note.rel_path, entry)
        else:
            entry.proposed_hash = note.content_hash
            entry.last_processed = now

    save_index(index, index_path)
    print(f"  updated {index_path}")
    print("  propose complete — review the diff files, then run: uv run anki-commit")


def main() -> None:
    v = sys.argv[1] if len(sys.argv) > 1 else "/Users/root1/Obsidian/quant"
    i = sys.argv[2] if len(sys.argv) > 2 else "data/sync_index.json"
    c = sys.argv[3] if len(sys.argv) > 3 else "books.yaml"
    propose(v, i, c)


if __name__ == "__main__":
    main()
