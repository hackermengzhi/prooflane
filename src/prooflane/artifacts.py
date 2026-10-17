"""Versioned evidence files shared by execution and reporting."""

import hashlib
import json
from pathlib import Path

SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "password", "signature", "token"}


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(directory: Path) -> list[dict]:
    return [
        {"path": str(path.relative_to(directory)), "sha256": digest(path)}
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
