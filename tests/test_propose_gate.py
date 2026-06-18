from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cli.check_markers import CALLOUT_MARKER
from cli.propose import find_unresolved_check_notes


class ProposeGateTests(unittest.TestCase):
    def test_finds_unresolved_check_callouts_in_notes(self) -> None:
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "OS").mkdir()
            (vault / "OS" / "good.md").write_text("A corrected note.\n")
            (vault / "OS" / "bad.md").write_text(
                f"{CALLOUT_MARKER}\n"
                "> [!danger] Factual wrong\n"
                "> **Correction:** fix this before carding\n"
            )

            self.assertEqual(
                find_unresolved_check_notes(vault),
                ["OS/bad.md"],
            )

    def test_ignores_generated_and_obsidian_files(self) -> None:
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / ".obsidian").mkdir()
            (vault / ".obsidian" / "workspace.md").write_text(CALLOUT_MARKER)
            (vault / "New cards.md").write_text(CALLOUT_MARKER)

            self.assertEqual(find_unresolved_check_notes(vault), [])


if __name__ == "__main__":
    unittest.main()
