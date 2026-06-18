"""Entrypoint for Brick 4 — user runs this after reviewing diff files.

Usage:
    uv run anki-commit [vault_path] [state_path]
"""
from __future__ import annotations

import sys

from commit.apply import apply_commit
from sync.config import load_app_config


def main() -> None:
    args = sys.argv[1:]

    app_config = load_app_config()
    vault = args[0] if len(args) > 0 else app_config.vault_path
    state = args[1] if len(args) > 1 else app_config.state_path

    print("=== Anki Commit ===")
    apply_commit(vault, state)


if __name__ == "__main__":
    main()
