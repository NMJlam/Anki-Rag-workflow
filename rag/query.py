"""Retrieve the most relevant book pages for a query, with page-exact citations.

CLI:        python -m rag.query "who handles a TLB miss?" -k 5
Reusable:   retrieve(query, k) -> list of {score, citation, book, label,
            printed_page, pdf_page, file, text}   (used later by the
            cross-check / card-proposal step)
"""
from __future__ import annotations

import argparse
from functools import lru_cache

import numpy as np

from .config import embedder_kwargs, load_config
from .embedder import get_embedder
from .store import VectorStore


def format_citation(meta: dict) -> str:
    parts = [meta["book"]]
    if meta.get("label"):
        parts.append(meta["label"])
    parts.append(f"p.{meta['printed_page']}")
    return " · ".join(parts)


class Retriever:
    """Reusable RAG retriever for one config/index/embedder combination."""

    def __init__(self, config_path: str = "config.toml"):
        cfg = load_config(config_path)
        self.store = VectorStore.load(cfg.index_dir)
        self.embedder = get_embedder(cfg.embedder, **embedder_kwargs(cfg))
        if self.embedder.dim != self.store.dim:
            raise SystemExit(
                f"Embedder dim {self.embedder.dim} != index dim {self.store.dim}. "
                f"You ingested with a different embedder ('{self.store.embedder_name}'). "
                f"Re-ingest after changing the embedder in config.toml."
            )
        self._query_vectors: dict[str, np.ndarray] = {}

    def retrieve(self, query: str, k: int = 5) -> list[dict]:
        qvec = self._query_vectors.get(query)
        if qvec is None:
            qvec = self.embedder.encode([query])[0]
            self._query_vectors[query] = qvec

        results = []
        for score, meta in self.store.search(qvec, k=k):
            results.append({
                "score": round(score, 4),
                "citation": format_citation(meta),
                "book": meta["book"],
                "label": meta.get("label", ""),
                "printed_page": meta["printed_page"],
                "pdf_page": meta["pdf_page"],
                "file": meta["file"],
                "text": meta["text"],
            })
        return results


@lru_cache(maxsize=8)
def get_retriever(config_path: str = "config.toml") -> Retriever:
    return Retriever(config_path)


def retrieve(query: str, k: int = 5, config_path: str = "config.toml") -> list[dict]:
    return get_retriever(str(config_path)).retrieve(query, k=k)


def _retrieve_uncached(query: str, k: int = 5, config_path: str = "config.toml") -> list[dict]:
    """Reference implementation for tests/debugging without process caching."""
    cfg = load_config(config_path)
    store = VectorStore.load(cfg.index_dir)

    embedder = get_embedder(cfg.embedder, **embedder_kwargs(cfg))
    if embedder.dim != store.dim:
        raise SystemExit(
            f"Embedder dim {embedder.dim} != index dim {store.dim}. "
            f"You ingested with a different embedder ('{store.embedder_name}'). "
            f"Re-ingest after changing the embedder in config.toml."
        )

    qvec = embedder.encode([query])[0]
    results = []
    for score, meta in store.search(qvec, k=k):
        results.append({
            "score": round(score, 4),
            "citation": format_citation(meta),
            "book": meta["book"],
            "label": meta.get("label", ""),
            "printed_page": meta["printed_page"],
            "pdf_page": meta["pdf_page"],
            "file": meta["file"],
            "text": meta["text"],
        })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--full", action="store_true", help="print full chunk text")
    args = ap.parse_args()

    for r in retrieve(args.query, k=args.k, config_path=args.config):
        print(f"[{r['score']:.3f}] {r['citation']}  (pdf p.{r['pdf_page']})")
        snippet = r["text"] if args.full else (r["text"][:200] + "…" if len(r["text"]) > 200 else r["text"])
        print(f"        {snippet}\n")


if __name__ == "__main__":
    main()
