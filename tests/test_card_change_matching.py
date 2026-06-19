from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commit.apply import parse_diff_cards
from reason.crosscheck import (
    CardProposal,
    NoteProposals,
    _card_identity_hash,
    _classify_card_change,
    _semantic_card_changes,
)
from reason.emit import emit_changed_cards
from sync.index import CardEntry, NoteEntry, SyncIndex, load_index, save_index


class CardChangeMatchingTests(unittest.TestCase):
    def test_classifies_exact_existing_card_as_keep(self) -> None:
        existing = {
            "anki_note_id": 123,
            "content_hash": _card_identity_hash("OSTEP p.188", "A TLB miss is uncached."),
            "front": "What is a TLB miss?",
        }

        action, match = _classify_card_change(
            question="What is a TLB miss?",
            answer="A TLB miss is uncached.",
            source="OSTEP p.188",
            existing_cards=[existing],
            used_existing_ids=set(),
        )

        self.assertEqual(action, "keep")
        self.assertEqual(match, existing)

    def test_classifies_same_question_changed_answer_as_replace(self) -> None:
        existing = {
            "anki_note_id": 123,
            "content_hash": _card_identity_hash("OSTEP p.188", "Old answer."),
            "front": "What is a TLB miss?",
            "answer": "Old answer.",
        }

        action, match = _classify_card_change(
            question="What is a TLB miss?",
            answer="New answer.",
            source="OSTEP p.188",
            existing_cards=[existing],
            used_existing_ids=set(),
        )

        self.assertEqual(action, "replace")
        self.assertEqual(match, existing)

    def test_classifies_source_only_change_as_keep(self) -> None:
        existing = {
            "anki_note_id": 123,
            "content_hash": _card_identity_hash("Old citation", "Same answer."),
            "front": "What is a TLB miss?",
            "answer": "Same answer.",
        }

        action, match = _classify_card_change(
            question="What is a TLB miss?",
            answer="Same answer.",
            source="New citation",
            existing_cards=[existing],
            used_existing_ids=set(),
        )

        self.assertEqual(action, "keep")
        self.assertEqual(match, existing)

    def test_classifies_unmatched_question_as_add(self) -> None:
        existing = {
            "anki_note_id": 123,
            "content_hash": _card_identity_hash("OSTEP p.188", "Old answer."),
            "front": "What is a TLB miss?",
            "answer": "Old answer.",
        }

        action, match = _classify_card_change(
            question="Who handles a software-managed TLB miss?",
            answer="The operating system handles it.",
            source="OSTEP p.192",
            existing_cards=[existing],
            used_existing_ids=set(),
        )

        self.assertEqual(action, "add")
        self.assertIsNone(match)

    def test_semantic_match_can_keep_reworded_same_card(self) -> None:
        existing = {
            "anki_note_id": 123,
            "front": "What is a TLB miss?",
            "answer": "A TLB miss means the translation is not cached in the TLB.",
            "source": "OSTEP p.188",
        }
        new = {
            "target": "TLB miss",
            "question": "Define a TLB miss.",
            "answer": "A TLB miss occurs when the needed virtual-to-physical translation is absent from the TLB.",
            "source": "OSTEP p.188",
        }

        with patch("reason.crosscheck.chat_json") as chat_json:
            chat_json.return_value = {
                "matches": [
                    {
                        "new_index": 0,
                        "action": "keep",
                        "existing_anki_note_id": 123,
                        "reason": "same learning objective and answer meaning",
                    }
                ]
            }

            matches = _semantic_card_changes(
                new_cards=[new],
                existing_cards=[existing],
                model="test-model",
            )

        self.assertEqual(matches[0], ("keep", existing))

    def test_semantic_match_can_replace_same_concept_changed_answer(self) -> None:
        existing = {
            "anki_note_id": 123,
            "front": "Who handles a TLB miss?",
            "answer": "The hardware handles it.",
            "source": "Old note",
        }
        new = {
            "target": "TLB miss handling",
            "question": "Who handles a TLB miss, and what does it depend on?",
            "answer": "It depends on the architecture: hardware may handle it, or the OS may handle it in software.",
            "source": "OSTEP p.192",
        }

        with patch("reason.crosscheck.chat_json") as chat_json:
            chat_json.return_value = {
                "matches": [
                    {
                        "new_index": 0,
                        "action": "replace",
                        "existing_anki_note_id": 123,
                        "reason": "same objective with materially changed answer",
                    }
                ]
            }

            matches = _semantic_card_changes(
                new_cards=[new],
                existing_cards=[existing],
                model="test-model",
            )

        self.assertEqual(matches[0], ("replace", existing))

    def test_changed_cards_emit_and_parse_replacements(self) -> None:
        proposals = [
            NoteProposals(
                rel_path="Virtualisation/Virtualisation.md",
                deck="Virtualisation",
                is_new_note=False,
                proposals=[
                    CardProposal(
                        target="TLB miss",
                        question="What is a TLB miss?",
                        answer="A TLB miss is an uncached address translation.",
                        source="OSTEP p.188",
                        action="replace",
                        replaces_anki_note_id=123,
                        replaces_front="What is a TLB miss?",
                        replaces_answer="A TLB miss is missing from the TLB.",
                    ),
                    CardProposal(
                        target="software-managed TLB",
                        question="Who handles a software-managed TLB miss?",
                        answer="The operating system handles it.",
                        source="OSTEP p.192",
                        action="add",
                    ),
                ],
            )
        ]

        content = emit_changed_cards(proposals, "/tmp/vault", timestamp="2026-06-18 15:47")

        self.assertIn("> [!warning]", content)
        self.assertIn("Replace existing card", content)
        self.assertIn("> -- card 123 from [[Virtualisation]]", content)
        self.assertIn('> old Q: "What is a TLB miss?"', content)
        self.assertIn('> old A: "A TLB miss is missing from the TLB."', content)
        self.assertIn("## ++ replace from [[Virtualisation]]", content)
        self.assertIn("## ++ add from [[Virtualisation]]", content)

        parsed = parse_diff_cards(content)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].replaces_anki_note_id, 123)
        self.assertIsNone(parsed[1].replaces_anki_note_id)

    def test_parser_reads_callout_replacement_header(self) -> None:
        parsed = parse_diff_cards(
            "> [!warning] Replace existing card\n"
            "> -- card 123 from [[Virtualisation]]      deck: Virtualisation\n"
            '> old Q: "What is a TLB miss?"\n'
            ">\n"
            "## ++ replace from [[Virtualisation]]      deck: Virtualisation\n"
            "Q: Define a TLB miss.\n"
            "A: A TLB miss is an uncached address translation.\n"
            'source: "OSTEP p.188"\n'
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].note_title, "Virtualisation")
        self.assertEqual(parsed[0].replaces_anki_note_id, 123)

    def test_parser_keeps_legacy_add_header_compatible(self) -> None:
        parsed = parse_diff_cards(
            "## ++ from [[Virtualisation]]      deck: Virtualisation\n"
            "Q: What is a TLB miss?\n"
            "A: An uncached address translation.\n"
            'source: "OSTEP p.188"\n'
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].note_title, "Virtualisation")
        self.assertIsNone(parsed[0].replaces_anki_note_id)

    def test_index_round_trips_committed_card_answer(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "card_state.sqlite"
            index = SyncIndex(
                notes={
                    "Virtualisation/Virtualisation.md": NoteEntry(
                        committed_file_hash="hash",
                        last_processed="now",
                        deck="Virtualisation",
                        cards=[
                            CardEntry(
                                anki_note_id=123,
                                concept_key="answer",
                                content_hash="content-hash",
                                front="What is a TLB miss?",
                                answer="An uncached address translation.",
                                source="OSTEP p.188",
                            )
                        ],
                    )
                }
            )

            save_index(index, db_path)
            loaded = load_index(db_path)

        card = loaded.get_note("Virtualisation/Virtualisation.md").cards[0]
        self.assertEqual(card.answer, "An uncached address translation.")


if __name__ == "__main__":
    unittest.main()
