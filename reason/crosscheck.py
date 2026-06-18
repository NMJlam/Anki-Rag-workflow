"""Card generation from Obsidian propositions with explicit concept targets.

For each changed note:
  1. LLM receives the note's bullets.
  2. Generates direct Q&A cards for linked, highlighted, or named theory targets.
  3. Returns surviving cards + audit trail (pruned / not-self-contained / skipped).

Run standalone:  python -m reason.crosscheck [vault_path] [state_path]
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from rag.query import retrieve
from sync.anki_source import AnkiConnectError, regular_cards_for_deck
from sync.config import load_app_config
from sync.index import SyncIndex, load_index, state_db_path
from sync.state import card_content_hash
from sync.vault import ChangedNote, VaultDiff, scan_vault

from .llm import chat_json, DEFAULT_MODEL

# ------------------------------------------------------------------
# Data types for proposals
# ------------------------------------------------------------------


@dataclass
class CardProposal:
    """A single proposed direct Q&A card."""
    target: str               # the explicit concept being tested
    question: str             # a direct question about the target
    answer: str               # the answer drawn from the proposition
    source: str               # validating resource citation
    action: str = "add"        # add or replace
    replaces_anki_note_id: int | None = None
    replaces_front: str = ""
    replaces_answer: str = ""


@dataclass
class PrunedCard:
    target: str
    source: str
    reason: str


@dataclass
class NotSelfContained:
    source: str
    reason: str


@dataclass
class SkippedBullet:
    source: str
    reason: str


@dataclass
class NoteProposals:
    """All proposals for a single note."""
    rel_path: str
    deck: str
    is_new_note: bool
    proposals: list[CardProposal] = field(default_factory=list)
    pruned: list[PrunedCard] = field(default_factory=list)
    not_self_contained: list[NotSelfContained] = field(default_factory=list)
    skipped: list[SkippedBullet] = field(default_factory=list)


# ------------------------------------------------------------------
# System prompt
# ------------------------------------------------------------------

GENERATE_CARDS_SYSTEM = """\
You convert the user's own notes into spaced-repetition cards. The user studies a
topic, understands it, then writes it as short propositions in Obsidian, wikilinking
the key concepts inside each sentence.

**Prime directive: invent nothing.** Every card you produce must be answerable using
only the text of the single proposition it came from. You never add a definition,
mechanism, contrast, example, number, or fact that is not already written there.

You may **rephrase** the answer for clarity and natural reading — but rephrasing is
cosmetic only: same meaning, zero new facts.

## Input contract

You receive Obsidian markdown bullets. Treat **each proposition as one unit of work**.

- **Targets** are exactly three kinds:
  1. `[[wikilinks]]` — key concepts the user linked.
  2. `==highlights==` — spans the user marked for testing (values, quantities, named
     constants).
  3. The proposition's named theory term, mechanism, technique, rule, or method,
     even if it is not linked. Example: in "The OS uses time sharing to share the
     CPU across multiple [[Processes]]", the best target is `time sharing`.
- Prefer the theory term/mechanism over a linked object when both would test the
  same claim. Ask directly what the theory term is, means, or does.
- You may only create cards about explicit text present in the proposition. Never
  invent a target.
- Ignore stray markup (trailing `^^`, block IDs like `^abc123`, formatting symbols).
- **No targets in a bullet** → skip it, list it as skipped.

## The core algorithm (run per proposition)

1. **Split compound bullets.** If one bullet packs multiple independent claims (joined
   by `;`, `, and`, multiple sentences, etc.), split it at those boundaries into
   separate propositions, each carrying its own targets.
2. **Parse** each proposition into its sentence and its list of targets.
3. **Self-containment gate.** If the proposition depends on a referent not present in
   the proposition itself (`it`, `this`, `that`, `the above` with no in-bullet
   antecedent), the proposition is **not self-contained.** Do not card it; record it
   under `not_self_contained`.
4. **Generate candidates:** Write direct questions about the strongest target(s)
   based on what the proposition says. Prefer questions that test the named theory
   directly — e.g. "What is X?", "Define X", "What does X do in this context?",
   "How does X relate to Y?". The answer is the information from the proposition,
   written as a clear standalone sentence (not a fill-in-the-blank).
5. **Prune** the candidates using the pruning rules.
6. **Keep** every survivor.

## Pruning rules

**Drop a candidate if any of these is true:**

- **Trivial / tautological.** The question and answer say the same thing with no
  recall value.
- **Structural filler.** The target is a generic connective or container word.
- **Twin cue / redundant.** It targets the same idea as a stronger card from the same
  proposition, or its question reads almost identically to another survivor. Keep the
  strongest. If the proposition names a theory term or mechanism, that is usually the
  strongest target; drop object-oriented variants that merely ask what object is
  affected.
- **Not self-contained.** Failed the self-containment gate.
- **Boundary violation.** Answering correctly would require information not in the
  proposition.

**Keep a candidate if:** answering it genuinely requires recalling the information from
the proposition, and the answer is faithful to the source.

## Output format

Reply with a JSON object:
{
  "cards": [
    {
      "target": "<the explicit target being tested>",
      "Q": "<a direct question about the target, drawn from the proposition>",
      "A": "<the answer — a clear sentence using information from the proposition>",
      "source": "<the full original bullet, VERBATIM including [[]] and == markup>"
    }
  ],
  "pruned": [
    {
      "target": "<pruned target>",
      "source": "<verbatim bullet>",
      "reason": "<one-clause reason>"
    }
  ],
  "not_self_contained": [
    {
      "source": "<verbatim bullet>",
      "reason": "<what referent is missing>"
    }
  ],
  "skipped": [
    {
      "source": "<verbatim bullet>",
      "reason": "<why skipped>"
    }
  ]
}

## Guardrails — what you must NEVER do

1. Never add a fact, definition, mechanism, contrast, example, or number not in the proposition.
2. Never create cards about anything that is not explicit text in the proposition.
3. Never card a proposition that isn't self-contained; flag it instead.
4. Never ship two near-identical questions from one bullet.
5. Never teach, explain, or flag the user's understanding.
6. Never merge or relate information across separate bullets.
7. Never paraphrase `source` — it must be the original bullet verbatim.
8. Never assign tags, decks, or card types.
9. When tempted to make a "better" card that needs outside knowledge — don't. Prune.\
"""


MATCH_CARDS_SYSTEM = """\
You compare newly generated Anki cards against already committed Anki cards for
one source note.

Your job is card lifecycle matching, not teaching. Decide whether each new card:

- "keep": an existing card already tests the same learning objective and its answer
  has the same meaning. Different wording, citation changes, or minor clarity edits
  are still keep.
- "replace": an existing card tests the same learning objective, but the new answer
  materially changes, corrects, narrows, or expands what must be remembered.
- "add": no existing card tests the same learning objective.

Match semantically. Do not require identical wording. However, do not collapse cards
that only share a broad topic; they must test the same recall target.

Use each existing card at most once. Prefer the most specific matching existing card.

Reply with JSON:
{
  "matches": [
    {
      "new_index": 0,
      "action": "keep|replace|add",
      "existing_anki_note_id": 123,
      "reason": "short reason"
    }
  ]
}

For "add", existing_anki_note_id must be null.
For "keep" or "replace", existing_anki_note_id must be the matched existing card id.
"""


# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------

_card_identity_hash = card_content_hash  # back-compat alias for tests


def _normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _question_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()


def _classify_card_change(
    *,
    question: str,
    answer: str,
    source: str,
    existing_cards: list[dict],
    used_existing_ids: set[int],
) -> tuple[str, dict | None]:
    """Return add/replace/keep for a proposed card against committed cards."""
    content_hash = card_content_hash(source, answer)
    for existing in existing_cards:
        anki_note_id = existing.get("anki_note_id")
        if existing.get("content_hash") == content_hash:
            if isinstance(anki_note_id, int):
                used_existing_ids.add(anki_note_id)
            return "keep", existing

    unused = [
        card for card in existing_cards
        if card.get("anki_note_id") not in used_existing_ids
    ]
    for existing in unused:
        if _normalize_text(existing.get("front", "")) == _normalize_text(question):
            anki_note_id = existing.get("anki_note_id")
            if isinstance(anki_note_id, int):
                used_existing_ids.add(anki_note_id)
            if _normalize_text(existing.get("answer", "")) == _normalize_text(answer):
                return "keep", existing
            return "replace", existing

    similar = [
        (_question_similarity(question, existing.get("front", "")), existing)
        for existing in unused
    ]
    if similar:
        score, existing = max(similar, key=lambda item: item[0])
        if score >= 0.86:
            anki_note_id = existing.get("anki_note_id")
            if isinstance(anki_note_id, int):
                used_existing_ids.add(anki_note_id)
            if _normalize_text(existing.get("answer", "")) == _normalize_text(answer):
                return "keep", existing
            return "replace", existing

    return "add", None


def _semantic_card_changes(
    *,
    new_cards: list[dict],
    existing_cards: list[dict],
    model: str = DEFAULT_MODEL,
) -> dict[int, tuple[str, dict | None]]:
    """Use the LLM to semantically map generated cards to committed cards."""
    if not new_cards or not existing_cards:
        return {}

    payload = {
        "existing_cards": [
            {
                "anki_note_id": card.get("anki_note_id"),
                "question": card.get("front", ""),
                "answer": card.get("answer", ""),
                "source": card.get("source", ""),
            }
            for card in existing_cards
        ],
        "new_cards": [
            {
                "new_index": i,
                "target": card.get("target", ""),
                "question": card.get("question", ""),
                "answer": card.get("answer", ""),
                "source": card.get("source", ""),
            }
            for i, card in enumerate(new_cards)
        ],
    }

    try:
        result = chat_json([
            {"role": "system", "content": MATCH_CARDS_SYSTEM},
            {"role": "user", "content": json.dumps(payload)},
        ], model=model, temperature=0.0)
    except Exception as exc:
        print(f"      WARNING: semantic card matching failed: {exc}")
        return {}

    existing_by_id = {}
    for card in existing_cards:
        try:
            anki_note_id = int(card.get("anki_note_id"))
        except (TypeError, ValueError):
            continue
        existing_by_id[anki_note_id] = card
    matches: dict[int, tuple[str, dict | None]] = {}
    used_existing_ids: set[int] = set()
    for match in result.get("matches", []):
        try:
            new_index = int(match.get("new_index"))
        except (TypeError, ValueError):
            continue
        if new_index < 0 or new_index >= len(new_cards):
            continue

        action = str(match.get("action", "")).strip().lower()
        if action not in {"keep", "replace", "add"}:
            continue

        try:
            existing_id = int(match.get("existing_anki_note_id"))
        except (TypeError, ValueError):
            existing_id = None
        if action == "add":
            matches[new_index] = ("add", None)
            continue

        existing = existing_by_id.get(existing_id)
        if existing is None or existing_id in used_existing_ids:
            continue
        used_existing_ids.add(existing_id)
        matches[new_index] = (action, existing)

    return matches


def _validating_source(card: dict, *, config_path: str) -> str:
    """Return the page-exact citation for the best validating textbook page."""
    query = " ".join(
        part for part in [
            card.get("target", ""),
            card.get("Q", ""),
            card.get("A", ""),
            card.get("source", ""),
        ] if part
    )
    if not query.strip():
        return card.get("source", "")

    try:
        results = retrieve(query, k=1, config_path=config_path)
    except Exception as exc:
        print(f"      WARNING: citation lookup failed: {exc}")
        return card.get("source", "")

    if not results:
        return card.get("source", "")

    return results[0]["citation"]


def generate_cards(note_content: str, *, model: str = DEFAULT_MODEL) -> dict:
    """LLM generates direct Q&A cards from a note's propositions.

    Returns the raw JSON dict with keys: cards, pruned, not_self_contained, skipped.
    """
    result = chat_json([
        {"role": "system", "content": GENERATE_CARDS_SYSTEM},
        {"role": "user", "content": note_content},
    ], model=model)
    return result


def process_note(
    note: ChangedNote,
    index: SyncIndex,
    *,
    model: str = DEFAULT_MODEL,
    config_path: str = "config.toml",
) -> NoteProposals:
    """Full pipeline for one changed note: generate cards from propositions."""
    note_entry = index.get_note(note.rel_path)
    existing_cards = []
    if note_entry and note_entry.cards:
        existing_cards = [
            {
                "anki_note_id": c.anki_note_id,
                "concept_key": c.concept_key,
                "content_hash": c.content_hash,
                "front": c.front,
                "answer": c.answer,
                "source": c.source,
            }
            for c in note_entry.cards
        ]
    if note.deck:
        try:
            regular_cards = regular_cards_for_deck(note.deck)
        except AnkiConnectError as exc:
            print(f"      WARNING: could not load regular Anki cards for deck {note.deck}: {exc}")
            regular_cards = []
        seen_ids = {
            card.get("anki_note_id")
            for card in existing_cards
            if card.get("anki_note_id") is not None
        }
        for card in regular_cards:
            if card.get("anki_note_id") not in seen_ids:
                existing_cards.append(card)
                seen_ids.add(card.get("anki_note_id"))

    is_new_note = len(existing_cards) == 0

    # Generate cards
    print(f"    generating cards from {note.rel_path} ...")
    result = generate_cards(note.content, model=model)

    proposals = NoteProposals(
        rel_path=note.rel_path, deck=note.deck, is_new_note=is_new_note
    )

    generated_cards = []
    for card in result.get("cards", []):
        generated_cards.append({
            "target": card.get("target", ""),
            "question": card.get("Q", ""),
            "answer": card.get("A", ""),
            "source": _validating_source(card, config_path=config_path),
        })

    semantic_matches: dict[int, tuple[str, dict | None]] = {}
    if not is_new_note:
        semantic_matches = _semantic_card_changes(
            new_cards=generated_cards,
            existing_cards=existing_cards,
            model=model,
        )

    # Parse cards
    used_existing_ids: set[int] = set()
    for i, card in enumerate(generated_cards):
        target = card.get("target", "")
        question = card.get("question", "")
        answer = card.get("answer", "")
        source = card.get("source", "")

        action = "add"
        replacement = None
        if not is_new_note:
            semantic = semantic_matches.get(i)
            if semantic:
                action, replacement = semantic
                if replacement and isinstance(replacement.get("anki_note_id"), int):
                    used_existing_ids.add(replacement["anki_note_id"])
            else:
                action, replacement = _classify_card_change(
                    question=question,
                    answer=answer,
                    source=source,
                    existing_cards=existing_cards,
                    used_existing_ids=used_existing_ids,
                )
            if action == "keep":
                continue

        proposals.proposals.append(CardProposal(
            target=target,
            question=question,
            answer=answer,
            source=source,
            action=action,
            replaces_anki_note_id=(
                replacement.get("anki_note_id") if replacement else None
            ),
            replaces_front=(
                replacement.get("front", "") if replacement else ""
            ),
            replaces_answer=(
                replacement.get("answer", "") if replacement else ""
            ),
        ))

    # Parse audit trail
    for p in result.get("pruned", []):
        proposals.pruned.append(PrunedCard(
            target=p.get("target", ""),
            source=p.get("source", ""),
            reason=p.get("reason", ""),
        ))

    for nsc in result.get("not_self_contained", []):
        proposals.not_self_contained.append(NotSelfContained(
            source=nsc.get("source", ""),
            reason=nsc.get("reason", ""),
        ))

    for s in result.get("skipped", []):
        proposals.skipped.append(SkippedBullet(
            source=s.get("source", ""),
            reason=s.get("reason", ""),
        ))

    return proposals


def process_all_notes(
    diff: VaultDiff,
    index: SyncIndex,
    *,
    model: str = DEFAULT_MODEL,
    config_path: str = "config.toml",
) -> list[NoteProposals]:
    """Process all changed notes and return proposals."""
    all_proposals = []
    for note in diff.changed:
        props = process_note(note, index, model=model, config_path=config_path)
        all_proposals.append(props)
    return all_proposals


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    app_config = load_app_config()
    vault_dir = sys.argv[1] if len(sys.argv) > 1 else app_config.vault_path
    state_path = sys.argv[2] if len(sys.argv) > 2 else app_config.state_path

    print(f"Vault:  {vault_dir}")
    print(f"State:  {state_db_path(state_path)}")

    idx = load_index(state_path)
    diff = scan_vault(vault_dir, idx)
    print(f"  {diff.summary()}")

    if not diff.changed:
        print("  nothing to process")
        sys.exit(0)

    results = process_all_notes(diff, idx)
    for np in results:
        print(f"\n  {np.rel_path} (deck: {np.deck}, {'NEW' if np.is_new_note else 'CHANGED'}):")
        for p in np.proposals:
            print(f"    Q: {p.question}")
            print(f"    A: {p.answer}")
        if np.pruned:
            print("    PRUNED:")
            for pr in np.pruned:
                print(f"      - {pr.target} — {pr.reason}")
        if np.not_self_contained:
            print("    NOT SELF-CONTAINED:")
            for nsc in np.not_self_contained:
                print(f"      - {nsc.source[:60]}... — {nsc.reason}")
        if np.skipped:
            print("    SKIPPED:")
            for s in np.skipped:
                print(f"      - {s.source[:60]}... — {s.reason}")
