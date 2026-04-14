"""Vault reload.

The app runs on the same machine that owns the vault, so there is no network
or git involved. On startup we build the index once. On every protected
request we cheap-check the max modification time across `Topics/`,
`Concepts/`, and `Sources/`; if something changed since our last build, we
rebuild the in-memory index in place. For ~88 small markdown files the scan
is a few milliseconds and only notices edits when they happen.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .content import build_index, holder

log = logging.getLogger(__name__)

_SUBDIRS = ("Topics", "Concepts", "Sources")

_lock = asyncio.Lock()
_last_mtime: float = 0.0


def _vault() -> Path:
    raw = os.environ.get("CONTENT_DIR")
    if not raw:
        raise RuntimeError("CONTENT_DIR env var is required")
    return Path(raw).expanduser().resolve()


def _scan_mtime(root: Path) -> float:
    """Max mtime across subdir entries. Includes the dirs themselves so
    creates/deletes register even before any file's mtime changes."""
    m = 0.0
    for sub in _SUBDIRS:
        d = root / sub
        if not d.is_dir():
            continue
        try:
            m = max(m, d.stat().st_mtime)
        except OSError:
            pass
        for p in d.glob("*.md"):
            try:
                m = max(m, p.stat().st_mtime)
            except OSError:
                pass
    return m


def _rebuild() -> None:
    global _last_mtime
    root = _vault()
    holder.set(build_index(root))
    _last_mtime = _scan_mtime(root)


def startup_load() -> None:
    root = _vault()
    if not root.is_dir():
        raise FileNotFoundError(f"CONTENT_DIR does not exist: {root}")
    log.info("vault=%s", root)
    _rebuild()


async def ensure_fresh() -> None:
    """FastAPI dependency: rebuild if anything in the vault has changed."""
    root = _vault()
    current = await asyncio.to_thread(_scan_mtime, root)
    if current <= _last_mtime:
        return
    async with _lock:
        # Double-check after acquiring the lock so we only rebuild once if
        # multiple concurrent requests raced.
        if await asyncio.to_thread(_scan_mtime, root) <= _last_mtime:
            return
        try:
            await asyncio.to_thread(_rebuild)
        except Exception:
            log.exception("vault rebuild failed; serving stale index")
