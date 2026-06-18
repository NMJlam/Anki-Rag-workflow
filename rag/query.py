"""Retrieve the most relevant book pages for a query, with page-exact citations.

CLI:        python -m rag.query "who handles a TLB miss?" -k 5
Reusable:   retrieve(query, k) -> list of {score, citation, book, label,
            printed_page, pdf_page, file, text}   (used later by the
            cross-check / card-proposal step)
"""
from __future__ import annotations

import argparse
from typing import Dict, List

from .config import load_config
from .embedder import get_embedder
from .store import VectorStore


def format_citation(meta: Dict) -> str:
    parts = [meta["book"]]
    if meta.get("label"):
        parts.append(meta["label"])
    parts.append(f"p.{meta['printed_page']}")
    return " · ".join(parts)


def retrieve(query: str, k: int = 5, config_path: str = "config.toml") -> List[Dict]:
    cfg = load_config(config_path)
    store = VectorStore.load(cfg.index_dir)

    embedder = get_embedder(cfg.embedder, **({"model_name": cfg.model}
                                            if cfg.embedder.startswith("s") else {}))
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
