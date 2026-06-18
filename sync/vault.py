"""Brick 2 — scan the Obsidian vault and diff against sync_index.json.

Walk the vault, hash each .md note, compare to the index, and emit lists of
created / edited / deleted notes.  Resolve each note's deck from its folder
path, with frontmatter `deck:` override.

Run standalone:  python -m sync.vault [vault_path] [index_path]
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .index import SyncIndex, load_index

# Directories and files to skip when walking the vault.
IGNORE_DIRS = {".obsidian", "templates"}
IGNORE_FILES = {"New cards.md", "Changed cards.md", "Anki sync log.md"}

# Regex for YAML frontmatter block at the top of a note.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
# Finds `deck: <value>` inside frontmatter.
_DECK_RE = re.compile(r"^deck:\s*(.+)$", re.MULTILINE)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _extract_deck_from_frontmatter(content: str) -> Optional[str]:
    """Return the `deck:` value from YAML frontmatter, or None."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    dm = _DECK_RE.search(m.group(1))
    if not dm:
        return None
    return dm.group(1).strip()


def _resolve_deck(rel_path: str, content: str) -> str:
    """Deck = frontmatter `deck:` override, else top-level vault folder.

    For a note at `Virtualisation/The Abstraction/Processes.md` the folder-based
    deck is `Virtualisation` (the first path component).  If the note has
    frontmatter `deck: Operating Systems`, that wins.
    """
    fm_deck = _extract_deck_from_frontmatter(content)
    if fm_deck:
        return fm_deck
    parts = Path(rel_path).parts
    if len(parts) > 1:
        return parts[0]
    # Note lives at the vault root — no obvious deck.
    return ""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

@dataclass
class ChangedNote:
    """A note that is new or has been edited since the last run."""
    rel_path: str        # vault-relative, forward slashes
    content: str
    content_hash: str
    deck: str
    is_new: bool         # True = created, False = edited


@dataclass
class DeletedNote:
    """A note that exists in the index but is gone from the vault."""
    rel_path: str


@dataclass
class VaultDiff:
    created: List[ChangedNote] = field(default_factory=list)
    edited: List[ChangedNote] = field(default_factory=list)
    deleted: List[DeletedNote] = field(default_factory=list)

    @property
    def changed(self) -> List[ChangedNote]:
        """All notes that need processing (created + edited)."""
        return self.created + self.edited

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"{len(self.created)} new")
        if self.edited:
            parts.append(f"{len(self.edited)} edited")
        if self.deleted:
            parts.append(f"{len(self.deleted)} deleted")
        return ", ".join(parts) if parts else "no changes"


def scan_vault(vault_path: str | Path, index: SyncIndex) -> VaultDiff:
    """Walk the vault and diff against the index.

    Returns a VaultDiff with created, edited, and deleted notes.
    """
    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"Vault not found: {vault}")

    diff = VaultDiff()
    seen_paths: set[str] = set()

    for md_file in sorted(vault.rglob("*.md")):
        # Skip ignored directories.
        rel = md_file.relative_to(vault)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        # Skip ignored files.
        if rel.name in IGNORE_FILES:
            continue

        rel_str = str(rel)  # OS-native separators on disk, but we normalize
        rel_str = rel_str.replace("\\", "/")  # always forward slashes in index
        seen_paths.add(rel_str)

        content = md_file.read_text(errors="replace")
        h = _sha256(content)
        deck = _resolve_deck(rel_str, content)

        existing = index.get_note(rel_str)
        if existing is None:
            diff.created.append(ChangedNote(
                rel_path=rel_str, content=content,
                content_hash=h, deck=deck, is_new=True,
            ))
        elif existing.hash != h:
            # Skip if already proposed with this exact content
            if existing.proposed_hash == h:
                continue
            diff.edited.append(ChangedNote(
                rel_path=rel_str, content=content,
                content_hash=h, deck=deck, is_new=False,
            ))
        # else: unchanged, skip

    # Deleted notes: in index but not on disk.
    for idx_path in index.all_paths():
        if idx_path not in seen_paths:
            diff.deleted.append(DeletedNote(rel_path=idx_path))

    return diff


# ------------------------------------------------------------------
# CLI entry point for standalone testing
# ------------------------------------------------------------------

if __name__ == "__main__":
    vault_dir = sys.argv[1] if len(sys.argv) > 1 else "/Users/root1/Obsidian/quant"
    index_path = sys.argv[2] if len(sys.argv) > 2 else "data/sync_index.json"

    print(f"Vault:  {vault_dir}")
    print(f"Index:  {index_path}")

    idx = load_index(index_path)
    result = scan_vault(vault_dir, idx)

    print(f"\n  {result.summary()}")
    for n in result.created:
        print(f"  + {n.rel_path}  (deck: {n.deck!r})")
    for n in result.edited:
        print(f"  ~ {n.rel_path}  (deck: {n.deck!r})")
    for n in result.deleted:
        print(f"  - {n.rel_path}")
