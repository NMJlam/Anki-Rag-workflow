"""Numpy-based vector store with cosine similarity search.

Persists to disk as .npy (vectors) + .json (metadata). Swap FAISS/Chroma
behind this interface if you ever need to scale beyond thousands of chunks.
"""
from __future__ import annotations

import json
import os

import numpy as np


class VectorStore:

    def __init__(self, dim: int, embedder_name: str = ""):
        self.dim = dim
        self.embedder_name = embedder_name
        self._vectors: np.ndarray | None = None  # (N, dim)
        self._metas: list[dict] = []

    def add(self, vectors: np.ndarray, metas: list[dict]) -> None:
        if vectors.shape[0] != len(metas):
            raise ValueError(f"vectors count {vectors.shape[0]} != metas count {len(metas)}")
        if vectors.shape[1] != self.dim:
            raise ValueError(f"vector dim {vectors.shape[1]} != expected {self.dim}")
        if self._vectors is None:
            self._vectors = vectors
        else:
            self._vectors = np.vstack([self._vectors, vectors])
        self._metas.extend(metas)

    def search(self, query_vec: np.ndarray, k: int = 5) -> list[tuple[float, dict]]:
        if self._vectors is None or len(self._metas) == 0:
            return []
        # cosine similarity
        norms = np.linalg.norm(self._vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normed = self._vectors / norms

        q_norm = np.linalg.norm(query_vec)
        if q_norm == 0:
            return []
        q_normed = query_vec / q_norm

        sims = normed @ q_normed
        k = min(k, len(sims))
        top_idx = np.argpartition(-sims, k)[:k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]

        return [(float(sims[i]), self._metas[i]) for i in top_idx]

    def save(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        np.save(os.path.join(directory, "vectors.npy"), self._vectors)
        with open(os.path.join(directory, "meta.json"), "w") as f:
            json.dump({
                "dim": self.dim,
                "embedder_name": self.embedder_name,
                "metas": self._metas,
            }, f)

    @classmethod
    def load(cls, directory: str) -> VectorStore:
        with open(os.path.join(directory, "meta.json")) as f:
            data = json.load(f)
        store = cls(dim=data["dim"], embedder_name=data.get("embedder_name", ""))
        store._vectors = np.load(os.path.join(directory, "vectors.npy"))
        store._metas = data["metas"]
        return store
