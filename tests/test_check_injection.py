from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cli.check import _CALLOUT_MARKER, _inject_callouts
from reason.check import ClaimIssue, NoteReport


class CheckInjectionTests(unittest.TestCase):
    def test_paraphrased_claim_inserts_above_best_matching_line(self) -> None:
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "note.md"
            wrong_line = (
                "Time sharing creates the illusion of many virtual CPUs by rapidly "
                "switching a limited number of physical CPUs among processes, not by "
                "promoting the idea that many virtual CPUs exist when only a limited "
                "number exist."
            )
            note.write_text(f"blah blah blah blah blah\n\n{wrong_line}\n")

            issue = ClaimIssue(
                claim=(
                    "Time sharing does not promote the idea that many virtual CPUs "
                    "exist when only a limited number exist."
                ),
                verdict="wrong",
                correction=(
                    "Time sharing promotes the illusion that many virtual CPUs exist "
                    "when only a limited number physical CPUs exist."
                ),
                citation="OSTEP p.25",
                severity="error",
            )

            _inject_callouts(vault, NoteReport("note.md", [issue]))

            lines = note.read_text().splitlines()
            marker_idx = lines.index(_CALLOUT_MARKER)
            wrong_line_idx = lines.index(wrong_line)

            self.assertEqual(lines[0], "blah blah blah blah blah")
            self.assertLess(marker_idx, wrong_line_idx)
            self.assertEqual(wrong_line_idx, marker_idx + 6)

    def test_empty_report_strips_stale_callouts(self) -> None:
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "note.md"
            note.write_text(
                "<!-- anki-check -->\n"
                "> [!warning] Factual imprecise\n"
                "> **You wrote:** old text\n"
                "\n"
                "Actual note text.\n"
            )

            _inject_callouts(vault, NoteReport("note.md"))

            self.assertEqual(note.read_text(), "Actual note text.\n")


if __name__ == "__main__":
    unittest.main()
