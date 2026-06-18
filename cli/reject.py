"""Reject pending card proposals without touching Anki.

Usage:
    uv run reject [vault_path] [index_path]
"""
from __future__ import annotations

import sys
from pathlib import Path

from sync.index import SyncIndex, load_index, save_index
from sync.state import default_state_path, pending_note_paths, reject_pending


DIFF_FILES = ("New cards.md", "Changed cards.md")


def _proposed_index_paths(index: SyncIndex) -> set[str]:
    return {
        rel_path
        for rel_path, entry in index.notes.items()
        if entry.proposed_hash is not None
    }


def _clear_proposals(index: SyncIndex, rel_paths: set[str]) -> int:
    cleared = 0
    for rel_path in sorted(rel_paths):
        entry = index.get_note(rel_path)
        if entry is None:
            continue

        if not entry.cards and not entry.hash:
            index.remove_note(rel_path)
            cleared += 1
            continue

        if entry.proposed_hash is not None:
            entry.proposed_hash = None
            cleared += 1

    return cleared


def reject(
    vault_path: str | Path = "/Users/root1/Obsidian/quant",
    index_path: str | Path = "data/sync_index.json",
) -> None:
    """Reject all currently pending card proposals."""
    vault = Path(vault_path).resolve()
    index = load_index(index_path)
    state_path = default_state_path(index_path)

    rel_paths = pending_note_paths(state_path)
    if not rel_paths:
        rel_paths = _proposed_index_paths(index)

    rejected = reject_pending(state_path)
    cleared = _clear_proposals(index, rel_paths)
    save_index(index, index_path)

    removed = 0
    for name in DIFF_FILES:
        path = vault / name
        if path.exists():
            path.unlink()
            removed += 1
            print(f"  removed {path}")

    print(f"  rejected {rejected} pending card(s)")
    print(f"  cleared {cleared} proposed note marker(s)")
    print(f"  removed {removed} review file(s)")
    print("  reject complete")


def main() -> None:
    vault = sys.argv[1] if len(sys.argv) > 1 else "/Users/root1/Obsidian/quant"
    index = sys.argv[2] if len(sys.argv) > 2 else "data/sync_index.json"

    print("=== Anki Reject ===")
    reject(vault, index)


if __name__ == "__main__":
    main()
