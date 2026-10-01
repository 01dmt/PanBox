"""Read and persist the local webhook ingestion credential."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .schema import ROOT_DIR


INGESTION_CONFIG_PATH = ROOT_DIR / "data" / "ingestion-config.json"
MIN_KEY_LENGTH = 16


def read_ingestion_key(path: Path = INGESTION_CONFIG_PATH) -> tuple[str, str]:
    """Return ``(key, source)`` while preferring the managed file over env."""
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            key = payload.get("api_key") if isinstance(payload, dict) else None
            if isinstance(key, str) and key.strip():
                return key.strip(), "file"
    except (OSError, ValueError, TypeError):
        # A broken or unreadable file should not prevent an env configured
        # deployment from starting.
        pass
    key = os.getenv("INGESTION_API_KEY", "").strip()
    return (key, "env") if key else ("", "none")


def save_ingestion_key(value: str, path: Path = INGESTION_CONFIG_PATH) -> str:
    """Atomically save a validated key and return the normalized value."""
    key = value.strip() if isinstance(value, str) else ""
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError(f"API Key 至少需要 {MIN_KEY_LENGTH} 个字符。")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"api_key": key}, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key
