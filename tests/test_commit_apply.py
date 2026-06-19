from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commit.anki import AnkiConnectError
from commit.apply import apply_commit


class CommitApplyTests(unittest.TestCase):
    def test_replacement_adds_new_note_before_deleting_old_note(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state_path = root / "state.sqlite"
            backup = root / "backup.apkg"
            backup.write_bytes(b"backup")
            (vault / "Changed cards.md").write_text(
                "> [!warning] NEW DETAIL - Replace existing card\n"
                "> -- card 123 from [[Virtualisation]]      deck: Operating Systems\n"
                ">\n"
                "## ++ replace from [[Virtualisation]]      deck: Operating Systems\n"
                "Front: What is a TLB miss?\n"
                "Back:\n"
                "- It depends on the architecture.\n"
                'source: "OSTEP p.188"\n'
            )

            events: list[str] = []

            def add_note(*args, **kwargs):
                events.append("add")
                self.assertTrue(kwargs["allow_duplicate"])
                return 456

            def delete_notes(note_ids):
                events.append("delete")
                self.assertEqual(note_ids, [123])

            with (
                patch("commit.apply.export_all_decks", return_value=[str(backup)]),
                patch("commit.apply.ensure_model"),
                patch("commit.apply.create_deck"),
                patch("commit.apply.add_note", side_effect=add_note),
                patch("commit.apply.delete_notes", side_effect=delete_notes),
                patch("commit.apply.sync"),
            ):
                apply_commit(vault, state_path)

        self.assertEqual(events, ["add", "delete"])

    def test_add_failure_preserves_review_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state_path = root / "state.sqlite"
            backup = root / "backup.apkg"
            backup.write_bytes(b"backup")
            review_file = vault / "New cards.md"
            review_file.write_text(
                "## ++ from [[Virtualisation]]      deck: Operating Systems\n"
                "Front: What is a TLB miss?\n"
                "Back:\n"
                "- It depends on the architecture.\n"
                'source: "OSTEP p.188"\n'
            )

            with (
                patch("commit.apply.export_all_decks", return_value=[str(backup)]),
                patch("commit.apply.ensure_model"),
                patch("commit.apply.create_deck"),
                patch(
                    "commit.apply.add_note",
                    side_effect=AnkiConnectError("temporary failure"),
                ),
                patch("commit.apply.delete_notes") as delete_notes,
                patch("commit.apply.sync") as sync,
            ):
                apply_commit(vault, state_path)

            self.assertTrue(review_file.exists())
            delete_notes.assert_not_called()
            sync.assert_not_called()

    def test_partial_add_failure_rewrites_review_file_with_failed_card_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            (vault / "Virtualisation.md").write_text("note")
            state_path = root / "state.sqlite"
            backup = root / "backup.apkg"
            backup.write_bytes(b"backup")
            review_file = vault / "New cards.md"
            review_file.write_text(
                "## ++ from [[Virtualisation]]      deck: Operating Systems\n"
                "Front: Applied question?\n"
                "Back:\n"
                "- Applied answer.\n"
                'source: "applied source"\n'
                "\n"
                "## ++ from [[Virtualisation]]      deck: Operating Systems\n"
                "Front: Failed question?\n"
                "Back:\n"
                "- Failed answer.\n"
                'source: "failed source"\n'
            )

            def add_note(deck, front, *args, **kwargs):
                if front == "Failed question?":
                    raise AnkiConnectError("temporary failure")
                return 456

            with (
                patch("commit.apply.export_all_decks", return_value=[str(backup)]),
                patch("commit.apply.ensure_model"),
                patch("commit.apply.create_deck"),
                patch("commit.apply.add_note", side_effect=add_note),
                patch("commit.apply.sync") as sync,
            ):
                apply_commit(vault, state_path)

            rewritten = review_file.read_text()
            self.assertNotIn("Applied question?", rewritten)
            self.assertIn("Failed question?", rewritten)
            sync.assert_not_called()

    def test_deleted_cards_failure_preserves_review_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state_path = root / "state.sqlite"
            backup = root / "backup.apkg"
            backup.write_bytes(b"backup")
            review_file = vault / "Deleted cards.md"
            review_file.write_text(
                "## -- delete card 123 from [[Virtualisation]]      deck: Operating Systems\n"
                "Front: What is a TLB miss?\n"
                "Back:\n"
                "- It depends on the architecture.\n"
                'source: "OSTEP p.188"\n'
            )

            with (
                patch("commit.apply.export_all_decks", return_value=[str(backup)]),
                patch("commit.apply.ensure_model"),
                patch("commit.apply.create_deck"),
                patch(
                    "commit.apply.delete_notes",
                    side_effect=AnkiConnectError("temporary failure"),
                ),
                patch("commit.apply.sync") as sync,
            ):
                apply_commit(vault, state_path)

            self.assertTrue(review_file.exists())
            sync.assert_not_called()

    def test_replacement_delete_failure_leaves_only_deletion_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            (vault / "Virtualisation.md").write_text("note")
            state_path = root / "state.sqlite"
            backup = root / "backup.apkg"
            backup.write_bytes(b"backup")
            changed_file = vault / "Changed cards.md"
            deleted_file = vault / "Deleted cards.md"
            changed_file.write_text(
                "> [!warning] NEW DETAIL - Replace existing card\n"
                "> -- card 123 from [[Virtualisation]]      deck: Operating Systems\n"
                ">\n"
                "## ++ replace from [[Virtualisation]]      deck: Operating Systems\n"
                "Front: What is a TLB miss?\n"
                "Back:\n"
                "- It depends on the architecture.\n"
                'source: "OSTEP p.188"\n'
            )

            with (
                patch("commit.apply.export_all_decks", return_value=[str(backup)]),
                patch("commit.apply.ensure_model"),
                patch("commit.apply.create_deck"),
                patch("commit.apply.add_note", return_value=456),
                patch(
                    "commit.apply.delete_notes",
                    side_effect=AnkiConnectError("temporary failure"),
                ),
                patch("commit.apply.sync") as sync,
            ):
                apply_commit(vault, state_path)

            self.assertFalse(changed_file.exists())
            self.assertTrue(deleted_file.exists())
            self.assertIn("delete card 123", deleted_file.read_text())
            sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
