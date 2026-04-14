"""Write a new Source markdown file into the vault.

The file follows nkr's conventions so its next scheduled `learn process` run
picks it up and turns it into Concepts. We always set `processed: false` and
either:

* a specific topic, when the caller knows it (the user clicked "+" on a
  topic page), or
* the literal string `auto-detect`, which nkr's processing prompt recognizes
  and resolves at processing time against the live Topics/ index.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

AUTO_DETECT_TOPIC = "auto-detect"

_UNSAFE = re.compile(r"[^a-zA-Z0-9_\- ]+")
_COLLAPSE_SPACES = re.compile(r"\s+")
# Only strip extensions that look like real file extensions, not ids like "1706.03762"
_FILE_EXTS = {"html", "htm", "pdf", "md", "txt", "asp", "aspx", "php"}


def _sources_dir() -> Path:
    root = os.environ.get("CONTENT_DIR")
    if not root:
        raise RuntimeError("CONTENT_DIR env var is required")
    return Path(root).expanduser().resolve() / "Sources"


def normalize_url(url: str) -> str:
    """Normalize URLs for duplicate detection.

    Lower-cases scheme + host, strips trailing slash on path, drops fragment.
    Keeps query strings — they can genuinely change what a URL points at.
    Protocol (http vs https) is preserved; we treat them as distinct.
    """
    p = urlparse(url.strip())
    scheme = p.scheme.lower()
    netloc = p.netloc.lower()
    path = p.path.rstrip("/") if p.path != "/" else ""
    return urlunparse((scheme, netloc, path, p.params, p.query, ""))


def slug_from_url(url: str) -> str:
    """Build a short, human-readable stem for a URL.

    Prefers the last path segment with any file-extension stripped; falls
    back to the hostname. Returns `"source"` for completely unusable input
    so the caller always gets something writable.
    """
    p = urlparse(url)
    parts = [seg for seg in p.path.strip("/").split("/") if seg]
    if parts:
        raw = parts[-1]
        head, dot, tail = raw.rpartition(".")
        if dot and tail.lower() in _FILE_EXTS:
            raw = head
    else:
        raw = p.hostname or "source"
    raw = raw.replace("_", "-")
    raw = _UNSAFE.sub("-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    raw = _COLLAPSE_SPACES.sub(" ", raw).strip()
    return raw or "source"


def _unique_path(directory: Path, base_stem: str) -> Path:
    path = directory / f"{base_stem}.md"
    if not path.exists():
        return path
    n = 2
    while True:
        path = directory / f"{base_stem} ({n}).md"
        if not path.exists():
            return path
        n += 1


def write_source(url: str, topic: str | None) -> Path:
    """Create a new Sources/*.md. Returns the written path."""
    directory = _sources_dir()
    directory.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    stem = f"{today} {slug_from_url(url)}"
    path = _unique_path(directory, stem)
    effective_topic = topic.strip() if topic and topic.strip() else AUTO_DETECT_TOPIC
    body = (
        "---\n"
        f'topic: "{effective_topic}"\n'
        f'url: "{url}"\n'
        f"added: {today}\n"
        "processed: false\n"
        "---\n"
    )
    path.write_text(body)
    return path
