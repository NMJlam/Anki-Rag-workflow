"""Orchestrator — the nightly propose job.

Scans the vault, diffs against SQLite card state, generates cloze-style cards
from propositions with [[wikilink]] and ==highlight== targets, and writes
New cards.md + Changed cards.md into the vault.

Usage:
    uv run anki-propose [vault_path] [index_path] [books_config]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from cli.check_markers import CALLOUT_MARKER
from reason.crosscheck import process_all_notes
from reason.emit import write_diff_files
from sync.index import NoteEntry, load_index, save_index, state_db_path
from sync.state import default_state_path, record_proposals
from sync.vault import IGNORE_DIRS, IGNORE_FILES, scan_vault


def find_unresolved_check_notes(vault_path: str | Path) -> list[str]:
    """Return notes that still contain unresolved anki-check callouts."""
    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"Vault not found: {vault}")

    unresolved = []
    for md_file in sorted(vault.rglob("*.md")):
        rel = md_file.relative_to(vault)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if rel.name in IGNORE_FILES:
            continue
        if CALLOUT_MARKER in md_file.read_text(errors="replace"):
            unresolved.append(str(rel).replace("\\", "/"))

    return unresolved


def propose(
    vault_path: str | Path = "/Users/root1/Obsidian/quant",
    index_path: str | Path = "data/sync_index.json",
    config_path: str = "books.yaml",
) -> None:
    """Run the full propose pipeline."""
    vault_path = Path(vault_path).resolve()
    print(f"Vault:  {vault_path}")
    print(f"State:  {state_db_path(index_path)}")
    print(f"Books:  {config_path}")

    unresolved = find_unresolved_check_notes(vault_path)
    if unresolved:
        print(
            "  unresolved anki-check error/warning callout(s) found — "
            "resolve them before proposing cards"
        )
        for rel_path in unresolved:
            print(f"  - {rel_path}")
        return

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
    recorded = record_proposals(default_state_path(index_path), proposals)
    print(f"  recorded {recorded} pending card(s)")

    # Mark notes as proposed (don't update hash — that happens at commit)
    now = datetime.now(timezone.utc).isoformat()
    for note in diff.changed:
        entry = index.get_note(note.rel_path)
        if entry is None:
            entry = NoteEntry(
                committed_file_hash="",
                last_processed=now,
                deck=note.deck,
                pending_file_hash=note.content_hash,
            )
            index.upsert_note(note.rel_path, entry)
        else:
            entry.pending_file_hash = note.content_hash
            entry.last_processed = now

    save_index(index, index_path)
    print(f"  updated {state_db_path(index_path)}")
    print("  propose complete — review the diff files, then run: uv run anki-commit")


def main() -> None:
    v = sys.argv[1] if len(sys.argv) > 1 else "/Users/root1/Obsidian/quant"
    i = sys.argv[2] if len(sys.argv) > 2 else "data/sync_index.json"
    c = sys.argv[3] if len(sys.argv) > 3 else "books.yaml"
    propose(v, i, c)


if __name__ == "__main__":
    main()
