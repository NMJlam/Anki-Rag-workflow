"""Entrypoint for Brick 4 — user runs this after reviewing diff files.

Usage:
    uv run anki-commit [vault_path] [state_path]

Flags:
    --skip-backup   Skip the Anki backup step (for testing only)
    --skip-sync     Skip the AnkiWeb sync step
"""
from __future__ import annotations

import sys

from commit.apply import apply_commit
from sync.config import load_app_config


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    app_config = load_app_config()
    vault = args[0] if len(args) > 0 else app_config.vault_path
    state = args[1] if len(args) > 1 else app_config.state_path

    print("=== Anki Commit ===")
    apply_commit(
        vault,
        state,
        skip_backup="--skip-backup" in flags,
        skip_sync="--skip-sync" in flags,
    )


if __name__ == "__main__":
    main()
