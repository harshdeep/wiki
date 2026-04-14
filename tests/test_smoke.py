"""Smoke tests against a fixture vault. Run with:

    .venv/bin/python -m pytest tests/

or just execute this file directly — it runs without pytest too.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("WIKI_PASSWORD", "test")
os.environ.setdefault("SECRET_KEY", "testkey")

from app.content import build_index  # noqa: E402
from app.markdown_render import make_renderer, Resolved  # noqa: E402
from app.slugs import normalize, slugify  # noqa: E402


def _fixture(root: Path) -> None:
    (root / "Topics").mkdir()
    (root / "Concepts").mkdir()
    (root / "Sources").mkdir()

    (root / "Topics" / "AI.md").write_text(
        "# AI\n\n## Sources\n\n- [[paper-1]]\n\n## Concepts\n\n- [[Alpha]]\n- [[Beta]]\n"
    )
    (root / "Concepts" / "Alpha.md").write_text(
        "---\ntopic: \"AI\"\nsources:\n  - \"[[paper-1]]\"\ncreated: 2026-01-01\n---\n\n"
        "Alpha references [[Beta]] and also [[nowhere]].\n"
    )
    (root / "Concepts" / "Beta.md").write_text(
        "---\ntopic: \"AI\"\nsources:\n  - \"[[Summary - Paper]]\"\ncreated: 2026-01-01\n---\n\n"
        "Beta pairs with [[Alpha]].\n"
    )
    (root / "Concepts" / "Summary - Paper.md").write_text(
        "---\ntopic: \"AI\"\nsource: \"[[paper-1]]\"\ncreated: 2026-01-01\n---\n\n"
        "Summary of [[paper-1]].\n"
    )
    (root / "Sources" / "paper-1.md").write_text(
        "---\ntopic: \"AI\"\nurl: \"https://example.com/paper\"\nadded: 2026-01-01\n---\n\n"
        "Abstract.\n"
    )


def test_slugify() -> None:
    assert slugify("Transformer Architecture") == "transformer-architecture"
    assert slugify("2026-04-12 1706.03762") == "2026-04-12-1706-03762"


def test_normalize_roundtrip() -> None:
    assert normalize("Self-Attention") == normalize("self-attention")
    assert normalize("  Foo  Bar  ") == "foo bar"


def test_wikilink_resolver() -> None:
    def resolver(target: str) -> Resolved:
        if target == "Foo":
            return Resolved("concept", "foo")
        return Resolved("broken", "")

    render = make_renderer(resolver)
    out = render("See [[Foo]] and [[Missing]].")
    assert 'href="/concepts/foo"' in out
    assert "broken-link" in out


def test_index_build() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _fixture(root)
        idx = build_index(root)

        assert set(idx.topics) == {"ai"}
        assert set(idx.concepts) == {"alpha", "beta", "summary-paper"}
        assert set(idx.sources) == {"paper-1"}

        alpha = idx.concepts["alpha"]
        # citations: the frontmatter references paper-1 directly
        assert any(c.kind == "source" and c.slug == "paper-1" for c in alpha.citations)
        # body links: Alpha → Beta (concept), Alpha → nowhere (broken)
        assert 'href="/concepts/beta"' in alpha.body_html
        assert "broken-link" in alpha.body_html

        beta = idx.concepts["beta"]
        # Beta cites a Summary concept, not a Source
        assert any(c.kind == "concept" and c.slug == "summary-paper" for c in beta.citations)

        summary = idx.concepts["summary-paper"]
        assert summary.is_summary is True
        assert any(c.kind == "source" and c.slug == "paper-1" for c in summary.citations)

        # Backlinks: Beta links to Alpha → Alpha has Beta in backlinks
        alpha_backlinks = {slug for slug, _title in alpha.backlinks}
        assert "beta" in alpha_backlinks
        # Alpha links to Beta → Beta has Alpha in backlinks
        beta_backlinks = {slug for slug, _title in beta.backlinks}
        assert "alpha" in beta_backlinks

        # Topic membership from the topic file
        topic = idx.topics["ai"]
        topic_concept_slugs = {s for s, _t in topic.concepts}
        assert "alpha" in topic_concept_slugs
        assert "beta" in topic_concept_slugs


def _run_all() -> None:
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")


if __name__ == "__main__":
    _run_all()
