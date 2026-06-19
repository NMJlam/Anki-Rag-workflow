from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commit.apply import parse_diff_cards
from reason.crosscheck import (
    CardProposal,
    NoteProposals,
    _add_missing_markdown_list_cards,
    _card_identity_hash,
    _classify_card_change,
    _merge_duplicate_question_cards,
    _semantic_card_changes,
    _split_definition_list_cards,
)
from reason.emit import emit_changed_cards
from sync.index import CardEntry, NoteEntry, SyncIndex, load_index, save_index


class CardChangeMatchingTests(unittest.TestCase):
    def test_splits_definition_list_card_into_list_and_definition_cards(self) -> None:
        source = (
            "**What are the states that a process can be in?**\n"
            "1. **Initial State:** When the process is being created\n"
            "2. **Running:** In the running state, the process is running on the CPU.\n"
            "3. **Ready:** In the ready state, a process is ready to run but the OS "
            "task scheduler has decided to not run it at this point in time.\n"
            "4. **Blocked:** In a blocked state, a process has performed an operation "
            "that makes it not ready to run until some other event has been performed.\n"
            "5. **Final:** The process has exited but its resources have not been cleaned up."
        )
        result = _split_definition_list_cards({
            "cards": [
                {
                    "target": "process states",
                    "Q": "What are the states that a process can be in?",
                    "A": (
                        "- Initial State: When the process is being created\n"
                        "- Running: In the running state, the process is running on the CPU.\n"
                        "- Ready: In the ready state, a process is ready to run but the OS task scheduler has decided to not run it at this point in time.\n"
                        "- Blocked: In a blocked state, a process has performed an operation that makes it not ready to run until some other event has been performed.\n"
                        "- Final: The process has exited but its resources have not been cleaned up."
                    ),
                    "source": source,
                }
            ],
            "pruned": [],
            "not_self_contained": [],
            "skipped": [],
        })

        self.assertEqual(len(result["cards"]), 6)
        self.assertEqual(
            result["cards"][0]["A"],
            "- Initial State\n- Running\n- Ready\n- Blocked\n- Final",
        )
        self.assertEqual(
            result["cards"][2]["Q"],
            "What does it mean for a process to be in the Running state?",
        )
        self.assertEqual(
            result["cards"][2]["A"],
            "In the running state, the process is running on the CPU.",
        )

    def test_adds_missing_card_for_colon_introduced_markdown_list(self) -> None:
        note = (
            "At any point in time, the process can be described by its PCB state:\n"
            "- The contents of CPU registers (Including the [[Instruction Pointer]] "
            "and [[Stack Pointer]]). This is the register context.\n"
            "- I/O information\n"
            "- Pointers to the contents of its address space\n"
        )

        result = _add_missing_markdown_list_cards(
            {
                "cards": [],
                "pruned": [],
                "not_self_contained": [],
                "skipped": [],
            },
            note,
        )

        self.assertEqual(len(result["cards"]), 1)
        self.assertEqual(
            result["cards"][0]["Q"],
            "What does a process's PCB state include?",
        )
        self.assertEqual(
            result["cards"][0]["A"],
            "- The contents of CPU registers (Including the Instruction Pointer and Stack Pointer)\n"
            "- I/O information\n"
            "- Pointers to the contents of its address space",
        )
        self.assertEqual(result["cards"][0]["source"], note.rstrip())

    def test_merges_same_question_same_source_into_list_card(self) -> None:
        source = (
            "The contents of CPU registers (Including the [[Instruction Pointer]] "
            "and [[Stack Pointer]]). This is the register context."
        )
        result = _merge_duplicate_question_cards({
            "cards": [
                {
                    "target": "Instruction Pointer",
                    "Q": "What is included in a process's register context?",
                    "A": (
                        "A process's register context includes the "
                        "Instruction Pointer."
                    ),
                    "source": source,
                },
                {
                    "target": "Stack Pointer",
                    "Q": "What is included in a process's register context?",
                    "A": (
                        "A process's register context includes the Stack Pointer."
                    ),
                    "source": source,
                },
            ],
            "pruned": [],
            "not_self_contained": [],
            "skipped": [],
        })

        self.assertEqual(len(result["cards"]), 1)
        self.assertEqual(
            result["cards"][0]["target"],
            "Instruction Pointer; Stack Pointer",
        )
        self.assertEqual(
            result["cards"][0]["A"],
            "- Instruction Pointer\n"
            "- Stack Pointer",
        )

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

    def test_semantic_match_does_not_replace_definition_with_list_card(self) -> None:
        existing = {
            "anki_note_id": 123,
            "front": "What does it mean for a process to be in the Running state?",
            "answer": "In the running state, the process is running on the CPU.",
            "source": "Old note",
        }
        new = {
            "target": "process states",
            "question": "What are the states that a process can be in?",
            "answer": "- Initial State\n- Running\n- Ready\n- Blocked\n- Final",
            "source": "Processes note",
        }

        with patch("reason.crosscheck.chat_json") as chat_json:
            chat_json.return_value = {
                "matches": [
                    {
                        "new_index": 0,
                        "action": "replace",
                        "existing_anki_note_id": 123,
                        "reason": "incorrect broad match",
                    }
                ]
            }

            matches = _semantic_card_changes(
                new_cards=[new],
                existing_cards=[existing],
                model="test-model",
            )

        self.assertEqual(matches[0], ("add", None))

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
        self.assertIn("> Front: What is a TLB miss?", content)
        self.assertIn("> Back:", content)
        self.assertIn("> - A TLB miss is missing from the TLB.", content)
        self.assertIn("## ++ replace from [[Virtualisation]]", content)
        self.assertIn("## ++ add from [[Virtualisation]]", content)
        self.assertIn("Front: What is a TLB miss?", content)
        self.assertIn("Back:\n- A TLB miss is an uncached address translation.", content)

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

    def test_parser_reads_front_back_blocks(self) -> None:
        parsed = parse_diff_cards(
            "## ++ from [[Virtualisation]]      deck: Virtualisation\n"
            "Front: What is a TLB miss?\n"
            "Back:\n"
            "- An uncached address translation.\n"
            "- It requires page-table lookup.\n"
            'source: "OSTEP p.188"\n'
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].question, "What is a TLB miss?")
        self.assertEqual(
            parsed[0].answer,
            "- An uncached address translation.\n- It requires page-table lookup.",
        )
        self.assertEqual(parsed[0].source, "OSTEP p.188")

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
