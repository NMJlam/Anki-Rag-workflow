# Obsidian -> RAG -> Anki

A pipeline that keeps Anki flashcards in sync with your Obsidian notes.
Changed notes are cross-checked against textbooks via a local RAG index,
and card proposals are written as human-readable diff files for review.
Nothing reaches Anki without explicit approval.

```
Obsidian vault          RAG (local, free)          Anki
  notes ──────> anki-propose ──────> diff files ──────> anki-commit ──────> cards
  (created/       scan + hash         New cards.md        backup +          AnkiWeb
   edited)        + LLM xcheck        Changed cards.md    add/delete        sync
```

---

## Project structure

```
anki-rag/
├── cli/                    # entrypoints — the things you run
│   ├── propose.py          #   orchestrator: scan vault + cross-check + write diffs
│   └── commit.py           #   orchestrator: parse diffs + apply to Anki
├── rag/                    # Brick 1 — local retrieval (no API)
│   ├── config.py           #   loads books.yaml
│   ├── embedder.py         #   sentence-transformers + offline test embedder
│   ├── store.py            #   numpy vector store (cosine search)
│   ├── ingest.py           #   PDF -> per-page chunks -> embeddings -> index
│   └── query.py            #   question -> top-k pages + citations
├── sync/                   # Brick 2 — vault scan + index
│   ├── vault.py            #   scan notes, compute hashes, diff vs index
│   └── index.py            #   read/write sync_index.json
├── reason/                 # Brick 3 — LLM cross-check (OpenRouter)
│   ├── llm.py              #   OpenRouter client (DeepSeek V3.2)
│   ├── crosscheck.py       #   note + RAG -> concepts + verdicts -> proposals
│   └── emit.py             #   write New cards.md / Changed cards.md
├── commit/                 # Brick 4 — apply to Anki (AnkiConnect)
│   ├── anki.py             #   AnkiConnect wrapper (localhost:8765)
│   └── apply.py            #   parse diffs, backup, add/delete, update index
├── tests/                  # test fixtures + smoke-test utilities
│   ├── make_sample_pdf.py  #   generates a sample PDF for offline testing
│   └── books.test.yaml     #   offline smoke-test config (hashing embedder)
├── docs/
│   └── project_spec.md     #   design specification
├── data/
│   ├── pdfs/               #   drop textbook PDFs here
│   ├── index/              #   generated vector index
│   └── sync_index.json     #   note -> card mapping (authoritative state)
├── books.yaml              # textbook config: paths + page-offset calibration
├── pyproject.toml
└── uv.lock
```

---

## Prerequisites

- **Python 3.13+** (managed via `uv`)
- **uv** — Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **Anki** with the **AnkiConnect** add-on (for the commit step)
- Optional: `brew install poppler ocrmypdf` for PDF diagnosis and OCR of scanned PDFs

## Setup

```bash
git clone <repo-url> && cd anki-rag

# Install dependencies
uv sync

# Create .env with your OpenRouter API key
echo 'OPEN_ROUTER_TOKEN=sk-or-v1-your-key-here' > .env
```

### Configure AnkiConnect

1. Open Anki
2. Go to **Tools -> Add-ons -> Get Add-ons...**
3. Enter code **2055492159** and click OK
4. Restart Anki

AnkiConnect runs a local server on `localhost:8765`. Verify it's working:

```bash
curl localhost:8765 -X POST -d '{"action": "deckNames", "version": 6}'
```

### Add your textbooks

Drop PDFs into `data/pdfs/`, then edit `books.yaml`:

```yaml
embedder: sentence-transformers
books:
  - name: "OSTEP"
    files:
      - path: "data/pdfs/ostep-chapters.pdf"
        label: ""
        page_offset: 0    # see "Calibrate page numbers" below
```

**Calibrate page numbers:** open the PDF, go to the page showing printed "1",
note the viewer's page number, subtract 1. That's your `page_offset`.
OSTEP per-chapter PDFs are usually `page_offset: 0`.

---

## Workflow

### 1. Ingest textbooks into RAG (one-time, re-run when adding books)

```bash
uv run python -m rag.ingest
```

Builds a local vector index from your PDFs. No API calls, nothing leaves your machine.

### 2. Propose card changes

```bash
uv run anki-propose
```

This scans your Obsidian vault for new or edited notes, cross-checks each
concept against the textbooks via RAG, and writes two diff files into your vault:

- **`New cards.md`** — cards for notes that have no existing managed cards
- **`Changed cards.md`** — delete/add proposals for notes whose cards need updating

Each card includes the LLM's verdict (`consistent`, `adds detail`, or
`contradicts`) with page-exact citations from the textbooks.

### 3. Review in Obsidian

Open the diff files in Obsidian (or any editor). You can:

- Delete any card or proposal you don't want
- Edit fronts, backs, or decks
- Change the deck assignment (look for `<- pick` markers)

Only entries still present when you run commit will be applied.

### 4. Commit to Anki

```bash
uv run anki-commit
```

This will:

1. Parse the approved diff files
2. Back up your Anki collection (`.apkg` file saved to home directory)
3. Ensure the `Basic (tracked)` note type exists
4. Create any needed decks
5. Delete old cards (for changed proposals) and add new ones via AnkiConnect
6. Update `sync_index.json` with the new card mappings
7. Write a timestamped log to `Anki sync log.md`
8. Clean up the diff files
9. Trigger AnkiWeb sync

**Flags:**

```bash
uv run anki-commit --skip-backup    # skip the backup step (testing only)
uv run anki-commit --skip-sync      # skip the AnkiWeb sync step
```

**Anki must be open** with AnkiConnect running when you run commit.

---

## Smoke test (offline, no API key needed)

```bash
uv run python tests/make_sample_pdf.py
uv run python -m rag.ingest tests/books.test.yaml
uv run python -m rag.query "who handles a TLB miss" -k 1 --config tests/books.test.yaml
```

Expected: a hit citing **`Sample Textbook . p.3 (pdf p.5)`** with TLB text.

---

## How it works

The pipeline has four bricks:

| Brick | Module | What it does |
|-------|--------|-------------|
| 1 | `rag/` | Turns textbook PDFs into a searchable vector index. Local, free, no API. |
| 2 | `sync/` | Scans the Obsidian vault, hashes each note, diffs against `sync_index.json`. |
| 3 | `reason/` | For each changed note: extracts concepts, retrieves textbook passages, asks the LLM for a verdict, and writes the diff files. |
| 4 | `commit/` | Parses the approved diffs, backs up Anki, applies adds/deletes via AnkiConnect, updates the index. |

**Key design decisions:**

- **Change = delete + add.** Replacing a card resets its scheduling so the
  updated material resurfaces soon. This is intentional.
- **Cards are `Basic (tracked)`** with four fields: Front, Back, SourceNote,
  ContentHash. Only Front/Back are shown when studying.
- **Deck = vault folder.** A note in `Operating Systems/` goes to the
  `Operating Systems` deck. Frontmatter `deck:` overrides.
- **Books verify, never auto-correct.** A contradiction is surfaced to you
  with the exact page citation. You decide what to do.
- **Only manages cards it created.** The system only touches cards carrying
  SourceNote + ContentHash fields.

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | `OPEN_ROUTER_TOKEN` — your OpenRouter API key |
| `books.yaml` | Textbook paths, labels, page offsets, embedder choice |
| `data/sync_index.json` | Note-to-card mapping (auto-managed, don't hand-edit) |

Default vault path: `/Users/root1/Obsidian/quant` (pass as first arg to
`anki-propose` or `anki-commit` to override).

## Using RAG from code

```python
from rag.query import retrieve

hits = retrieve("who handles a TLB miss", k=5)
# -> [{'score', 'citation', 'book', 'label', 'printed_page',
#      'pdf_page', 'file', 'text'}, ...]
```

## Notes

- **Scanned PDFs** extract no text — ingest will warn. Fix with
  `ocrmypdf in.pdf out.pdf` and re-ingest.
- The vector store is brute-force cosine in numpy — instant for thousands of
  pages. Swap FAISS/Chroma behind `store.py` if needed.
- Tokens are spent only during `anki-propose` (the LLM cross-check step). RAG
  retrieval and commit are free. A quiet day with no note changes costs nothing.
