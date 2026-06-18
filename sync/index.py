"""SQLite-backed note/card state.

The public API intentionally keeps the old SyncIndex shape so callers can ask
"what cards are currently committed for this note?" without knowing the storage
details. ``card_state.sqlite`` is the source of truth.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 2


@dataclass
class CardEntry:
    anki_note_id: int
    concept_key: str
    content_hash: str
    front: str
    answer: str = ""
    source: str = ""


@dataclass
class NoteEntry:
    committed_file_hash: str
    last_processed: str
    deck: str
    cards: List[CardEntry] = field(default_factory=list)
    pending_file_hash: Optional[str] = None

    @property
    def hash(self) -> str:
        return self.committed_file_hash

    @hash.setter
    def hash(self, value: str) -> None:
        self.committed_file_hash = value

    @property
    def proposed_hash(self) -> Optional[str]:
        return self.pending_file_hash

    @proposed_hash.setter
    def proposed_hash(self, value: Optional[str]) -> None:
        self.pending_file_hash = value


@dataclass
class SyncIndex:
    version: int = SCHEMA_VERSION
    notes: Dict[str, NoteEntry] = field(default_factory=dict)

    def get_note(self, rel_path: str) -> Optional[NoteEntry]:
        return self.notes.get(rel_path)

    def has_note(self, rel_path: str) -> bool:
        return rel_path in self.notes

    def all_paths(self) -> set[str]:
        return set(self.notes.keys())

    def upsert_note(self, rel_path: str, entry: NoteEntry) -> None:
        self.notes[rel_path] = entry

    def remove_note(self, rel_path: str) -> Optional[NoteEntry]:
        return self.notes.pop(rel_path, None)


def state_db_path(state_path: str | Path) -> Path:
    return Path(state_path)


def _connect(state_path: str | Path) -> sqlite3.Connection:
    path = state_db_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            rel_path TEXT PRIMARY KEY,
            deck TEXT NOT NULL,
            committed_file_hash TEXT NOT NULL DEFAULT '',
            pending_file_hash TEXT,
            last_seen_file_hash TEXT,
            last_processed TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            note_rel_path TEXT NOT NULL,
            note_title TEXT NOT NULL,
            deck TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            card_content_hash TEXT NOT NULL,
            concept_key TEXT NOT NULL DEFAULT '',
            front TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('proposed', 'committed', 'rejected')),
            proposed_at TEXT NOT NULL,
            committed_at TEXT,
            rejected_at TEXT,
            anki_note_id INTEGER,
            FOREIGN KEY(note_rel_path) REFERENCES notes(rel_path) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cards_status
        ON cards(status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cards_note_status
        ON cards(note_rel_path, status)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_open_proposal
        ON cards(note_rel_path, card_content_hash)
        WHERE status = 'proposed'
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    _migrate_legacy_card_state(conn)
    conn.commit()


def _migrate_legacy_card_state(conn: sqlite3.Connection) -> None:
    already_migrated = conn.execute(
        "SELECT value FROM meta WHERE key = 'legacy_card_state_migrated'"
    ).fetchone()
    if already_migrated:
        return

    legacy_table = conn.execute(
        """
        SELECT name FROM sqlite_master
         WHERE type = 'table'
           AND name = 'card_state'
        """
    ).fetchone()
    if not legacy_table:
        return

    rows = conn.execute(
        """
        SELECT run_id, note_rel_path, note_title, deck, question, answer, source,
               content_hash, status, proposed_at, resolved_at, anki_note_id
          FROM card_state
        """
    ).fetchall()
    for (
        run_id,
        note_rel_path,
        note_title,
        deck,
        question,
        answer,
        source,
        content_hash,
        status,
        proposed_at,
        resolved_at,
        anki_note_id,
    ) in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO notes (
                rel_path, deck, committed_file_hash, pending_file_hash,
                last_seen_file_hash, last_processed
            )
            VALUES (?, ?, '', NULL, NULL, ?)
            """,
            (note_rel_path, deck, resolved_at or proposed_at or ""),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO cards (
                run_id, note_rel_path, note_title, deck, question, answer, source,
                card_content_hash, concept_key, front, status, proposed_at,
                committed_at, rejected_at, anki_note_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                note_rel_path,
                note_title,
                deck,
                question,
                answer,
                source,
                content_hash,
                answer,
                question,
                status,
                proposed_at,
                resolved_at if status == "committed" else None,
                resolved_at if status == "rejected" else None,
                anki_note_id,
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_card_state_migrated', '1')"
    )

def load_index(path: str | Path) -> SyncIndex:
    with _connect(path) as conn:
        notes: Dict[str, NoteEntry] = {}
        note_rows = conn.execute(
            """
            SELECT rel_path, deck, committed_file_hash, pending_file_hash,
                   last_processed
              FROM notes
             ORDER BY rel_path
            """
        ).fetchall()
        for rel_path, deck, committed_hash, pending_hash, last_processed in note_rows:
            notes[rel_path] = NoteEntry(
                committed_file_hash=committed_hash,
                pending_file_hash=pending_hash,
                last_processed=last_processed,
                deck=deck,
            )

        card_rows = conn.execute(
            """
            SELECT note_rel_path, anki_note_id, concept_key, card_content_hash,
                   front, answer, source
              FROM cards
             WHERE status = 'committed'
             ORDER BY id
            """
        ).fetchall()
        for rel_path, anki_note_id, concept_key, content_hash, front, answer, source in card_rows:
            entry = notes.get(rel_path)
            if entry is None:
                continue
            entry.cards.append(CardEntry(
                anki_note_id=anki_note_id,
                concept_key=concept_key,
                content_hash=content_hash,
                front=front,
                answer=answer,
                source=source,
            ))

    return SyncIndex(notes=notes)


def save_index(index: SyncIndex, path: str | Path) -> None:
    with _connect(path) as conn:
        seen = set(index.notes)
        existing = {
            row[0] for row in conn.execute("SELECT rel_path FROM notes").fetchall()
        }
        for rel_path in sorted(existing - seen):
            conn.execute("DELETE FROM notes WHERE rel_path = ?", (rel_path,))

        for rel_path, entry in index.notes.items():
            conn.execute(
                """
                INSERT INTO notes (
                    rel_path, deck, committed_file_hash, pending_file_hash,
                    last_seen_file_hash, last_processed
                )
                VALUES (?, ?, ?, ?, COALESCE(?, ?), ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    deck = excluded.deck,
                    committed_file_hash = excluded.committed_file_hash,
                    pending_file_hash = excluded.pending_file_hash,
                    last_seen_file_hash = excluded.last_seen_file_hash,
                    last_processed = excluded.last_processed
                """,
                (
                    rel_path,
                    entry.deck,
                    entry.committed_file_hash,
                    entry.pending_file_hash,
                    entry.pending_file_hash,
                    entry.committed_file_hash,
                    entry.last_processed,
                ),
            )

            desired_hashes = {card.content_hash for card in entry.cards}
            if desired_hashes:
                placeholders = ",".join("?" for _ in desired_hashes)
                conn.execute(
                    f"""
                    DELETE FROM cards
                     WHERE note_rel_path = ?
                       AND status = 'committed'
                       AND card_content_hash NOT IN ({placeholders})
                    """,
                    (rel_path, *desired_hashes),
                )
            else:
                conn.execute(
                    "DELETE FROM cards WHERE note_rel_path = ? AND status = 'committed'",
                    (rel_path,),
                )

            for card in entry.cards:
                existing_card = conn.execute(
                    """
                    SELECT id FROM cards
                     WHERE note_rel_path = ?
                       AND card_content_hash = ?
                       AND status = 'committed'
                     LIMIT 1
                    """,
                    (rel_path, card.content_hash),
                ).fetchone()
                if existing_card:
                    conn.execute(
                        """
                        UPDATE cards
                           SET deck = ?,
                               question = COALESCE(NULLIF(question, ''), ?),
                               answer = COALESCE(NULLIF(answer, ''), ?),
                               source = COALESCE(NULLIF(source, ''), ?),
                               concept_key = ?,
                               front = ?,
                               anki_note_id = ?
                         WHERE id = ?
                        """,
                        (
                            entry.deck,
                            card.front,
                            card.answer,
                            card.source,
                            card.concept_key,
                            card.front,
                            card.anki_note_id,
                            existing_card[0],
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO cards (
                            run_id, note_rel_path, note_title, deck, question, answer,
                            source, card_content_hash, concept_key, front, status,
                            proposed_at, committed_at, anki_note_id
                        )
                        VALUES ('save-index', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?)
                        """,
                        (
                            rel_path,
                            Path(rel_path).stem,
                            entry.deck,
                            card.front,
                            card.answer,
                            card.source,
                            card.content_hash,
                            card.concept_key,
                            card.front,
                            entry.last_processed,
                            entry.last_processed,
                            card.anki_note_id,
                        ),
                    )
