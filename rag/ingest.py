"""Ingest configured PDFs into the local vector store.

Per-page extraction so every chunk carries its page number. Long pages are
sub-chunked (with overlap) but each sub-chunk keeps its page, so citations stay
page-exact. Run:  python -m rag.ingest
"""
from __future__ import annotations

import sys

from pypdf import PdfReader

from .config import Config, embedder_kwargs, load_config
from .embedder import get_embedder
from .store import VectorStore


def chunk_page(text: str, chunk_chars: int, overlap: int) -> list[str]:
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if overlap >= chunk_chars:
        raise ValueError("chunk_overlap must be smaller than chunk_chars")

    text = (text or "").strip()
    if not text or len(text) < 50:
        return []
    if len(text) <= chunk_chars:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_chars
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if len(c) >= 50]


def ingest(cfg: Config) -> VectorStore:
    embedder = get_embedder(cfg.embedder, **embedder_kwargs(cfg))
    store = VectorStore(dim=embedder.dim, embedder_name=embedder.model_name)

    texts: list[str] = []
    metas: list[dict] = []

    for book in cfg.books:
        for bf in book.files:
            try:
                reader = PdfReader(bf.path)
            except Exception as e:
                print(f"  !! cannot open {bf.path}: {e}", file=sys.stderr)
                continue

            n_pages = len(reader.pages)
            pages_with_text = 0
            for i, page in enumerate(reader.pages, start=1):  # 1-based PDF page
                page_text = page.extract_text() or ""
                if page_text.strip():
                    pages_with_text += 1
                for chunk in chunk_page(page_text, cfg.chunk_chars, cfg.chunk_overlap):
                    texts.append(chunk)
                    metas.append({
                        "book": book.name,
                        "label": bf.label,
                        "file": bf.path,
                        "pdf_page": i,
                        "printed_page": i - bf.page_offset,
                        "text": chunk,
                    })

            label = f" [{bf.label}]" if bf.label else ""
            print(f"  {book.name}{label}: {pages_with_text}/{n_pages} pages had text")
            if pages_with_text == 0:
                print(f"     !! NO TEXT EXTRACTED — likely a scanned PDF. "
                      f"OCR it first (e.g. `ocrmypdf in.pdf out.pdf`).",
                      file=sys.stderr)

    if not texts:
        raise SystemExit("No text ingested. Check books_dir and book overrides in config.toml.")

    device = getattr(embedder, "device", "cpu")
    print(f"  embedding {len(texts)} chunks with {embedder.model_name} on {device} ...")
    vectors = embedder.encode(texts)
    store.add(vectors, metas)
    store.save(cfg.index_dir)
    print(f"  saved index -> {cfg.index_dir}  ({len(texts)} chunks)")
    return store


if __name__ == "__main__":
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    print("Ingesting...")
    ingest(cfg)
