"""SQLite state for proposed, committed, and rejected cards."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:
    from reason.crosscheck import NoteProposals


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CardState:
    note_rel_path: str
    note_title: str
    deck: str
    question: str
    answer: str
    source: str
    content_hash: str


def default_state_path(index_path: str | Path) -> Path:
    """Place card state beside the sync index."""
    return Path(index_path).parent / "card_state.sqlite"


def card_content_hash(source: str, answer: str) -> str:
    combined = source + "\n" + answer
    return hashlib.sha256(combined.encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
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
        CREATE TABLE IF NOT EXISTS card_state (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            note_rel_path TEXT NOT NULL,
            note_title TEXT NOT NULL,
            deck TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            source TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('proposed', 'committed', 'rejected')),
            proposed_at TEXT NOT NULL,
            resolved_at TEXT,
            anki_note_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_card_state_status
        ON card_state(status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_card_state_note_status
        ON card_state(note_rel_path, status)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_card_state_open_proposal
        ON card_state(note_rel_path, content_hash)
        WHERE status = 'proposed'
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def record_proposals(
    db_path: str | Path,
    proposals: Sequence[NoteProposals],
    *,
    run_id: str | None = None,
) -> int:
    """Store a proposal run as pending card state."""
    now = _utc_now()
    run = run_id or now
    note_paths = {note.rel_path for note in proposals}
    rows = []
    for note in proposals:
        note_title = Path(note.rel_path).stem
        for proposal in note.proposals:
            rows.append(
                (
                    run,
                    note.rel_path,
                    note_title,
                    note.deck,
                    proposal.question,
                    proposal.answer,
                    proposal.source,
                    card_content_hash(proposal.source, proposal.answer),
                    "proposed",
                    now,
                )
            )

    if not rows:
        return 0

    with _connect(db_path) as conn:
        conn.executemany(
            """
            UPDATE card_state
               SET status = 'rejected',
                   resolved_at = ?
             WHERE status = 'proposed'
               AND note_rel_path = ?
            """,
            [(now, rel_path) for rel_path in note_paths],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO card_state (
                run_id, note_rel_path, note_title, deck, question, answer, source,
                content_hash, status, proposed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def pending_note_paths(db_path: str | Path) -> set[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT note_rel_path FROM card_state WHERE status = 'proposed'"
        ).fetchall()
    return {row[0] for row in rows}


def pending_count(db_path: str | Path) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM card_state WHERE status = 'proposed'"
        ).fetchone()
    return int(row[0])


def _card_values(card: CardState) -> tuple[str, str, str, str, str, str, str]:
    return (
        card.note_rel_path,
        card.note_title,
        card.deck,
        card.question,
        card.answer,
        card.source,
        card.content_hash,
    )


def mark_cards_committed(
    db_path: str | Path,
    cards: Iterable[CardState],
    *,
    anki_note_ids: dict[str, int] | None = None,
) -> int:
    """Resolve matching pending cards as committed, or insert committed rows."""
    now = _utc_now()
    count = 0
    with _connect(db_path) as conn:
        for card in cards:
            anki_note_id = (anki_note_ids or {}).get(card.content_hash)
            result = conn.execute(
                """
                UPDATE card_state
                   SET status = 'committed',
                       resolved_at = ?,
                       anki_note_id = COALESCE(?, anki_note_id)
                 WHERE status = 'proposed'
                   AND note_rel_path = ?
                   AND content_hash = ?
                """,
                (now, anki_note_id, card.note_rel_path, card.content_hash),
            )
            if result.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO card_state (
                        run_id, note_rel_path, note_title, deck, question, answer,
                        source, content_hash, status, proposed_at, resolved_at,
                        anki_note_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?)
                    """,
                    ("manual-commit", *_card_values(card), now, now, anki_note_id),
                )
            count += 1
    return count


def reject_pending(db_path: str | Path) -> int:
    now = _utc_now()
    with _connect(db_path) as conn:
        result = conn.execute(
            """
            UPDATE card_state
               SET status = 'rejected',
                   resolved_at = ?
             WHERE status = 'proposed'
            """,
            (now,),
        )
    return result.rowcount
