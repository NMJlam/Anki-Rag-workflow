"""Application-level path configuration."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = "config.toml"
CONFIG_ENV_VAR = "ANKI_RAG_CONFIG"


@dataclass(frozen=True)
class AppConfig:
    vault_path: Path
    state_path: Path
    books_config: Path
    config_path: Path


def _resolve_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _paths_table(raw: dict[str, Any]) -> dict[str, Any]:
    paths = raw.get("paths", {})
    return paths if isinstance(paths, dict) else {}


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    """Load path settings from TOML, resolving relative paths from that file."""
    selected = config_path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH
    path = Path(selected).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as f:
            raw = tomllib.load(f)

    base_dir = path.parent
    paths = _paths_table(raw)

    vault = paths.get("vault") or raw.get("vault_path") or Path.home() / "Obsidian" / "quant"
    state = paths.get("state") or raw.get("state_path") or "data/card_state.sqlite"
    books = paths.get("books") or raw.get("books_config") or path

    return AppConfig(
        vault_path=_resolve_path(vault, base_dir=base_dir),
        state_path=_resolve_path(state, base_dir=base_dir),
        books_config=_resolve_path(books, base_dir=base_dir),
        config_path=path,
    )
