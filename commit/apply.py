"""Brick 4 — parse approved diff files, backup Anki, apply, update state, log.

Flow:
  1. Parse New cards.md + Changed cards.md (Q/A/source format).
  2. Backup collection via AnkiConnect exportPackage — abort if it fails.
  3. Ensure 'Basic (tracked)' model exists; ensure target decks exist.
  4. For changed notes: delete old cards, add new ones.
  5. For new notes: add all cards.
  6. Update SQLite state with new card entries.
  7. Move applied entries to Anki sync log.md; trigger AnkiWeb sync.

Run standalone:  python -m commit.apply [vault_path] [state_path]
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from sync.config import load_app_config
from sync.index import (
    CardEntry,
    NoteEntry,
    SyncIndex,
    load_index,
    save_index,
    state_db_path,
)
from sync.state import (
    CardState,
    default_state_path,
    mark_cards_committed,
)

from .anki import (
    AnkiConnectError,
    add_note,
    create_deck,
    delete_notes,
    ensure_model,
    export_package,
    sync,
)


# ------------------------------------------------------------------
# Parsed card struct (shared by new + changed)
# ------------------------------------------------------------------

@dataclass
class ParsedCard:
    """A single card parsed from a diff file."""
    note_title: str       # e.g. "Processes" (from [[Processes]])
    deck: str
    question: str         # direct question about the target
    answer: str           # answer drawn from the proposition
    source: str           # verbatim original bullet


# ------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------

# Matches: ## ++ from [[NoteTitle]]      deck: SomeDeck
_HEADER_RE = re.compile(
    r"^##\s*\+\+\s*from\s*\[\[(.+?)\]\]\s+deck:\s*(.+)$"
)

# Matches: Q: ...
_Q_RE = re.compile(r"^Q:\s*(.+)$")

# Matches: A: ...
_A_RE = re.compile(r"^A:\s*(.+)$")

# Matches: source: "..."
_SOURCE_RE = re.compile(r'^source:\s*"(.+)"\s*$')


def parse_diff_cards(content: str) -> List[ParsedCard]:
    """Parse a diff file (New cards.md or Changed cards.md) into cards.

    Stops at ### audit sections (Pruned, Not self-contained, Skipped).
    """
    cards: List[ParsedCard] = []
    lines = content.split("\n")
    i = 0
    current_title = ""
    current_deck = ""

    while i < len(lines):
        line = lines[i]

        # Skip audit/comment sections
        if line.startswith("### ") or line.startswith("# "):
            if not line.startswith("## "):
                i += 1
                continue

        # Card header
        m = _HEADER_RE.match(line)
        if m:
            current_title = m.group(1)
            current_deck = m.group(2).strip()
            if "← pick" in current_deck:
                current_deck = current_deck.replace("← pick", "").strip()
            i += 1
            continue

        # Q line
        qm = _Q_RE.match(line)
        if qm and current_title:
            question = qm.group(1).strip()
            answer = ""
            source = ""

            # Look for A and source lines
            i += 1
            while i < len(lines):
                am = _A_RE.match(lines[i])
                if am:
                    answer = am.group(1).strip()
                    i += 1
                    continue
                sm = _SOURCE_RE.match(lines[i])
                if sm:
                    source = sm.group(1)
                    i += 1
                    break
                if lines[i].strip() == "":
                    i += 1
                    # If we already have answer, the blank line ends this card
                    if answer:
                        break
                    continue
                # Hit something unexpected — stop parsing this card
                break

            if question and answer:
                cards.append(ParsedCard(
                    note_title=current_title,
                    deck=current_deck,
                    question=question,
                    answer=answer,
                    source=source,
                ))
            continue

        i += 1

    return cards


# ------------------------------------------------------------------
# Content hash (stable identity: source + target)
# ------------------------------------------------------------------

def _content_hash(source: str, target: str) -> str:
    combined = source + "\n" + target
    return hashlib.sha256(combined.encode()).hexdigest()


def _card_state(
    card: ParsedCard,
    *,
    rel_path: str,
    content_hash: str,
) -> CardState:
    return CardState(
        note_rel_path=rel_path,
        note_title=card.note_title,
        deck=card.deck,
        question=card.question,
        answer=card.answer,
        source=card.source,
        content_hash=content_hash,
    )


# ------------------------------------------------------------------
# Resolve note title → rel_path in the index
# ------------------------------------------------------------------

def _resolve_rel_path(note_title: str, index: SyncIndex) -> Optional[str]:
    """Find the index entry whose filename stem matches note_title."""
    for rel_path in index.all_paths():
        if Path(rel_path).stem == note_title:
            return rel_path
    return None


def _find_rel_path_in_vault(note_title: str, vault: Path) -> Optional[str]:
    """Search the vault for a .md file matching note_title."""
    for md in vault.rglob("*.md"):
        if md.stem == note_title:
            return str(md.relative_to(vault)).replace("\\", "/")
    return None


# ------------------------------------------------------------------
# Main apply flow
# ------------------------------------------------------------------

def apply_commit(
    vault_path: str | Path,
    state_path: str | Path | None = None,
    *,
    skip_backup: bool = False,
    skip_sync: bool = False,
) -> None:
    """Full commit flow: parse → backup → apply → update state → log."""
    if state_path is None:
        state_path = load_app_config().state_path

    vault = Path(vault_path).resolve()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M")

    # Load card state
    index = load_index(state_path)

    # ------------------------------------------------------------------
    # 1. Parse diff files
    # ------------------------------------------------------------------
    new_file = vault / "New cards.md"
    changed_file = vault / "Changed cards.md"

    new_cards: List[ParsedCard] = []
    changed_cards: List[ParsedCard] = []

    if new_file.exists():
        new_cards = parse_diff_cards(new_file.read_text())
        print(f"  parsed {len(new_cards)} new card(s) from New cards.md")
    else:
        print("  New cards.md not found — skipping")

    if changed_file.exists():
        changed_cards = parse_diff_cards(changed_file.read_text())
        print(f"  parsed {len(changed_cards)} card(s) from Changed cards.md")
    else:
        print("  Changed cards.md not found — skipping")

    total = len(new_cards) + len(changed_cards)
    if total == 0:
        print("  nothing to commit")
        return

    # ------------------------------------------------------------------
    # 2. Backup
    # ------------------------------------------------------------------
    if not skip_backup:
        print("  backing up Anki collection ...")
        try:
            backup_path = export_package()
            backup_file = Path(backup_path)
            if not backup_file.exists() or backup_file.stat().st_size == 0:
                raise AnkiConnectError("Backup file is empty or missing")
            print(f"  backup saved: {backup_path}")
        except AnkiConnectError as exc:
            print(f"  BACKUP FAILED: {exc}")
            print("  aborting commit — fix the issue and retry")
            return

    # ------------------------------------------------------------------
    # 3. Ensure model + decks
    # ------------------------------------------------------------------
    ensure_model()

    needed_decks: set[str] = set()
    for c in new_cards:
        if c.deck:
            needed_decks.add(c.deck)
    for c in changed_cards:
        if c.deck:
            needed_decks.add(c.deck)

    for deck in needed_decks:
        create_deck(deck)

    # ------------------------------------------------------------------
    # 4. Apply
    # ------------------------------------------------------------------
    log_entries: List[str] = []
    log_entries.append(f"## Commit {ts}")
    log_entries.append("")
    committed_states: List[CardState] = []
    committed_note_ids: dict[str, int] = {}

    # --- Changed notes: delete ALL old cards for affected notes, then add new ---
    changed_note_titles = {c.note_title for c in changed_cards}
    for title in changed_note_titles:
        rel_path = _resolve_rel_path(title, index)
        if rel_path:
            note_entry = index.get_note(rel_path)
            if note_entry and note_entry.cards:
                old_ids = [c.anki_note_id for c in note_entry.cards]
                print(f"  deleting {len(old_ids)} old card(s) for [[{title}]]")
                try:
                    delete_notes(old_ids)
                except AnkiConnectError as exc:
                    print(f"    WARNING: delete failed: {exc}")
                for oid in old_ids:
                    log_entries.append(f"-- deleted card {oid} from [[{title}]]")
                note_entry.cards.clear()

    # Add changed cards
    for card in changed_cards:
        if not card.deck:
            print(f"  SKIPPING [[{card.note_title}]] — no deck")
            continue

        c_hash = _content_hash(card.source, card.answer)
        rel_path = _resolve_rel_path(card.note_title, index)
        source_note = rel_path or card.note_title

        try:
            note_id = add_note(
                card.deck, card.question, card.answer, source_note, c_hash,
            )
            print(f"  added card {note_id} → {card.deck} ({card.question[:50]}...)")
        except AnkiConnectError as exc:
            print(f"  WARNING: add failed: {exc}")
            continue

        # Update card state
        if rel_path:
            note_entry = index.get_note(rel_path)
            if note_entry is None:
                note_entry = NoteEntry(
                    committed_file_hash="",
                    last_processed=now.isoformat(),
                    deck=card.deck,
                )
                index.upsert_note(rel_path, note_entry)
            note_entry.cards.append(CardEntry(
                anki_note_id=note_id,
                concept_key=card.answer,
                content_hash=c_hash,
                front=card.question,
                source=card.source,
            ))
            note_entry.last_processed = now.isoformat()
            committed_states.append(
                _card_state(card, rel_path=rel_path, content_hash=c_hash)
            )
            committed_note_ids[c_hash] = note_id

        log_entries.append(
            f"++ added card {note_id} to [[{card.note_title}]] · deck: {card.deck}"
        )

    # --- New cards ---
    for card in new_cards:
        if not card.deck:
            print(f"  SKIPPING [[{card.note_title}]] — no deck (mark ← pick)")
            continue

        c_hash = _content_hash(card.source, card.answer)
        rel_path = (
            _resolve_rel_path(card.note_title, index)
            or _find_rel_path_in_vault(card.note_title, vault)
        )
        source_note = rel_path or card.note_title

        try:
            note_id = add_note(
                card.deck, card.question, card.answer, source_note, c_hash,
            )
            print(f"  added card {note_id} → {card.deck} ({card.question[:50]}...)")
        except AnkiConnectError as exc:
            print(f"  WARNING: add failed: {exc}")
            continue

        # Update card state
        if rel_path is None:
            rel_path = f"{card.deck}/{card.note_title}.md"

        note_entry = index.get_note(rel_path)
        if note_entry is None:
            note_entry = NoteEntry(
                committed_file_hash="",
                last_processed=now.isoformat(),
                deck=card.deck,
            )
            index.upsert_note(rel_path, note_entry)

        note_entry.cards.append(CardEntry(
            anki_note_id=note_id,
            concept_key=card.answer,
            content_hash=c_hash,
            front=card.question,
            source=card.source,
        ))
        note_entry.last_processed = now.isoformat()
        note_entry.deck = card.deck
        committed_states.append(
            _card_state(card, rel_path=rel_path, content_hash=c_hash)
        )
        committed_note_ids[c_hash] = note_id

        log_entries.append(
            f"++ added card {note_id} to [[{card.note_title}]] · deck: {card.deck}"
        )

    # ------------------------------------------------------------------
    # 5. Promote pending_file_hash → committed_file_hash for committed notes, then save
    # ------------------------------------------------------------------
    committed_paths = {c.note_rel_path for c in committed_states}
    for rel_path in committed_paths:
        note_entry = index.get_note(rel_path)
        if note_entry and note_entry.pending_file_hash:
            note_entry.committed_file_hash = note_entry.pending_file_hash
            note_entry.pending_file_hash = None

    if committed_states:
        committed_count = mark_cards_committed(
            default_state_path(state_path),
            committed_states,
            anki_note_ids=committed_note_ids,
        )
        print(f"  recorded {committed_count} committed card(s)")

    save_index(index, state_path)
    print(f"  updated {state_db_path(state_path)}")

    # ------------------------------------------------------------------
    # 6. Write log + clear diff files
    # ------------------------------------------------------------------
    log_entries.append("")
    log_text = "\n".join(log_entries)

    log_file = vault / "Anki sync log.md"
    if log_file.exists():
        existing = log_file.read_text()
        log_file.write_text(existing + "\n" + log_text)
    else:
        log_file.write_text(log_text)
    print(f"  appended to {log_file}")

    # Clear the diff files (entries have been applied)
    if new_file.exists():
        new_file.unlink()
        print("  removed New cards.md")
    if changed_file.exists():
        changed_file.unlink()
        print("  removed Changed cards.md")

    # ------------------------------------------------------------------
    # 7. Sync to AnkiWeb
    # ------------------------------------------------------------------
    if not skip_sync:
        try:
            sync()
            print("  AnkiWeb sync triggered")
        except AnkiConnectError as exc:
            print(f"  WARNING: sync failed: {exc}")

    print("  commit complete")


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    app_config = load_app_config()
    vault_dir = sys.argv[1] if len(sys.argv) > 1 else app_config.vault_path
    state_path = sys.argv[2] if len(sys.argv) > 2 else app_config.state_path

    print(f"Vault:  {vault_dir}")
    print(f"State:  {state_db_path(state_path)}")
    apply_commit(vault_dir, state_path)
