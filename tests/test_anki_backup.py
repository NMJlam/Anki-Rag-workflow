from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

from commit.anki import export_all_decks


class AnkiBackupTests(unittest.TestCase):
    def test_export_all_decks_exports_each_top_level_deck(self) -> None:
        with TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"

            with (
                patch(
                    "commit.anki.deck_names",
                    return_value=["Default", "Operating Systems", "Operating Systems::VM", "C++"],
                ),
                patch("commit.anki._request", return_value=True) as request,
            ):
                paths = export_all_decks(path=str(backup_dir))

        self.assertEqual(len(paths), 3)
        self.assertEqual(
            [Path(path).name for path in paths],
            ["C__.apkg", "Default.apkg", "Operating_Systems.apkg"],
        )
        request.assert_has_calls(
            [
                call(
                    "exportPackage",
                    deck="C++",
                    path=str(backup_dir / "C__.apkg"),
                    includeSched=True,
                ),
                call(
                    "exportPackage",
                    deck="Default",
                    path=str(backup_dir / "Default.apkg"),
                    includeSched=True,
                ),
                call(
                    "exportPackage",
                    deck="Operating Systems",
                    path=str(backup_dir / "Operating_Systems.apkg"),
                    includeSched=True,
                ),
            ]
        )


if __name__ == "__main__":
    unittest.main()
