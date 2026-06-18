# Obsidian → RAG → Anki — Project Spec & Handoff

> **For Claude Code.** This document is the complete design. The conversation that
> produced it is not available to you, so treat this as the single source of
> truth. Drop it at the repo root (optionally reference it from or rename it to
> `CLAUDE.md` so it auto-loads as project context). Brick 1 (RAG retrieval) is
> **built and verified**; bricks 2–4 are **specified but not built** — that's
> your job.

---

## 1. What we're building

A pipeline that keeps an Anki deck in sync with what the user is actually
learning. The user studies and takes notes in Obsidian; textbooks they've read
live in a local RAG; Anki holds the spaced-repetition cards. Each night the
system reads notes that **changed**, cross-checks the relevant facts against the
textbooks, and proposes card changes as two human-readable diff files. The user
reviews and approves; only then are changes applied to Anki. Nothing destructive
happens without explicit approval.

The novel part (and why a custom build, not just an existing Obsidian↔Anki
plugin): the LLM-driven step that asks "has the user learned something new here,
does it *contradict* a textbook, or merely *add detail*?" and turns that
judgement into card add/replace decisions with page-exact citations.

## 2. The three systems and their roles

- **Obsidian = the record of what's been learned, and the source of card
  candidates.** A note is "learned" when it is **created or edited**. The
  pipeline is **change-triggered, not tag-triggered**. Cards originate *here*.
- **Textbooks in RAG = verification and enrichment only.** They never spawn
  cards on their own, and they never silently "correct" a note. They confirm a
  note's claim, sharpen detail, and flag contradictions for the user to
  adjudicate.
- **Anki = the memorization layer that tracks Obsidian.** For each candidate:
  add if new, replace if the user's understanding changed, leave alone if
  already correct.

## 3. Architecture & topology

Two machines, two **separate** sync channels.

```
┌─────────────── HOST PC (Linux, headless, always on) ──────────────┐
│  • vault mirror (read)                                            │
│  • the RAG (PDFs + local vector index)  ← Brick 1, BUILT          │
│  • nightly proposal job at ~03:00 (cron) ← Bricks 2+3             │
│    reads changed notes, queries RAG, calls OpenRouter,            │
│    writes the two .md diff files into the vault.                  │
│  NO Anki here. Nothing destructive ever runs at night.            │
└───────────────────────────┬───────────────────────────────────────┘
                           │  Syncthing (vault + card_state.sqlite, both ways)
                           ▼
┌─────────────── LAPTOP (macOS, where the user is) ─────────────────┐
│  • the two .md files arrive overnight                            │
│  • user reviews / edits / approves in the morning                │
│  • user runs COMMIT here ← Brick 4                               │
│    backup → add/delete via AnkiConnect → update state → log      │
│  • Anki then syncs to AnkiWeb → phone                            │
└───────────────────────────────────────────────────────────────────┘
```

Rules that fall out of this topology — **do not violate**:

- **Syncthing** (free, P2P, bidirectional) syncs the **vault + `card_state.sqlite`**.
- **AnkiWeb** (Anki's own sync) is the *only* thing that moves the Anki
  collection between devices. **Never let Syncthing or any file-sync touch
  Anki's `collection.anki2`** — it's a live SQLite DB and copying it while Anki
  is open corrupts it.
- **Commit (the destructive step) runs on the laptop**, where Anki already runs
  with a GUI and AnkiConnect on localhost. The host never talks to Anki.

### Build phases

- **Phase 1 (now):** everything on the Mac — vault scan, RAG, propose *and*
  commit, all triggered by hand — to prove the logic.
- **Phase 2 (later):** move only the RAG + proposal job + the 03:00 schedule to
  the host. Commit stays on the laptop, unchanged.

Build and test on the Mac first. The code should not hard-code host paths.

## 4. End-to-end pipeline

```
        ┌──────────── NIGHTLY PROPOSE (≈03:00, unattended) ───────────┐
        │ [1] scan vault → hash each note → diff vs card_state.sqlite     │
        │      → list of created/edited notes since last run           │
        │ [2] for each changed note: LLM extracts atomic concepts      │
        │ [3] for each concept: RAG retrieve() → passages w/ pages →    │
        │      LLM verdict: consistent ✓ | adds detail | contradicts   │
        │ [4] route via card state:                                    │
        │       note has NO managed card → New cards.md      (++)      │
        │       note already has card(s) → Changed cards.md  (-- / ++) │
        │ [5] write the two .md files into the vault (diff format §7)   │
        └───────────────────────────────────────────────────────────────┘
                                   │  (user wakes, reviews)
        ┌──────────── COMMIT (daytime, user-triggered) ───────────────┐
        │ [6] user edits/deletes lines they don't want in the .md files │
        │ [7] run commit → BACKUP Anki first → for each ++ addNote,     │
        │      for each -- deleteNotes(card's note id) via AnkiConnect  │
        │ [8] update card_state.sqlite (note→card map, new hashes);      │
        │      move applied entries → Anki sync log.md; trigger sync    │
        └───────────────────────────────────────────────────────────────┘
```

Cost note: tokens are spent only in steps 2–3 (OpenRouter), and only for
**changed** notes. RAG retrieval is local/free. A quiet day costs ~nothing.

## 5. Locked design rules

These were decided with the user. Don't relitigate them in code.

1. **Learned = note created or edited.** Change-triggered.
2. **A change = delete the old card + add a new one** (NOT edit-in-place). This
   is deliberate: deleting + re-adding resets the card's scheduling so the
   changed material resurfaces soon instead of hiding behind a long interval.
   The user explicitly wants the "surprise" of re-learning edited facts.
3. **New vs Changed routing** is decided by whether the source note already has
   managed cards (per `card_state.sqlite`), not by the LLM.
4. **Books verify/enrich, never auto-correct the note.** A note-vs-book conflict
   is *surfaced* to the user, never silently fixed.
5. **The diff is `++` / `--`.** Every `--` carries a reason:
   `CONTRADICTION` (a book proved the old card wrong — **must** cite exact book +
   page) or `NEW DETAIL` (card was right but the note outgrew it — book cited
   only as consistent, or note-only edit with no book conflict).
6. **Cards are Basic only** (front/back). Note type: `Basic (tracked)` (§6).
7. **Backs are in bullet-point form.** Single-fact answer = one bullet; multi-part
   answer = several bullets kept as **one card** (do NOT split into one-bullet
   cards — enumeration recall is the point).
8. **One key atomic detail per card.** The LLM decides granularity (a new term, a
   changed step in a process, or a fact → its own card).
9. **Deck = vault folder** (a note in `Operating Systems/` → `Operating Systems`
   deck). Frontmatter `deck:` overrides. For a *new* note where the folder is
   ambiguous, the proposal marks the deck `← pick` for the user to choose.
   Changed cards inherit the existing card's deck (never re-ask).
10. **Approval gate.** Nothing reaches Anki without an explicit commit run.
    **Full Anki backup immediately before any destructive commit.**
11. **The two `.md` files are a pending tray** — regenerated each propose run;
    on commit, applied entries move to `Anki sync log.md` so the two files only
    ever show what's awaiting a decision. Anything the user didn't act on stays
    pending.
12. **Starting from scratch** — there are no pre-existing cards, so every card
    the system touches is one it created and linked. No fuzzy matching against
    legacy cards is needed. Keep it that way: only manage cards that carry the
    `SourceNote` + `ContentHash` fields.

Subjects/decks in use (folder names): e.g. `C++`, `Concurrency`,
`Operating Systems`, plus others the user adds. The user does deck filing in the
note (frontmatter) and picks the deck when prompted.

## 6. Card format — the `Basic (tracked)` note type

A custom Anki note type with **four fields**:

- `Front` — the question.
- `Back` — the answer, as Markdown/HTML bullet list (see rule 7).
- `SourceNote` — vault-relative path or note title (traceability + recovery).
- `ContentHash` — hash of `Front + Back` (identity, dedupe, safe deletion).

Card template shows only `Front` → `Back` when studying; `SourceNote` and
`ContentHash` are data-only fields (not shown on the card face). Brick 4 creates
this note type via AnkiConnect if it doesn't exist (verify `createModel`
signature against the installed AnkiConnect version).

## 7. The two diff files — exact format

The format below is **final** (the user signed off on it). Match it precisely;
the commit parser (Brick 4) depends on it.

### `New cards.md`

```
# New Cards — pending approval
# run 2026-06-18 03:00 · 2 new notes · edit freely, then run `commit`
# deck is pre-filled from the note's folder; "← pick" means choose a deck

## ++ from [[MLFQ]]      deck: Operating Systems
   xcheck: OSTEP · cpu-sched-mlfq · p.4–8 — consistent ✓

Front: In MLFQ, when two jobs sit in different queues, which one runs?
Back:
- The job in the higher-priority queue (Rule 1)

Front: In the revised MLFQ rules, what causes a job's priority to drop?
Back:
- Using up its time allotment at a level — total CPU time, summed across yields
- Not the number of times it releases the CPU
- This closes the gaming loophole

## ++ from [[lock_guard vs unique_lock]]   deck: C++? or Concurrency? ← pick
   xcheck: C++ Concurrency in Action 2e · §3.2.3 · p.~49–52 — adds detail

Front: Name two things std::unique_lock can do that std::lock_guard cannot.
Back:
- Defer locking, or unlock and relock manually
- Be moved (it's movable; lock_guard is not)
- (Also: timed locking, and use with condition variables)
```

### `Changed cards.md`

```
# Changed Cards / Proposals — pending approval
# run 2026-06-18 03:00 · each proposal = delete old card + add new
# delete+add is intentional: it resets scheduling so the change resurfaces soon

## Proposal 1 · CONTRADICTION · note [[TLB]] · deck: Operating Systems

-- DELETE  card 1718…023  ("Basic (tracked)")
   Front: Who handles a TLB miss?
   Back:
   - The hardware — the CPU walks the page table and fills the TLB
   why: Stated as always-hardware; the book shows it's architecture-dependent
   source: OSTEP · vm-tlbs · p.7

++ ADD
   Front: Who handles a TLB miss — and what does it depend on?
   Back:
   - Depends on the architecture
   - CISC/x86: hardware-managed — the MMU walks the page table itself
   - RISC/MIPS: software-managed — the miss traps to the OS, which installs it

## Proposal 2 · NEW DETAIL · note [[Spinlocks]] · deck: Operating Systems

-- DELETE  card 1718…044  ("Basic (tracked)")
   Front: What does a spinlock do while waiting for a lock?
   Back:
   - It spins, repeatedly checking the lock, using CPU
   why: Correct but incomplete — the note now captures *when* spinning pays off
   source: note edit only — no book conflict (book consistent, just fuller)

++ ADD
   Front: When is a spinlock appropriate, and when is it wasteful?
   Back:
   - Good on a multiprocessor with short critical sections
   - Wasteful on a single CPU — the spinner burns its slice while the holder
     can't run
```

The `-- DELETE` block shows the **exact existing card** (front/back). This is a
safety feature: if card-matching ever fingers the wrong card, the user catches it
at review before anything is deleted. The real `card …id` is filled from
`card_state.sqlite`.

## 8. Current status — Brick 1 (RAG retrieval): BUILT & VERIFIED

Local, free retrieval. No API. Lives in `rag/`. See `README.md` for run/calibrate
instructions. Verified end-to-end: per-page chunking, page-offset arithmetic, and
page-exact citations all round-trip correctly on a controlled test PDF.

Modules:
- `rag/config.py` — loads `config.toml` RAG settings, discovers PDFs from
  `books_dir`, and applies per-book `page_offset` calibration.
  `printed_page = pdf_page(1-based) − page_offset`.
- `rag/embedder.py` — `SentenceTransformerEmbedder` (real, default,
  `all-MiniLM-L6-v2`, runs locally) and `HashingEmbedder` (offline, for tests
  only). `get_embedder(name)`.
- `rag/store.py` — `VectorStore`: numpy brute-force cosine, persisted to
  `data/index/` as `.npy` + `.json`. Swap FAISS/Chroma behind it if needed.
- `rag/ingest.py` — PDF → per-page text (`pypdf`) → page-preserving sub-chunks →
  embeddings → store. Warns on scanned PDFs (no text layer → OCR needed).
- `rag/query.py` — the seam you'll build on:

```python
from rag.query import retrieve
hits = retrieve("who handles a TLB miss", k=5)
# -> [{'score', 'citation', 'book', 'label', 'printed_page',
#      'pdf_page', 'file', 'text'}, ...]
# 'citation' is already formatted: "OSTEP · vm-tlbs · p.7"
```

Config files: `config.toml` (real), `books.test.toml` (offline smoke test).
**Calibration is a manual per-book step** — verify one citation by hand against
the printed page (README §6). OSTEP per-chapter PDFs usually `page_offset: 0`;
single-file Manning books need the front-matter offset.

## 9. Build plan — Bricks 2, 3, 4

Suggested package layout to add:

```
sync/        # Brick 2: vault scan + index
  vault.py        scan notes, compute hashes, diff vs card state
  index.py        read/write card_state.sqlite (schema §10)
reason/      # Brick 3: concept extraction + cross-check (OpenRouter)
  llm.py          OpenRouter client (OpenAI-compatible), structured output
  crosscheck.py   note + retrieve() → concepts + verdicts → proposals
  emit.py         write New cards.md / Changed cards.md (format §7)
commit/      # Brick 4: apply to Anki (LAPTOP only)
  anki.py         AnkiConnect wrapper (addNote, deleteNotes, createModel, sync)
  apply.py        parse approved .md, backup, apply, update state, write log
propose.py   # orchestrates bricks 2+3 (the nightly job)
commit.py    # entrypoint for brick 4 (user runs this after approval)
```

### Brick 2 — vault scan + `card_state.sqlite`

- Walk the vault (configurable path; ignore `.obsidian/`, the two diff files,
  `Anki sync log.md`, and any `templates/`).
- For each `.md` note compute `sha256` of its content. Compare to
  `card_state.sqlite`. Emit a list of **created** (not in state) and **edited**
  (hash changed) notes. Deleted notes (in state, file gone) → later propose
  archiving their cards (low priority; can stub initially).
- Resolve each note's **deck** from its folder, with frontmatter `deck:`
  override.
- This brick is pure/local, no API. Make it independently runnable for testing.

### Brick 3 — concept extraction + cross-check (OpenRouter)

- **LLM client (`reason/llm.py`):** OpenRouter is OpenAI-compatible.
  `base_url = https://openrouter.ai/api/v1`, key from env `OPENROUTER_API_KEY`.
  Use the `openai` SDK or `requests`. Model is configurable (a mid-tier model is
  fine for this; keep it swappable). **Request structured JSON output** and parse
  defensively (strip code fences, validate schema, retry once on parse failure).
- **For each changed note:**
  1. LLM extracts the atomic concepts (new term / changed process step / fact),
     one key detail each (rule 8).
  2. For each concept, call `retrieve(concept_query, k=5)`; pass the top
     passages **with their `citation` strings** into the LLM and ask for a
     verdict: `consistent` | `adds_detail` | `contradicts`, plus the specific
     citation (`book`, `label`, `printed_page`) when the verdict is
     `adds_detail`/`contradicts`.
  3. Pull the note's existing managed cards from `card_state.sqlite` (front/back).
     Ask the LLM whether each concept maps to an existing card (→ replace, goes
     to Changed) or is new (→ goes to New). On a fresh vault every concept is new.
- **Routing + emit (`reason/emit.py`):** write the two files in the **exact
  format of §7**. Pre-fill deck from Brick 2; mark `← pick` when the folder is
  ambiguous. Every `--` line gets its reason; `CONTRADICTION` always includes the
  page-exact `source:`. Generate a stable `concept_key` per card so re-runs are
  deterministic.
- **The LLM↔orchestrator I/O contract (suggested JSON per concept):**

```json
{
  "concept_key": "tlb-miss-handler",
  "front": "Who handles a TLB miss — and what does it depend on?",
  "back_bullets": ["Depends on the architecture", "CISC/x86: hardware-managed …"],
  "verdict": "contradicts",
  "citation": {"book": "OSTEP", "label": "vm-tlbs", "printed_page": 7},
  "maps_to_existing_card": true,
  "reason": "Old card states always-hardware; book shows it's arch-dependent."
}
```

  The orchestrator (not the LLM) makes the final New-vs-Changed call using
  `maps_to_existing_card` and `card_state.sqlite` (card state is authoritative).

### Brick 4 — commit to Anki (laptop only)

- **`commit/anki.py`:** thin AnkiConnect wrapper. Endpoint
  `POST http://localhost:8765`, body `{"action","version":6,"params"}`.
  Stable actions you'll use: `deckNames`, `createDeck`, `modelNames`,
  `createModel`, `addNote`, `findNotes`, `notesInfo`, `deleteNotes`, `sync`.
  **Verify exact `createModel` params and any backup/export action against the
  installed AnkiConnect version's docs — don't assume.**
- **`commit/apply.py` flow:**
  1. Parse the (possibly user-edited) `New cards.md` + `Changed cards.md` per §7.
     Only act on entries still present (user deletes lines to reject them).
  2. **Backup the full collection first.** Options to verify against the user's
     Anki: AnkiConnect export to a `.colpkg`/`.apkg`, Anki's File → Export, or
     rely on Anki's automatic backups. Pick one, confirm it actually wrote a
     file, and abort the commit if the backup step fails.
  3. Ensure `Basic (tracked)` model exists (create if missing) and ensure each
     target deck exists (`createDeck`).
  4. For each `--`: `deleteNotes([note_id])` (the id from `card_state.sqlite`).
     For each `++`: `addNote(...)` with the four fields; populate `SourceNote`
     and `ContentHash`. Use `options.allowDuplicate=false`.
  5. Update `card_state.sqlite`: new hashes for processed notes; refresh each
     note's `cards` list with new `anki_note_id`/`concept_key`/`content_hash`.
  6. Move applied entries from the two diff files into `Anki sync log.md`
     (timestamped). Leave un-acted entries pending.
  7. Call `sync` so AnkiWeb propagates to the phone.
- Make commit **idempotent and resumable**: if it dies mid-run, re-running
  shouldn't double-add (check `ContentHash`/existing notes).

## 10. `card_state.sqlite` schema (authoritative state)

`card_state.sqlite` is the sole source of truth for tracked notes and cards.
At a high level it contains:

- `meta`: schema version and migration markers.
- `notes`: vault-relative note path, deck, committed file hash, pending file
  hash, last seen file hash, and last processed timestamp.
- `cards`: proposed/committed/rejected card rows, including note path, deck,
  question, answer, source, content hash, concept key, status, timestamps, and
  the Anki note id for committed cards.

The committed `cards` rows are what make "delete the *right* old card"
deterministic and what routes New vs Changed. Keep `SourceNote`/`ContentHash` on
the Anki side in sync with this so the system can recover if card state is ever
lost.

## 11. Constraints & gotchas (read before coding)

- **Don't sync Anki's DB via Syncthing.** AnkiWeb only. (§3)
- **Nothing destructive runs unattended.** The 03:00 job only *proposes*. Commit
  is always human-triggered, on the laptop, after approval, after a backup.
- **delete+add resets scheduling on purpose** — this is a feature, not a bug to
  "optimize away" into edit-in-place.
- **Page calibration is manual per book** and must be verified by hand once
  (§8 / README §6). OSTEP = per-chapter files, usually offset 0. Manning = front
  matter offset. Citations must show the *printed* page.
- **Scanned PDFs extract no text** → ingest warns; OCR with `ocrmypdf` first.
- **Code listings / figures extract messily from PDFs.** Cards are conceptual, so
  prose-explained concepts are fine; a fact living only inside a code block or
  diagram may cross-check weakly. Acceptable, just known.
- **The contradiction-vs-detail call is the one LLM judgement with teeth.**
  Early on, the user should spot-check that the label is right before trusting it.
  Consider logging the LLM's evidence so mislabels are auditable.
- **Don't hard-code host paths** — build/test on the Mac (Phase 1), then the
  RAG + propose job moves to the Linux host (Phase 2) unchanged.
- **Don't manage cards the system didn't create.** Only touch cards carrying the
  `SourceNote` + `ContentHash` fields. (Vault is starting from scratch.)
- **Verify AnkiConnect specifics** (createModel signature, backup/export action)
  against the installed version rather than trusting any remembered API shape.

## 12. Deferred / open decisions

- How many cards a note should yield — left to the LLM, kept atomic (rule 8).
- Commit trigger UX — the user expects to run a command on the laptop after
  reviewing; a one-button local page is a possible nicety, not required.
- Deleted-note handling (archive its cards) — can be stubbed in Brick 2 and
  fleshed out later.

---

### Suggested build order

Brick 2 (vault scan + index, pure/local, easy to test) → Brick 3 (cross-check +
emit, the heart) → Brick 4 (commit, laptop). Each brick should be independently
runnable and testable before wiring them into `propose.py` / `commit.py`.
