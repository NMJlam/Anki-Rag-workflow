"""Load RAG/book settings from config.toml into typed config objects."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class BookFile:
    path: str
    label: str = ""
    page_offset: int = 0


@dataclass
class Book:
    name: str
    files: List[BookFile] = field(default_factory=list)


@dataclass
class Config:
    embedder: str = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    index_dir: str = "data/index"
    chunk_chars: int = 1200
    chunk_overlap: int = 200
    books: List[Book] = field(default_factory=list)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _matching_override(book_path: Path, overrides: list[dict]) -> dict:
    for override in overrides:
        raw_path = override.get("path") or override.get("file")
        if raw_path is None:
            continue
        override_path = Path(raw_path).expanduser()
        candidates = {str(raw_path), override_path.name, override_path.stem}
        try:
            candidates.add(str(override_path.resolve()))
        except OSError:
            pass
        if (
            str(book_path) in candidates
            or str(book_path.resolve()) in candidates
            or book_path.name in candidates
            or book_path.stem in candidates
        ):
            return override
    return {}


def _book_from_pdf(pdf_path: Path, overrides: list[dict]) -> Book:
    override = _matching_override(pdf_path, overrides)
    return Book(
        name=override.get("name", pdf_path.stem),
        files=[
            BookFile(
                path=str(pdf_path),
                label=override.get("label", ""),
                page_offset=int(override.get("page_offset", 0)),
            )
        ],
    )


def load_config(path: str | Path = "config.toml") -> Config:
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    raw_rag = raw.get("rag", raw)

    cfg = Config(
        embedder=raw_rag.get("embedder", "sentence-transformers"),
        model=raw_rag.get("model", "all-MiniLM-L6-v2"),
        index_dir=str(raw_rag.get("index_dir", "data/index")),
        chunk_chars=int(raw_rag.get("chunk_chars", 1200)),
        chunk_overlap=int(raw_rag.get("chunk_overlap", 200)),
    )

    base_dir = config_path.parent
    if not Path(cfg.index_dir).is_absolute():
        cfg.index_dir = str((base_dir / cfg.index_dir).resolve())

    books_dir = Path(raw_rag.get("books_dir", "books")).expanduser()
    if not books_dir.is_absolute():
        books_dir = (base_dir / books_dir).resolve()

    overrides = [item for item in _as_list(raw_rag.get("book_overrides")) if isinstance(item, dict)]
    if books_dir.is_dir():
        for pdf_path in sorted(p for p in books_dir.rglob("*") if p.suffix.lower() == ".pdf"):
            cfg.books.append(_book_from_pdf(pdf_path.resolve(), overrides))

    return cfg
