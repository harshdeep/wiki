"""Vault loader and in-memory index.

The vault layout we expect (same as an Obsidian vault produced by nkr):

    <root>/
        Topics/*.md       — one per topic, body lists concepts & sources
        Concepts/*.md     — one per concept, frontmatter has `topic`, `sources`
        Sources/*.md      — raw source material, frontmatter has `url`, `topic`

Everything is loaded once at startup (or on every git HEAD change) and held
as an immutable `Index`. Rebuilds swap the live reference atomically.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import frontmatter

from .markdown_render import Resolved, make_renderer
from .slugs import normalize, slugify

log = logging.getLogger(__name__)

# Files inside Concepts/ that are indices/listings, not real concepts.
_SKIP_CONCEPT_STEMS = {"index", "concepts"}


@dataclass
class Citation:
    kind: str  # "concept" | "source" | "broken"
    slug: str  # empty if broken
    title: str  # display title


@dataclass
class Concept:
    slug: str
    title: str
    topic: str | None
    citations: list[Citation]  # resolved references from `sources:` frontmatter
    created: str | None
    body_html: str
    backlinks: list[tuple[str, str]] = field(default_factory=list)  # (slug, title)
    is_summary: bool = False

    @property
    def sources(self) -> list[str]:
        """Slugs of citations that resolved to Sources/. Kept for backward compat."""
        return [c.slug for c in self.citations if c.kind == "source"]


@dataclass
class Topic:
    slug: str
    title: str
    body_html: str
    concepts: list[tuple[str, str]] = field(default_factory=list)  # (slug, title)
    sources: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Source:
    slug: str
    title: str
    url: str | None
    topic: str | None
    added: str | None
    body_html: str


@dataclass
class Index:
    topics: dict[str, Topic]
    concepts: dict[str, Concept]
    sources: dict[str, Source]

    # Lookup helpers (normalized title → slug)
    _concept_by_norm: dict[str, str]
    _source_by_norm: dict[str, str]

    def topic_list(self) -> list[Topic]:
        return sorted(self.topics.values(), key=lambda t: t.title.lower())

    def all_concepts_sorted(self) -> list[Concept]:
        return sorted(self.concepts.values(), key=lambda c: c.title.lower())


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def _read(path: Path) -> tuple[dict, str]:
    try:
        post = frontmatter.load(path)
    except Exception:
        log.exception("failed to parse frontmatter for %s", path)
        return {}, path.read_text(errors="replace")
    return post.metadata or {}, post.content or ""


def _coerce_str(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _strip_wikilink(s: str) -> str:
    """Turn a possibly-wikilinked value like "[[Foo]]" into "Foo"."""
    s = s.strip()
    m = re.match(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", s)
    return m.group(1).strip() if m else s


def build_index(root: Path) -> Index:
    """Walk the vault and produce a fully-populated Index."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"content root does not exist: {root}")

    topics_dir = root / "Topics"
    concepts_dir = root / "Concepts"
    sources_dir = root / "Sources"

    raw_concepts: list[tuple[Path, dict, str]] = []
    raw_topics: list[tuple[Path, dict, str]] = []
    raw_sources: list[tuple[Path, dict, str]] = []

    if concepts_dir.is_dir():
        for p in concepts_dir.glob("*.md"):
            if p.stem.lower() in _SKIP_CONCEPT_STEMS:
                continue
            meta, body = _read(p)
            raw_concepts.append((p, meta, body))
    if topics_dir.is_dir():
        for p in topics_dir.glob("*.md"):
            meta, body = _read(p)
            raw_topics.append((p, meta, body))
    if sources_dir.is_dir():
        for p in sources_dir.glob("*.md"):
            meta, body = _read(p)
            raw_sources.append((p, meta, body))

    concept_by_norm: dict[str, str] = {}
    source_by_norm: dict[str, str] = {}

    for p, _, _ in raw_concepts:
        concept_by_norm[normalize(p.stem)] = slugify(p.stem)
    for p, _, _ in raw_sources:
        source_by_norm[normalize(p.stem)] = slugify(p.stem)

    def resolver(target: str) -> Resolved:
        key = normalize(target)
        if key in concept_by_norm:
            return Resolved(kind="concept", slug=concept_by_norm[key])
        if key in source_by_norm:
            return Resolved(kind="source", slug=source_by_norm[key])
        return Resolved(kind="broken", slug="")

    render = make_renderer(resolver)

    # --- Concepts ---------------------------------------------------------
    concepts: dict[str, Concept] = {}
    for path, meta, body in raw_concepts:
        title = path.stem
        slug = slugify(title)
        topic = _coerce_str(meta.get("topic"))
        # Both `sources:` (list) and `source:` (single, on Summary notes).
        # A citation can resolve to a Source file OR to another Concept
        # (typically a "Summary - ..." concept that wraps the source).
        citation_targets_raw = _as_list(meta.get("sources")) + _as_list(meta.get("source"))
        citation_targets = [_strip_wikilink(s) for s in citation_targets_raw]
        citations: list[Citation] = []
        for tgt in citation_targets:
            key = normalize(tgt)
            if key in source_by_norm:
                citations.append(Citation(kind="source", slug=source_by_norm[key], title=tgt))
            elif key in concept_by_norm:
                citations.append(Citation(kind="concept", slug=concept_by_norm[key], title=tgt))
            else:
                citations.append(Citation(kind="broken", slug="", title=tgt))
        created = _coerce_str(meta.get("created"))
        body_html = render(body)
        concepts[slug] = Concept(
            slug=slug,
            title=title,
            topic=topic,
            citations=citations,
            created=created,
            body_html=body_html,
            is_summary=title.lower().startswith("summary - "),
        )

    # --- Sources ----------------------------------------------------------
    sources: dict[str, Source] = {}
    for path, meta, body in raw_sources:
        title = path.stem
        slug = slugify(title)
        sources[slug] = Source(
            slug=slug,
            title=title,
            url=_coerce_str(meta.get("url")),
            topic=_coerce_str(meta.get("topic")),
            added=_coerce_str(meta.get("added")),
            body_html=render(body),
        )

    # --- Topics -----------------------------------------------------------
    topics: dict[str, Topic] = {}
    for path, _meta, body in raw_topics:
        title = path.stem
        slug = slugify(title)
        topic_concepts: list[tuple[str, str]] = []
        topic_sources: list[tuple[str, str]] = []
        current_section: str | None = None
        for line in body.splitlines():
            h = re.match(r"^#{1,6}\s+(.+)$", line)
            if h:
                current_section = h.group(1).strip().lower()
                continue
            m = re.match(r"^\s*-\s*\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", line)
            if not m:
                continue
            target = m.group(1).strip()
            key = normalize(target)
            if current_section and "source" in current_section:
                if key in source_by_norm:
                    topic_sources.append((source_by_norm[key], target))
            else:
                if key in concept_by_norm:
                    topic_concepts.append((concept_by_norm[key], target))
                elif key in source_by_norm:
                    # Some topic files list summaries under ## Summaries etc.
                    topic_sources.append((source_by_norm[key], target))

        topics[slug] = Topic(
            slug=slug,
            title=title,
            body_html=render(body),
            concepts=topic_concepts,
            sources=topic_sources,
        )

    # Fallback: if a topic file didn't enumerate concepts (or doesn't exist),
    # derive membership from concept frontmatter `topic:` values.
    topic_titles_by_norm = {normalize(t.title): t.slug for t in topics.values()}
    for c in concepts.values():
        if not c.topic:
            continue
        t_key = normalize(c.topic)
        t_slug = topic_titles_by_norm.get(t_key)
        if t_slug is None:
            # Synthesize a topic stub so /topics/<slug> still works.
            t_slug = slugify(c.topic)
            topics[t_slug] = Topic(slug=t_slug, title=c.topic, body_html="")
            topic_titles_by_norm[t_key] = t_slug
        tp = topics[t_slug]
        if not any(s == c.slug for s, _ in tp.concepts):
            tp.concepts.append((c.slug, c.title))

    # --- Backlinks --------------------------------------------------------
    # A concept X links to Y if [[Y]] appears in X's source body. We computed
    # body_html already, but it's easier to scan raw bodies for [[...]].
    raw_body_by_slug: dict[str, str] = {
        slugify(p.stem): body for (p, _m, body) in raw_concepts
    }
    for src_slug, body in raw_body_by_slug.items():
        seen: set[str] = set()
        for m in re.finditer(r"\[\[([^\]\n|]+)(?:\|[^\]\n]+)?\]\]", body):
            target = re.split(r"[#^]", m.group(1), maxsplit=1)[0].strip()
            key = normalize(target)
            dest_slug = concept_by_norm.get(key)
            if not dest_slug or dest_slug == src_slug or dest_slug in seen:
                continue
            seen.add(dest_slug)
            src = concepts.get(src_slug)
            dst = concepts.get(dest_slug)
            if src and dst:
                dst.backlinks.append((src.slug, src.title))

    for c in concepts.values():
        c.backlinks.sort(key=lambda pair: pair[1].lower())

    return Index(
        topics=topics,
        concepts=concepts,
        sources=sources,
        _concept_by_norm=concept_by_norm,
        _source_by_norm=source_by_norm,
    )


# ---------------------------------------------------------------------------
# Live reference
# ---------------------------------------------------------------------------


class IndexHolder:
    """Holds the current `Index` and lets callers swap it atomically."""

    def __init__(self) -> None:
        self._index: Index | None = None

    def set(self, idx: Index) -> None:
        self._index = idx
        log.info(
            "index rebuilt: %d topics, %d concepts, %d sources",
            len(idx.topics),
            len(idx.concepts),
            len(idx.sources),
        )

    def get(self) -> Index:
        if self._index is None:
            raise RuntimeError("index not yet loaded")
        return self._index

    @property
    def loaded(self) -> bool:
        return self._index is not None


holder = IndexHolder()
