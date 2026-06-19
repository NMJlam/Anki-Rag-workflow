# Obsidian -> RAG -> Anki

A pipeline that keeps Anki flashcards in sync with your Obsidian notes. Use
this README to install the project, configure your local paths, ingest
textbooks, and run the proposal/commit workflow.

## Prerequisites

- Python 3.13+
- `uv` ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
- Anki with the AnkiConnect add-on for committing cards
- Optional: `poppler` and `ocrmypdf` for PDF diagnosis and OCR

On macOS, optional PDF tools can be installed with:

```bash
brew install poppler ocrmypdf
```

## Setup

```bash
git clone https://github.com/NMJlam/Anki-Rag-workflow.git
cd anki-rag
uv sync
```

Create a `.env` file with your OpenRouter API key:

```bash
echo 'OPEN_ROUTER_TOKEN=sk-or-v1-your-key-here' > .env
```

## Configure AnkiConnect

1. Open Anki.
2. Go to **Tools -> Add-ons -> Get Add-ons...**.
3. Enter code **2055492159**.
4. Restart Anki.

Verify AnkiConnect is running:

```bash
curl localhost:8765 -X POST -d '{"action": "deckNames", "version": 6}'
```

Keep Anki open whenever you run `anki-commit`.

## Configure Paths

Edit `config.toml` and set your Obsidian vault path:

```toml
[paths]
vault = "/path/to/your/Obsidian/vault"
state = "data/card_state.sqlite"

[rag]
books_dir = "books"
index_dir = "data/index"
embedder = "sentence-transformers"
embedder_device = "auto"  # uses Apple Metal/MPS on Apple Silicon when available
embed_batch_size = 32
```

Relative paths in `config.toml` are resolved from the config file's directory.

## Add Textbooks

Drop PDF textbooks into `books/`.

If a PDF needs a display name or page-number calibration, add an override in
`config.toml`:

```toml
[[rag.book_overrides]]
path = "ostep-chapters.pdf"
name = "OSTEP"
label = ""
page_offset = 0
```

To calibrate `page_offset`, open the PDF, go to the page showing printed page
`1`, note the viewer's page number, then subtract `1`.

If a scanned PDF extracts no text, run OCR and ingest again:

```bash
ocrmypdf in.pdf out.pdf
```

## Build The Textbook Index

Run this once after setup, and again whenever you add or change textbooks:

```bash
uv run python -m rag.ingest
```

## Propose Card Changes

```bash
uv run anki-propose
```

This writes proposal files into your Obsidian vault:

- `New cards.md`
- `Changed cards.md`

Review these files before committing. You can delete unwanted proposals, edit
card text, or change deck assignments.

To discard the current proposal instead of committing it:

```bash
uv run reject
```

## Commit To Anki

Make sure Anki is open, then run:

```bash
uv run anki-commit
```

## Offline Smoke Test

This test does not require an API key:

```bash
uv run python tests/make_sample_pdf.py
uv run python -m rag.ingest tests/books.test.toml
uv run python -m rag.query "who handles a TLB miss" -k 1 --config tests/books.test.toml
```

Expected result: a hit citing `Sample Textbook · p.3 (pdf p.5)` with TLB text.
