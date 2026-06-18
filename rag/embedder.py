"""Embedding backends: real (sentence-transformers) and offline (hashing).

get_embedder(name) returns an embedder with .encode(texts) -> np.ndarray
and .dim / .model_name attributes.
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from typing import List

import numpy as np
from dotenv import load_dotenv

load_dotenv()


class Embedder(ABC):
    dim: int
    model_name: str

    @abstractmethod
    def encode(self, texts: List[str]) -> np.ndarray:
        ...


class SentenceTransformerEmbedder(Embedder):
    """Real semantic embeddings via sentence-transformers (runs locally)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_embedding_dimension()

    def encode(self, texts: List[str]) -> np.ndarray:
        return self._model.encode(texts, show_progress_bar=False,
                                  convert_to_numpy=True)


class HashingEmbedder(Embedder):
    """Deterministic, dependency-free embedder for offline tests.

    Maps each text to a fixed-dim vector via MD5 → byte unpacking.
    NOT semantic — only useful for smoke-testing the pipeline plumbing.
    """

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.model_name = f"hashing-{dim}"

    def encode(self, texts: List[str]) -> np.ndarray:
        vecs = []
        for t in texts:
            h = hashlib.md5(t.encode()).digest()
            # repeat hash bytes to fill dim
            repeated = (h * ((self.dim // len(h)) + 1))[:self.dim]
            vec = np.frombuffer(repeated, dtype=np.uint8).astype(np.float32)
            # normalize to unit vector for cosine similarity
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            vecs.append(vec)
        return np.array(vecs)


def get_embedder(name: str, **kwargs) -> Embedder:
    if name == "sentence-transformers":
        return SentenceTransformerEmbedder(**kwargs)
    if name == "hashing":
        return HashingEmbedder(**kwargs)
    raise ValueError(f"Unknown embedder: {name!r}. Use 'sentence-transformers' or 'hashing'.")
