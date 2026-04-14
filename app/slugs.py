"""Filename → slug conversion, and a normalized form for wikilink lookup."""

from __future__ import annotations

import re

_slug_invalid = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    s = title.strip().lower()
    s = _slug_invalid.sub("-", s)
    return s.strip("-")


def normalize(title: str) -> str:
    """Case/space-insensitive key for resolving wikilinks against filenames."""
    return re.sub(r"\s+", " ", title.strip().casefold())
