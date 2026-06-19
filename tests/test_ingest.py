import unittest

from rag.ingest import chunk_page


class IngestTests(unittest.TestCase):
    def test_chunk_page_rejects_overlap_that_cannot_advance(self) -> None:
        with self.assertRaisesRegex(ValueError, "chunk_overlap"):
            chunk_page("x" * 200, chunk_chars=100, overlap=100)

    def test_chunk_page_rejects_invalid_chunk_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "chunk_chars"):
            chunk_page("x" * 200, chunk_chars=0, overlap=0)


if __name__ == "__main__":
    unittest.main()
