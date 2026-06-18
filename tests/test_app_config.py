from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sync.config import load_app_config


class AppConfigTests(unittest.TestCase):
    def test_resolves_relative_paths_from_config_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.toml"
            config_path.write_text(
                "[paths]\n"
                'vault = "vault"\n'
                'state = "state/card_state.sqlite"\n'
            )

            config = load_app_config(config_path)

            root = root.resolve()
            self.assertEqual(config.vault_path, root / "vault")
            self.assertEqual(config.state_path, root / "state" / "card_state.sqlite")
            self.assertEqual(config.books_config, (root / "config.toml").resolve())


if __name__ == "__main__":
    unittest.main()
