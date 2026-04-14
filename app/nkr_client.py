"""Trigger nkr's `learn process` step after we drop a new Source file.

`nkr learn process` scans `~/Vault/Learn/Sources/*.md` for files with
`processed: false`, hands each one to Claude (via Claude Code CLI) to
extract concepts, and then marks the file as processed. Running it right
after we write a source means the user sees the concepts materialize within
a minute or two instead of waiting for nkr's 6 AM scheduled run.

We spawn it detached (new session, DEVNULL for stdio) so the wiki request
returns immediately — processing routinely takes 30–90 seconds and we never
want to block the HTTP response on it.

Dedup: a single asyncio lock plus tracking the current Popen handle means
repeated "+ add source" clicks don't spawn multiple parallel processing
runs that would race each other writing to `Concepts/`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

NKR_CMD = os.environ.get(
    "NKR_CMD",
    str(Path.home() / "Build" / "nkr" / ".venv" / "bin" / "nkr"),
)
NKR_CWD = os.environ.get(
    "NKR_CWD",
    str(Path.home() / "Build" / "nkr"),
)

_lock = asyncio.Lock()
_current: subprocess.Popen | None = None


def _still_running() -> bool:
    return _current is not None and _current.poll() is None


async def trigger_process() -> None:
    """Kick off `nkr learn process` in the background. Never blocks."""
    global _current
    async with _lock:
        if _still_running():
            assert _current is not None
            log.info("nkr learn process already running (pid=%d); skipping", _current.pid)
            return
        if not Path(NKR_CMD).exists():
            log.warning(
                "NKR_CMD not found at %s — source will be processed on nkr's next scheduled run",
                NKR_CMD,
            )
            return
        try:
            _current = subprocess.Popen(
                [NKR_CMD, "learn", "process"],
                cwd=NKR_CWD,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("triggered nkr learn process (pid=%d)", _current.pid)
        except Exception:
            log.exception("failed to spawn nkr learn process")
