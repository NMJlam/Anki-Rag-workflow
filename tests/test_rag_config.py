from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rag.config import load_config


class RagConfigTests(unittest.TestCase):
    def test_discovers_pdfs_from_books_dir_and_applies_overrides(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            books = root / "books"
            books.mkdir()
            (books / "Operating Systems - Three Easy Pieces.pdf").write_bytes(b"%PDF")
            (books / "Other.pdf").write_bytes(b"%PDF")

            config_path = root / "config.toml"
            config_path.write_text(
                "[rag]\n"
                'embedder = "hashing"\n'
                'embedder_device = "cpu"\n'
                'embed_batch_size = 16\n'
                'index_dir = "data/test_index"\n'
                'books_dir = "books"\n'
                "\n"
                "[[rag.book_overrides]]\n"
                'path = "Operating Systems - Three Easy Pieces.pdf"\n'
                'name = "OSTEP"\n'
                'page_offset = 21\n'
            )

            cfg = load_config(config_path)

            self.assertEqual(cfg.embedder, "hashing")
            self.assertEqual(cfg.embedder_device, "cpu")
            self.assertEqual(cfg.embed_batch_size, 16)
            self.assertEqual(cfg.index_dir, str((root / "data" / "test_index").resolve()))
            self.assertEqual([book.name for book in cfg.books], ["OSTEP", "Other"])
            self.assertEqual(cfg.books[0].files[0].page_offset, 21)
            self.assertEqual(
                cfg.books[0].files[0].path,
                str((books / "Operating Systems - Three Easy Pieces.pdf").resolve()),
            )


if __name__ == "__main__":
    unittest.main()
