from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from reason.check import NoteReport, _is_trivial_imprecision, check_all_notes
from sync.index import NoteEntry, save_index
from sync.vault import _sha256


class CheckCacheTests(unittest.TestCase):
    def test_treats_close_paraphrase_as_trivial_imprecision(self) -> None:
        self.assertTrue(_is_trivial_imprecision(
            "Space sharing is dividing a shared resource (in space) by those who wish to use it",
            "Space sharing is where a resource is divided (in space) among those who wish to use it.",
        ))

    def test_skips_unchanged_committed_note(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state = root / "state.sqlite"
            note = vault / "note.md"
            note.write_text("Already committed.\n")

            index = __import__("sync.index", fromlist=["SyncIndex"]).SyncIndex()
            index.upsert_note(
                "note.md",
                NoteEntry(
                    committed_file_hash=_sha256(note.read_text()),
                    last_processed="now",
                    deck="",
                ),
            )
            save_index(index, state)

            with patch("reason.check.check_note") as check_note:
                reports = check_all_notes(vault, state_path=state)

            self.assertEqual(reports, [])
            check_note.assert_not_called()

    def test_marks_clean_changed_note_as_checked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state = root / "state.sqlite"
            note = vault / "note.md"
            note.write_text("Changed content.\n")
            checked = []

            def fake_check_note(rel_path: str, content: str, **_: object) -> NoteReport:
                checked.append((rel_path, content))
                return NoteReport(rel_path)

            with patch("reason.check.check_note", side_effect=fake_check_note):
                first = check_all_notes(vault, state_path=state)
                second = check_all_notes(vault, state_path=state)

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(checked, [("note.md", "Changed content.\n")])


if __name__ == "__main__":
    unittest.main()
