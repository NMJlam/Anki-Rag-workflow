"""Thin AnkiConnect wrapper — POST to localhost:8765.

All actions use AnkiConnect v6 protocol:
  {"action": "...", "version": 6, "params": {...}}

Run standalone:  python -m commit.anki  (lists decks + models as a smoke test)
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

ANKI_CONNECT_URL = "http://localhost:8765"

# The custom note type we manage (§6).
MODEL_NAME = "Basic (tracked)"
MODEL_FIELDS = ["Front", "Back", "SourceNote", "ContentHash"]
MODEL_CARD_TEMPLATES = [
    {
        "Name": "Card 1",
        "Front": "{{Front}}",
        "Back": "{{FrontSide}}<hr id=answer>{{Back}}",
    }
]


# ------------------------------------------------------------------
# Low-level request
# ------------------------------------------------------------------

class AnkiConnectError(Exception):
    pass


def _request(action: str, **params: Any) -> Any:
    """Send a single AnkiConnect request and return the result."""
    payload = json.dumps({
        "action": action,
        "version": 6,
        "params": params,
    }).encode("utf-8")

    req = urllib.request.Request(
        ANKI_CONNECT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except Exception as exc:
        raise AnkiConnectError(
            f"Cannot reach AnkiConnect at {ANKI_CONNECT_URL}. "
            "Is Anki running with AnkiConnect installed?"
        ) from exc

    if body.get("error"):
        raise AnkiConnectError(f"AnkiConnect error: {body['error']}")
    return body.get("result")


# ------------------------------------------------------------------
# High-level helpers
# ------------------------------------------------------------------

def deck_names() -> list[str]:
    return _request("deckNames")


def create_deck(deck: str) -> int:
    """Create a deck (idempotent — returns deck ID)."""
    return _request("createDeck", deck=deck)


def model_names() -> list[str]:
    return _request("modelNames")


def ensure_model() -> None:
    """Create the 'Basic (tracked)' note type if it doesn't exist."""
    existing = model_names()
    if MODEL_NAME in existing:
        return
    _request(
        "createModel",
        modelName=MODEL_NAME,
        inOrderFields=MODEL_FIELDS,
        cardTemplates=MODEL_CARD_TEMPLATES,
    )
    print(f"  created Anki model '{MODEL_NAME}'")


def add_note(
    deck: str,
    front: str,
    back: str,
    source_note: str,
    content_hash: str,
) -> int:
    """Add a single note. Returns the new Anki note ID.

    allowDuplicate=false so re-running won't double-add.
    """
    note_id = _request(
        "addNote",
        note={
            "deckName": deck,
            "modelName": MODEL_NAME,
            "fields": {
                "Front": front,
                "Back": back,
                "SourceNote": source_note,
                "ContentHash": content_hash,
            },
            "options": {
                "allowDuplicate": False,
                "duplicateScope": "deck",
                "duplicateScopeOptions": {
                    "deckName": deck,
                    "checkChildren": False,
                },
            },
        },
    )
    return note_id


def delete_notes(note_ids: list[int]) -> None:
    """Delete notes by their Anki note IDs."""
    if not note_ids:
        return
    _request("deleteNotes", notes=note_ids)


def find_notes(query: str) -> list[int]:
    """Find note IDs matching an Anki search query."""
    return _request("findNotes", query=query)


def notes_info(note_ids: list[int]) -> list[dict]:
    """Get full info for a list of note IDs."""
    return _request("notesInfo", notes=note_ids)


def cards_info(card_ids: list[int]) -> list[dict]:
    """Get full info for card IDs."""
    if not card_ids:
        return []
    return _request("cardsInfo", cards=card_ids)


def export_package(
    deck: str = "Default",
    path: str | None = None,
    include_scheduling: bool = True,
) -> str:
    """Export the collection as an .apkg backup.

    Returns the path where the backup was written.
    """
    if path is None:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(Path.home() / f"anki_backup_{ts}.apkg")

    _request(
        "exportPackage",
        deck=deck,
        path=path,
        includeSched=include_scheduling,
    )
    return path


def sync() -> None:
    """Trigger AnkiWeb sync."""
    _request("sync")


# ------------------------------------------------------------------
# CLI smoke test
# ------------------------------------------------------------------

if __name__ == "__main__":
    print("AnkiConnect smoke test")
    print(f"  Decks:  {deck_names()}")
    print(f"  Models: {model_names()}")
    print("  OK")
