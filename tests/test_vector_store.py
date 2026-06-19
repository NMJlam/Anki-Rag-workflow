import unittest

import numpy as np

from rag.store import VectorStore


class VectorStoreTests(unittest.TestCase):
    def test_search_all_vectors_when_k_matches_vector_count(self) -> None:
        store = VectorStore(dim=2)
        store.add(
            np.array([[1.0, 0.0], [0.0, 1.0]]),
            [{"id": "x"}, {"id": "y"}],
        )

        results = store.search(np.array([1.0, 0.0]), k=2)

        self.assertEqual([meta["id"] for _, meta in results], ["x", "y"])

    def test_search_single_vector(self) -> None:
        store = VectorStore(dim=2)
        store.add(np.array([[1.0, 0.0]]), [{"id": "x"}])

        results = store.search(np.array([1.0, 0.0]), k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1]["id"], "x")


if __name__ == "__main__":
    unittest.main()
