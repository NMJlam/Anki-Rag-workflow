"""Read/write sync_index.json — the authoritative note→card mapping.

Schema (version 1):
{
  "version": 1,
  "notes": {
    "Virtualisation/The Abstraction/Processes.md": {
      "hash": "<sha256>",
      "last_processed": "2026-06-18T03:00:00Z",
      "deck": "Virtualisation",
      "cards": [
        {
          "anki_note_id": 123,
          "concept_key": "process-definition",
          "content_hash": "<sha256 of Front+Back>",
          "front": "What is a process?"
        }
      ]
    }
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CardEntry:
    anki_note_id: int
    concept_key: str          # the target (wikilink or highlight)
    content_hash: str         # sha256(source + target) — stable identity
    front: str                # direct question about the target
    source: str = ""          # verbatim original bullet


@dataclass
class NoteEntry:
    hash: str                          # confirmed (committed) content hash
    last_processed: str
    deck: str
    cards: List[CardEntry] = field(default_factory=list)
    proposed_hash: Optional[str] = None  # set by propose, cleared by commit


@dataclass
class SyncIndex:
    version: int = 1
    notes: Dict[str, NoteEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def get_note(self, rel_path: str) -> Optional[NoteEntry]:
        return self.notes.get(rel_path)

    def has_note(self, rel_path: str) -> bool:
        return rel_path in self.notes

    def all_paths(self) -> set[str]:
        return set(self.notes.keys())

    # ------------------------------------------------------------------
    # Mutation helpers (used by later bricks)
    # ------------------------------------------------------------------

    def upsert_note(self, rel_path: str, entry: NoteEntry) -> None:
        self.notes[rel_path] = entry

    def remove_note(self, rel_path: str) -> Optional[NoteEntry]:
        return self.notes.pop(rel_path, None)


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------

def _note_to_dict(entry: NoteEntry) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "hash": entry.hash,
        "last_processed": entry.last_processed,
        "deck": entry.deck,
        "cards": [
            {
                "anki_note_id": c.anki_note_id,
                "concept_key": c.concept_key,
                "content_hash": c.content_hash,
                "front": c.front,
                "source": c.source,
            }
            for c in entry.cards
        ],
    }
    if entry.proposed_hash is not None:
        d["proposed_hash"] = entry.proposed_hash
    return d


def _dict_to_note(d: Dict[str, Any]) -> NoteEntry:
    return NoteEntry(
        hash=d["hash"],
        last_processed=d["last_processed"],
        deck=d["deck"],
        cards=[
            CardEntry(
                anki_note_id=c["anki_note_id"],
                concept_key=c["concept_key"],
                content_hash=c["content_hash"],
                front=c["front"],
                source=c.get("source", ""),
            )
            for c in d.get("cards", [])
        ],
        proposed_hash=d.get("proposed_hash"),
    )


def load_index(path: str | Path) -> SyncIndex:
    """Load sync_index.json.  Returns an empty index if the file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return SyncIndex()
    data = json.loads(p.read_text())
    return SyncIndex(
        version=data.get("version", 1),
        notes={k: _dict_to_note(v) for k, v in data.get("notes", {}).items()},
    )


def save_index(index: SyncIndex, path: str | Path) -> None:
    """Persist sync_index.json (pretty-printed for easy diffing)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": index.version,
        "notes": {k: _note_to_dict(v) for k, v in index.notes.items()},
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
