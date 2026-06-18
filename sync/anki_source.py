"""Import committed tracked cards from Anki into the local state cache."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from commit.anki import (
    MODEL_NAME,
    AnkiConnectError,
    cards_info,
    find_notes,
    model_names,
    notes_info,
)

from .index import CardEntry, NoteEntry, SyncIndex, load_index, save_index


def _field_value(fields: dict[str, Any], name: str) -> str:
    field = fields.get(name, "")
    if isinstance(field, dict):
        return str(field.get("value", ""))
    return str(field)


def _deck_from_source(source_note: str) -> str:
    parts = Path(source_note).parts
    if len(parts) > 1:
        return parts[0]
    return ""


def _resolve_source_note(source_note: str, vault_path: str | Path | None) -> str:
    source_note = source_note.strip()
    if not source_note or vault_path is None:
        return source_note

    vault = Path(vault_path)
    direct = vault / source_note
    if direct.exists():
        return source_note.replace("\\", "/")

    source_path = Path(source_note)
    if source_path.suffix == ".md":
        target_stem = source_path.stem
    else:
        target_stem = source_note

    matches = [
        str(md.relative_to(vault)).replace("\\", "/")
        for md in vault.rglob("*.md")
        if md.stem == target_stem
    ]
    if len(matches) == 1:
        return matches[0]
    return source_note.replace("\\", "/")


def _deck_by_note_id(note_infos: list[dict[str, Any]]) -> dict[int, str]:
    card_to_note: dict[int, int] = {}
    for info in note_infos:
        note_id = int(info.get("noteId"))
        for card_id in info.get("cards", []):
            card_to_note[int(card_id)] = note_id

    result: dict[int, str] = {}
    for info in cards_info(list(card_to_note)):
        card_id = int(info.get("cardId"))
        note_id = card_to_note.get(card_id)
        deck = str(info.get("deckName", ""))
        if note_id is not None and deck and note_id not in result:
            result[note_id] = deck
    return result


def _front_back_from_fields(fields: dict[str, Any]) -> tuple[str, str]:
    if "Front" in fields and "Back" in fields:
        return _field_value(fields, "Front"), _field_value(fields, "Back")

    values = [_field_value(fields, name) for name in fields]
    front = values[0] if values else ""
    back = values[1] if len(values) > 1 else ""
    return front, back


def _quote_anki_search(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def regular_cards_for_deck(deck: str) -> list[dict[str, Any]]:
    """Return non-tracked Anki notes from a deck as semantic match candidates."""
    if not deck:
        return []

    query = f"deck:{_quote_anki_search(deck)} -note:{_quote_anki_search(MODEL_NAME)}"
    note_ids = find_notes(query)
    if not note_ids:
        return []

    cards = []
    for info in notes_info(note_ids):
        fields = info.get("fields", {})
        front, answer = _front_back_from_fields(fields)
        if not front or not answer:
            continue
        cards.append({
            "anki_note_id": int(info.get("noteId")),
            "concept_key": answer,
            "content_hash": "",
            "front": front,
            "answer": answer,
            "source": "",
            "tracked": False,
        })
    return cards


def committed_index_from_anki(
    *,
    vault_path: str | Path | None = None,
) -> SyncIndex:
    """Build an index containing committed cards from Anki's tracked note type."""
    if MODEL_NAME not in model_names():
        return SyncIndex()

    note_ids = find_notes(f'note:"{MODEL_NAME}"')
    if not note_ids:
        return SyncIndex()

    note_infos = notes_info(note_ids)
    decks = _deck_by_note_id(note_infos)
    index = SyncIndex()

    for info in note_infos:
        note_id = int(info.get("noteId"))
        fields = info.get("fields", {})
        front = _field_value(fields, "Front")
        answer = _field_value(fields, "Back")
        source_note = _field_value(fields, "SourceNote")
        content_hash = _field_value(fields, "ContentHash")
        if not source_note or not content_hash:
            continue

        rel_path = _resolve_source_note(source_note, vault_path)
        deck = decks.get(note_id) or _deck_from_source(rel_path)

        entry = index.get_note(rel_path)
        if entry is None:
            entry = NoteEntry(
                committed_file_hash="",
                last_processed="",
                deck=deck,
            )
            index.upsert_note(rel_path, entry)
        elif deck:
            entry.deck = deck

        entry.cards.append(CardEntry(
            anki_note_id=note_id,
            concept_key=answer,
            content_hash=content_hash,
            front=front,
            answer=answer,
            source=source_note,
        ))

    return index


def refresh_committed_cards_from_anki(
    state_path: str | Path,
    *,
    vault_path: str | Path | None = None,
) -> int:
    """Replace local committed card cache with Anki's tracked cards.

    Local note hashes and pending proposals are preserved.
    """
    local = load_index(state_path)
    anki_index = committed_index_from_anki(vault_path=vault_path)

    for rel_path, anki_entry in anki_index.notes.items():
        local_entry = local.get_note(rel_path)
        if local_entry is None:
            local.upsert_note(rel_path, anki_entry)
            continue

        local_entry.cards = anki_entry.cards
        if anki_entry.deck:
            local_entry.deck = anki_entry.deck

    anki_paths = set(anki_index.notes)
    for rel_path, local_entry in list(local.notes.items()):
        if rel_path not in anki_paths:
            local_entry.cards = []

    save_index(local, state_path)
    return sum(len(entry.cards) for entry in anki_index.notes.values())


__all__ = [
    "AnkiConnectError",
    "committed_index_from_anki",
    "regular_cards_for_deck",
    "refresh_committed_cards_from_anki",
]
