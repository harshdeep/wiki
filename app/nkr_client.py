"""Trigger `nkr learn add` for a new source URL, track processing status.

`nkr learn add` scrapes the URL, writes the source file with full content,
then runs Claude to extract concepts — all in one blocking call. We run it
in a background thread so the HTTP request returns immediately.

One run at a time: if a run is already in progress when a second URL arrives,
it joins the queue and starts automatically when the first run finishes.

Status is tracked in module-level state and exposed via get_status() so the
/sources/status endpoint can poll it for the frontend.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NKR_CMD = os.environ.get(
    "NKR_CMD",
    str(Path.home() / "Build" / "nkr" / ".venv" / "bin" / "nkr"),
)
NKR_CWD = os.environ.get(
    "NKR_CWD",
    str(Path.home() / "Build" / "nkr"),
)
CONCEPTS_DIR = Path.home() / "Vault" / "Learn" / "Concepts"

_lock = threading.Lock()

_status: dict[str, Any] = {
    "status": "idle",   # idle | processing | done | error
    "url": None,
    "concepts": [],     # [{"title": str, "slug": str}] from the last run
    "error": None,
    "queue": [],        # [(url, topic)] waiting to run
}


def _scan_new_concepts(since: float) -> list[dict[str, str]]:
    """Return concepts whose file was created/modified after `since` (epoch)."""
    if not CONCEPTS_DIR.exists():
        return []
    results = []
    for path in sorted(CONCEPTS_DIR.glob("*.md")):
        if path.name in ("Index.md", "Concepts.md"):
            continue
        if path.stat().st_mtime > since:
            from .slugs import slugify
            results.append({"title": path.stem, "slug": slugify(path.stem)})
    return results


def _run_add(url: str, topic: str | None) -> None:
    """Blocking subprocess call. Runs in a background thread."""
    start_time = time.time()
    cmd = [NKR_CMD, "learn", "add", url]
    if topic:
        cmd += ["--topic", topic]

    try:
        result = subprocess.run(
            cmd,
            cwd=NKR_CWD,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            log.warning("nkr learn add failed (rc=%d): %s", result.returncode, result.stdout[-500:])
            with _lock:
                _status["status"] = "error"
                _status["error"] = "Processing failed. Check nkr logs."
        else:
            concepts = _scan_new_concepts(start_time)
            log.info("nkr learn add finished; new concepts: %d", len(concepts))
            with _lock:
                _status["status"] = "done"
                _status["concepts"] = concepts
    except subprocess.TimeoutExpired:
        log.error("nkr learn add timed out for %s", url)
        with _lock:
            _status["status"] = "error"
            _status["error"] = "Processing timed out after 5 minutes."
    except Exception:
        log.exception("nkr learn add raised an exception for %s", url)
        with _lock:
            _status["status"] = "error"
            _status["error"] = "Unexpected error — check nkr logs."

    # Start next queued URL if any
    with _lock:
        if _status["queue"]:
            next_url, next_topic = _status["queue"].pop(0)
            _status.update({
                "status": "processing",
                "url": next_url,
                "concepts": [],
                "error": None,
            })
            threading.Thread(
                target=_run_add, args=(next_url, next_topic), daemon=True
            ).start()
            log.info("started next queued url=%s", next_url)


async def trigger_add(url: str, topic: str | None = None) -> None:
    """Queue `nkr learn add <url>` and start it if nothing is running."""
    with _lock:
        if not Path(NKR_CMD).exists():
            log.warning(
                "NKR_CMD not found at %s — source will be processed on nkr's next scheduled run",
                NKR_CMD,
            )
            return

        if _status["status"] == "processing":
            _status["queue"].append((url, topic))
            log.info("queued url=%s (queue_length=%d)", url, len(_status["queue"]))
            return

        _status.update({
            "status": "processing",
            "url": url,
            "concepts": [],
            "error": None,
        })

    threading.Thread(target=_run_add, args=(url, topic), daemon=True).start()
    log.info("triggered nkr learn add url=%s topic=%s", url, topic)


def get_status() -> dict[str, Any]:
    """Return a snapshot of the current processing state."""
    with _lock:
        return {
            "status": _status["status"],
            "url": _status["url"],
            "concepts": list(_status["concepts"]),
            "error": _status["error"],
            "queue_length": len(_status["queue"]),
        }
