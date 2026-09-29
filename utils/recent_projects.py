"""最近打开的工程文件列表。"""
import json
import os
from pathlib import Path
from typing import List

from utils.fileio import atomic_write_text

MAX_RECENT = 10


def recent_file() -> Path:
    override = os.environ.get("TIN_EARTHWORK_RECENT")
    if override:
        return Path(override)
    return Path.home() / ".tin-earthwork" / "recent_projects.json"


def load_recent() -> List[str]:
    path = recent_file()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if item]


def remember_recent(filepath: str, limit: int = MAX_RECENT) -> List[str]:
    resolved = str(Path(filepath).expanduser().resolve())
    items = [path for path in load_recent() if path != resolved]
    items.insert(0, resolved)
    items = items[:limit]
    atomic_write_text(recent_file(), json.dumps(items, ensure_ascii=False, indent=2))
    return items


def forget_recent(filepath: str) -> List[str]:
    candidates = {str(Path(filepath)), str(Path(filepath).expanduser())}
    try:
        candidates.add(str(Path(filepath).expanduser().resolve()))
    except OSError:
        pass
    items = [path for path in load_recent() if path not in candidates]
    atomic_write_text(recent_file(), json.dumps(items, ensure_ascii=False, indent=2))
    return items
