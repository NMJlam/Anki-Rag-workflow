from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from rag.query import get_retriever, retrieve
from rag.store import VectorStore


class CountingEmbedder:
    instances = 0
    encode_calls = 0

    def __init__(self) -> None:
        type(self).instances += 1
        self.dim = 2
        self.model_name = "counting"

    def encode(self, texts: list[str]) -> np.ndarray:
        type(self).encode_calls += 1
        return np.array([[1.0, 0.0] for _ in texts])


class RagQueryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        get_retriever.cache_clear()
        CountingEmbedder.instances = 0
        CountingEmbedder.encode_calls = 0

    def tearDown(self) -> None:
        get_retriever.cache_clear()

    def test_reuses_retriever_and_query_embedding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            index_dir = root / "index"
            store = VectorStore(dim=2, embedder_name="counting")
            store.add(
                np.array([[1.0, 0.0]]),
                [{
                    "book": "Book",
                    "label": "",
                    "printed_page": 1,
                    "pdf_page": 1,
                    "file": "book.pdf",
                    "text": "passage",
                }],
            )
            store.save(str(index_dir))

            config = root / "config.toml"
            config.write_text(
                "[rag]\n"
                'embedder = "hashing"\n'
                f'index_dir = "{index_dir}"\n'
            )

            with patch("rag.query.get_embedder", return_value=CountingEmbedder()):
                retrieve("same query", config_path=str(config))
                retrieve("same query", config_path=str(config))

        self.assertEqual(CountingEmbedder.instances, 1)
        self.assertEqual(CountingEmbedder.encode_calls, 1)


if __name__ == "__main__":
    unittest.main()
