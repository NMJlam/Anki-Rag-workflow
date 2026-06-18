"""SQLite lifecycle helpers for proposed, committed, and rejected cards."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from .index import init_db, state_db_path

if TYPE_CHECKING:
    from reason.crosscheck import NoteProposals


@dataclass(frozen=True)
class CardState:
    note_rel_path: str
    note_title: str
    deck: str
    question: str
    answer: str
    source: str
    content_hash: str


def default_state_path(state_path: str | Path) -> Path:
    return state_db_path(state_path)


def card_content_hash(source: str, answer: str) -> str:
    combined = source + "\n" + answer
    return hashlib.sha256(combined.encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = state_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


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
            content_hash = card_content_hash(proposal.source, proposal.answer)
            rows.append(
                (
                    run,
                    note.rel_path,
                    note_title,
                    note.deck,
                    proposal.question,
                    proposal.answer,
                    proposal.source,
                    content_hash,
                    proposal.target,
                    proposal.question,
                    now,
                )
            )

    if not rows:
        return 0

    with _connect(db_path) as conn:
        for note in proposals:
            conn.execute(
                """
                INSERT OR IGNORE INTO notes (
                    rel_path, deck, committed_file_hash, pending_file_hash,
                    last_seen_file_hash, last_processed
                )
                VALUES (?, ?, '', NULL, NULL, ?)
                """,
                (note.rel_path, note.deck, now),
            )
        conn.executemany(
            """
            UPDATE cards
               SET status = 'rejected',
                   rejected_at = ?
             WHERE status = 'proposed'
               AND note_rel_path = ?
            """,
            [(now, rel_path) for rel_path in note_paths],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO cards (
                run_id, note_rel_path, note_title, deck, question, answer, source,
                card_content_hash, concept_key, front, status, proposed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
            """,
            rows,
        )
    return len(rows)


def pending_note_paths(db_path: str | Path) -> set[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT note_rel_path FROM cards WHERE status = 'proposed'"
        ).fetchall()
    return {row[0] for row in rows}


def pending_count(db_path: str | Path) -> int:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE status = 'proposed'"
        ).fetchone()
    return int(row[0])


def _card_values(card: CardState) -> tuple[str, str, str, str, str, str, str, str, str]:
    return (
        card.note_rel_path,
        card.note_title,
        card.deck,
        card.question,
        card.answer,
        card.source,
        card.content_hash,
        card.answer,
        card.question,
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
    committed_by_note: dict[str, set[str]] = {}
    with _connect(db_path) as conn:
        for card in cards:
            anki_note_id = (anki_note_ids or {}).get(card.content_hash)
            conn.execute(
                """
                INSERT OR IGNORE INTO notes (
                    rel_path, deck, committed_file_hash, pending_file_hash,
                    last_seen_file_hash, last_processed
                )
                VALUES (?, ?, '', NULL, NULL, ?)
                """,
                (card.note_rel_path, card.deck, now),
            )
            result = conn.execute(
                """
                UPDATE cards
                   SET status = 'committed',
                       committed_at = ?,
                       anki_note_id = COALESCE(?, anki_note_id),
                       question = ?,
                       answer = ?,
                       source = ?,
                       front = ?
                 WHERE status = 'proposed'
                   AND note_rel_path = ?
                   AND card_content_hash = ?
                """,
                (
                    now,
                    anki_note_id,
                    card.question,
                    card.answer,
                    card.source,
                    card.question,
                    card.note_rel_path,
                    card.content_hash,
                ),
            )
            if result.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO cards (
                        run_id, note_rel_path, note_title, deck, question, answer,
                        source, card_content_hash, concept_key, front, status,
                        proposed_at, committed_at, anki_note_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?)
                    """,
                    (
                        "manual-commit",
                        *_card_values(card),
                        now,
                        now,
                        anki_note_id,
                    ),
                )
            committed_by_note.setdefault(card.note_rel_path, set()).add(card.content_hash)
            count += 1

        for rel_path, hashes in committed_by_note.items():
            placeholders = ",".join("?" for _ in hashes)
            conn.execute(
                f"""
                UPDATE cards
                   SET status = 'rejected',
                       rejected_at = ?
                 WHERE status = 'proposed'
                   AND note_rel_path = ?
                   AND card_content_hash NOT IN ({placeholders})
                """,
                (now, rel_path, *hashes),
            )
    return count


def reject_pending(db_path: str | Path) -> int:
    now = _utc_now()
    with _connect(db_path) as conn:
        result = conn.execute(
            """
            UPDATE cards
               SET status = 'rejected',
                   rejected_at = ?
             WHERE status = 'proposed'
            """,
            (now,),
        )
    return result.rowcount
