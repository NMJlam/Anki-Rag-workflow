"""Orchestrator — the nightly propose job.

Scans the vault, diffs against SQLite card state, generates cloze-style cards
from propositions with [[wikilink]] and ==highlight== targets, and writes
New cards.md + Changed cards.md into the vault.

Usage:
    uv run anki-propose [vault_path] [state_path] [config_path]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from cli.check_markers import CALLOUT_MARKER
from reason.crosscheck import process_all_notes
from reason.emit import write_diff_files
from sync.anki_source import AnkiConnectError, refresh_committed_cards_from_anki
from sync.config import load_app_config
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
    vault_path: str | Path | None = None,
    state_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> None:
    """Run the full propose pipeline."""
    app_config = load_app_config()
    vault_path = vault_path or app_config.vault_path
    state_path = state_path or app_config.state_path
    config_path = config_path or app_config.books_config

    vault_path = Path(vault_path).resolve()
    print(f"Vault:  {vault_path}")
    print(f"State:  {state_db_path(state_path)}")
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

    # Treat Anki as authoritative for committed tracked cards. SQLite remains
    # the local cache for note hashes and pending proposal state.
    state_exists = Path(state_path).exists()
    try:
        imported = refresh_committed_cards_from_anki(
            state_path,
            vault_path=vault_path,
        )
        print(f"  synced {imported} committed tracked card(s) from Anki")
    except AnkiConnectError as exc:
        if not state_exists:
            print(f"  cannot sync committed cards from Anki: {exc}")
            print("  aborting because no local card state exists yet")
            print("  open Anki with AnkiConnect installed, then retry")
            return
        print(f"  WARNING: could not sync committed cards from Anki: {exc}")
        print("  continuing with existing local card-state cache")

    # Brick 2 — scan vault
    index = load_index(state_path)
    diff = scan_vault(vault_path, index)
    print(f"  {diff.summary()}")

    if not diff.changed:
        print("  nothing changed — skipping")
        return

    # Generate cards from propositions
    print(f"  processing {len(diff.changed)} note(s) ...")
    proposals = process_all_notes(diff, index, config_path=config_path)

    total_cards = sum(len(p.proposals) for p in proposals)
    now = datetime.now(timezone.utc).isoformat()

    if total_cards == 0:
        print("  no card-impact changes to propose")
        for note in diff.changed:
            entry = index.get_note(note.rel_path)
            if entry is None:
                entry = NoteEntry(
                    committed_file_hash=note.content_hash,
                    last_processed=now,
                    deck=note.deck,
                    pending_file_hash=None,
                )
                index.upsert_note(note.rel_path, entry)
            else:
                entry.committed_file_hash = note.content_hash
                entry.pending_file_hash = None
                entry.last_processed = now
                entry.deck = note.deck
        save_index(index, state_path)
        print(f"  updated {state_db_path(state_path)}")
        return

    notes_with_cards = sum(1 for proposal in proposals if proposal.proposals)
    print(f"  {total_cards} card(s) proposed across {notes_with_cards} note(s)")

    # Write diff files
    write_diff_files(proposals, vault_path)
    recorded = record_proposals(default_state_path(state_path), proposals)
    print(f"  recorded {recorded} pending card(s)")

    # Mark notes as proposed (don't update hash — that happens at commit)
    proposal_paths = {
        proposal.rel_path for proposal in proposals
        if proposal.proposals
    }
    for note in diff.changed:
        entry = index.get_note(note.rel_path)
        if entry is None:
            committed_file_hash = (
                "" if note.rel_path in proposal_paths else note.content_hash
            )
            pending_file_hash = (
                note.content_hash if note.rel_path in proposal_paths else None
            )
            entry = NoteEntry(
                committed_file_hash=committed_file_hash,
                last_processed=now,
                deck=note.deck,
                pending_file_hash=pending_file_hash,
            )
            index.upsert_note(note.rel_path, entry)
        else:
            if note.rel_path in proposal_paths:
                entry.pending_file_hash = note.content_hash
            else:
                entry.committed_file_hash = note.content_hash
                entry.pending_file_hash = None
            entry.last_processed = now
            entry.deck = note.deck

    save_index(index, state_path)
    print(f"  updated {state_db_path(state_path)}")
    print("  propose complete — review the diff files, then run: uv run anki-commit")


def main() -> None:
    app_config = load_app_config()
    v = sys.argv[1] if len(sys.argv) > 1 else app_config.vault_path
    s = sys.argv[2] if len(sys.argv) > 2 else app_config.state_path
    c = sys.argv[3] if len(sys.argv) > 3 else app_config.books_config
    propose(v, s, c)


if __name__ == "__main__":
    main()
