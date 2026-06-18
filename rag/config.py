"""Load books.yaml into typed config objects."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

import yaml


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


def load_config(path: str = "books.yaml") -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    cfg = Config(
        embedder=raw.get("embedder", "sentence-transformers"),
        model=raw.get("model", "all-MiniLM-L6-v2"),
        index_dir=raw.get("index_dir", "data/index"),
        chunk_chars=raw.get("chunk_chars", 1200),
        chunk_overlap=raw.get("chunk_overlap", 200),
    )

    base_dir = os.path.dirname(os.path.abspath(path))

    for b in raw.get("books", []):
        book_files = []
        for bf in b.get("files", []):
            file_path = bf["path"]
            # resolve relative paths against the config file's directory
            if not os.path.isabs(file_path):
                file_path = os.path.join(base_dir, file_path)
            book_files.append(BookFile(
                path=file_path,
                label=bf.get("label", ""),
                page_offset=bf.get("page_offset", 0),
            ))
        cfg.books.append(Book(name=b["name"], files=book_files))

    return cfg
