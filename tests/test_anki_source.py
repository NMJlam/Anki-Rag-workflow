from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sync.anki_source import refresh_committed_cards_from_anki, regular_cards_for_deck
from sync.index import CardEntry, NoteEntry, SyncIndex, load_index, save_index


class AnkiSourceTests(unittest.TestCase):
    def test_refresh_imports_tracked_cards_from_anki(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            (vault / "Virtualisation.md").write_text("# Virtualisation\n")
            db_path = root / "card_state.sqlite"

            with (
                patch("sync.anki_source.model_names", return_value=["Basic (tracked)"]),
                patch("sync.anki_source.find_notes", return_value=[123]),
                patch(
                    "sync.anki_source.notes_info",
                    return_value=[
                        {
                            "noteId": 123,
                            "fields": {
                                "Front": {"value": "What is a TLB miss?"},
                                "Back": {"value": "A missing TLB translation."},
                                "SourceNote": {"value": "Virtualisation.md"},
                                "ContentHash": {"value": "hash-1"},
                            },
                            "cards": [456],
                        }
                    ],
                ),
                patch(
                    "sync.anki_source.cards_info",
                    return_value=[{"cardId": 456, "deckName": "Virtualisation"}],
                ),
            ):
                imported = refresh_committed_cards_from_anki(
                    db_path,
                    vault_path=vault,
                )

            self.assertEqual(imported, 1)
            index = load_index(db_path)
            note = index.get_note("Virtualisation.md")
            self.assertIsNotNone(note)
            self.assertEqual(note.deck, "Virtualisation")
            self.assertEqual(len(note.cards), 1)
            self.assertEqual(note.cards[0].anki_note_id, 123)
            self.assertEqual(note.cards[0].front, "What is a TLB miss?")
            self.assertEqual(note.cards[0].answer, "A missing TLB translation.")
            self.assertEqual(note.cards[0].content_hash, "hash-1")

    def test_refresh_preserves_note_hashes_and_pending_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "card_state.sqlite"
            save_index(
                SyncIndex(
                    notes={
                        "Virtualisation.md": NoteEntry(
                            committed_file_hash="file-hash",
                            pending_file_hash="pending-hash",
                            last_processed="now",
                            deck="Old Deck",
                            cards=[
                                CardEntry(
                                    anki_note_id=999,
                                    concept_key="old",
                                    content_hash="old-hash",
                                    front="Old",
                                    answer="Old answer",
                                )
                            ],
                        )
                    }
                ),
                db_path,
            )

            with (
                patch("sync.anki_source.model_names", return_value=["Basic (tracked)"]),
                patch("sync.anki_source.find_notes", return_value=[123]),
                patch(
                    "sync.anki_source.notes_info",
                    return_value=[
                        {
                            "noteId": 123,
                            "fields": {
                                "Front": {"value": "New front"},
                                "Back": {"value": "New answer"},
                                "SourceNote": {"value": "Virtualisation.md"},
                                "ContentHash": {"value": "new-hash"},
                            },
                            "cards": [456],
                        }
                    ],
                ),
                patch(
                    "sync.anki_source.cards_info",
                    return_value=[{"cardId": 456, "deckName": "Virtualisation"}],
                ),
            ):
                refresh_committed_cards_from_anki(db_path)

            note = load_index(db_path).get_note("Virtualisation.md")
            self.assertEqual(note.committed_file_hash, "file-hash")
            self.assertEqual(note.pending_file_hash, "pending-hash")
            self.assertEqual(note.deck, "Virtualisation")
            self.assertEqual(len(note.cards), 1)
            self.assertEqual(note.cards[0].anki_note_id, 123)

    def test_regular_cards_for_deck_reads_non_tracked_front_back(self) -> None:
        with (
            patch("sync.anki_source.find_notes", return_value=[123]),
            patch(
                "sync.anki_source.notes_info",
                return_value=[
                    {
                        "noteId": 123,
                        "fields": {
                            "Front": {"value": "What is a TLB miss?"},
                            "Back": {"value": "A missing translation."},
                        },
                    }
                ],
            ),
        ):
            cards = regular_cards_for_deck("Virtualisation")

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["anki_note_id"], 123)
        self.assertEqual(cards[0]["front"], "What is a TLB miss?")
        self.assertEqual(cards[0]["answer"], "A missing translation.")
        self.assertFalse(cards[0]["tracked"])


if __name__ == "__main__":
    unittest.main()
