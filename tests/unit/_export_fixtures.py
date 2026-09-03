"""Write rule pages in the exact shape ``_render_page`` produces, for export tests."""
from __future__ import annotations

from pathlib import Path


def write_rule(
    vault: Path,
    *,
    page_type: str = "feedback",
    slug: str,
    name: str | None = None,
    projects: tuple[str, ...] = ("app",),
    body: str = "Do the thing.\n",
    quote: str | None = None,
    stability: str = "stable",
    inbox: bool = False,
    graph_section: bool = True,
) -> Path:
    """One page under ``shared/<type>/`` (or ``shared/_inbox/<type>/``).

    ``projects`` becomes one ``sources:`` briefing per project, which is how
    ``projects_for_rule`` attributes a page; two projects make it universal at
    the default threshold of 2.
    """
    where = vault / "shared" / ("_inbox" if inbox else "") / page_type
    where.mkdir(parents=True, exist_ok=True)
    sources = "\n".join(
        f"  - bots/{p}/briefings/sessions/{slug}-{i}.md" for i, p in enumerate(projects)
    )
    lines = [
        "---",
        f"name: '{name or slug.replace('-', ' ').capitalize()}'",
        f"slug: {slug}",
        f"description: 'about {slug}'",
        f"type: {page_type}",
        f"stability: {stability}",
        "sources:",
        sources,
        "tags:",
        "  - auto-promoted",
    ]
    if quote is not None:
        lines += ["evidence:", f"  quote: '{quote}'", "  source: 'briefing: x — user turns, turn 1'"]
    lines.append("---")
    text = "\n".join(lines) + "\n\n" + body
    if graph_section:
        text += "\n<!-- mnemo:graph-section -->\n## Sources\n- [[bots/app/briefings/sessions/x]]\n"
    path = where / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path
