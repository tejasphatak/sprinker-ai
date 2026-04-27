from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import REPO_ROOT


HISTORY_PATH = REPO_ROOT / "data" / "history.jsonl"


def append_entry(entry: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def recent_entries(days: int = 14) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    entries: list[dict[str, Any]] = []
    for line in HISTORY_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            ts = datetime.fromisoformat(obj["timestamp"]).timestamp()
            if ts >= cutoff:
                entries.append(obj)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return entries
