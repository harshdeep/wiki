"""Render Obsidian-flavored markdown to HTML.

The only non-standard feature we care about is `[[wikilinks]]`. We hand
`python-markdown` a small preprocessor that rewrites them into HTML anchors
*before* the standard markdown parser runs, so they get left alone by inline
parsing and emerge untouched in the output.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable

import markdown
from markdown.preprocessors import Preprocessor
from markdown.extensions import Extension

from .slugs import normalize

# Matches [[Target]] or [[Target|Display]]
_WIKILINK_RE = re.compile(r"\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]")


@dataclass
class Resolved:
    kind: str  # "concept" | "source" | "broken"
    slug: str  # empty if broken


Resolver = Callable[[str], Resolved]


class _WikilinkPreprocessor(Preprocessor):
    """Rewrite [[...]] to raw HTML anchors before markdown parses inlines."""

    def __init__(self, md: markdown.Markdown, resolver: Resolver) -> None:
        super().__init__(md)
        self._resolver = resolver

    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        in_code_fence = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_code_fence = not in_code_fence
                out.append(line)
                continue
            if in_code_fence:
                out.append(line)
                continue
            out.append(_WIKILINK_RE.sub(self._sub, line))
        return out

    def _sub(self, m: re.Match[str]) -> str:
        target = m.group(1).strip()
        display = (m.group(2) or target).strip()
        # Obsidian permits "Target#Heading" and "Target^block"; strip the tail.
        bare = re.split(r"[#^]", target, maxsplit=1)[0].strip()
        resolved = self._resolver(bare)
        safe_display = html.escape(display)
        if resolved.kind == "broken":
            return f'<span class="broken-link" title="missing: {html.escape(bare)}">{safe_display}</span>'
        prefix = "concepts" if resolved.kind == "concept" else "sources"
        return f'<a class="wikilink" href="/{prefix}/{resolved.slug}">{safe_display}</a>'


class WikilinkExtension(Extension):
    def __init__(self, resolver: Resolver) -> None:
        super().__init__()
        self._resolver = resolver

    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802
        md.preprocessors.register(
            _WikilinkPreprocessor(md, self._resolver),
            "wikilink",
            priority=30,  # before fenced_code (25) would be bad; after is fine
        )


def make_renderer(resolver: Resolver) -> Callable[[str], str]:
    """Return a function body_md -> body_html.

    The returned renderer is cheap to call repeatedly and reuses a single
    `Markdown` instance; we call `reset()` between docs.
    """
    md = markdown.Markdown(
        extensions=[
            "extra",          # tables, fenced code, def lists, etc.
            "sane_lists",
            "smarty",
            WikilinkExtension(resolver),
        ],
        output_format="html",
    )

    def render(body: str) -> str:
        md.reset()
        return md.convert(body)

    return render
