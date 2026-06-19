from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
