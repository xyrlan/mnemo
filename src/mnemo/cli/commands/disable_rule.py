"""`mnemo disable-rule <slug>` / `mnemo enable-rule <slug>` — veto a rule, or undo the veto.

The veto is ``disabled: true`` in the page's frontmatter, which
``filters.is_consumer_visible`` honours: the rule stops blocking through its
``enforce`` block, the reflex stops injecting it, and the topic menu and
``list_rules_by_topic`` stop offering it. Both indexes are rebuilt here, so
that holds from the next tool call rather than the next session start.

It used to write ``runtime: false``, which nothing reads — and which
``extract/promote.py`` stamps on every promoted project page anyway, so it
could not have been honoured without dropping every project rule (#629).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import config, paths
from mnemo.core.filters import (
    DISABLED,
    derive_rule_slug,
    is_disabled,
    iter_shared_pages,
    parse_frontmatter,
)


def _find_rule_file(vault_root: Path, slug: str) -> Path | None:
    """Resolve ``slug`` to a live rule page.

    Accepts, in order: the file stem, ``derive_rule_slug`` (the ``slug:``
    field once #114 has stamped it, else ``name:``/stem), or the exact
    display ``name:``. The name fallback keeps a migrated vault answering to
    muscle memory — after migration ``derive_rule_slug`` is the kebab slug,
    so ``mnemo disable-rule "Use Yarn"`` would otherwise match nothing. All
    pages are walked for stem/slug hits before any name hit is honoured, so
    an exact slug always beats a page whose display name collides with it.
    """
    shared = vault_root / "shared"
    if not shared.is_dir():
        return None
    by_name: Path | None = None
    for md in iter_shared_pages(vault_root):
        if md.stem == slug:
            return md
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text)
        if not fm:
            continue
        if derive_rule_slug(fm, md.stem) == slug:
            return md
        if by_name is None and fm.get("name") == slug:
            by_name = md
    return by_name


def _split_page(vault_root: Path, slug: str) -> tuple[Path, str, str] | None:
    """``(path, frontmatter_block, body)`` for ``slug``, or None after printing why."""
    md = _find_rule_file(vault_root, slug)
    if md is None:
        print(f"error: rule not found for slug {slug!r}", file=sys.stderr)
        return None
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        print(f"error: {md} has no frontmatter", file=sys.stderr)
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        print(f"error: {md} frontmatter not closed", file=sys.stderr)
        return None
    return md, text[4:end], text[end + 5:]


def _is_disabled_line(line: str) -> bool:
    key, sep, _ = line.partition(":")
    return bool(sep) and not line[:1].isspace() and key.strip() == DISABLED


def _rebuild_indexes(vault_root: Path) -> None:
    """Make the change hold now: PreToolUse and the reflex read the cached indexes.

    Best-effort, like ``inbox._rebuild_indexes``: the page is already written,
    and a failed rebuild only delays it to the next session start.
    """
    from mnemo.core import errors

    try:
        from mnemo.core import rule_activation
        rule_activation.write_index(vault_root, rule_activation.build_index(vault_root))
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault_root, "disable_rule.rule_activation_index", exc)
    try:
        from mnemo.core.reflex import index as reflex_index
        reflex_index.write_index(vault_root, reflex_index.build_index(vault_root))
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault_root, "disable_rule.reflex_index", exc)


def run_disable_rule(vault_root: Path, *, slug: str) -> int:
    page = _split_page(vault_root, slug)
    if page is None:
        return 2
    md, fm_block, body = page
    rel = md.relative_to(vault_root).as_posix()
    if is_disabled(parse_frontmatter("---\n" + fm_block + "\n---\n")):
        print(f"already disabled: {rel}")
        return 0
    fm_lines = [ln for ln in fm_block.splitlines() if not _is_disabled_line(ln)]
    fm_lines.append(f"{DISABLED}: true")
    md.write_text("---\n" + "\n".join(fm_lines) + "\n---\n" + body, encoding="utf-8")
    _rebuild_indexes(vault_root)
    print(f"disabled: {rel}")
    print(f"undo: mnemo enable-rule {slug}")
    return 0


def run_enable_rule(vault_root: Path, *, slug: str) -> int:
    page = _split_page(vault_root, slug)
    if page is None:
        return 2
    md, fm_block, body = page
    rel = md.relative_to(vault_root).as_posix()
    fm_lines = fm_block.splitlines()
    kept = [ln for ln in fm_lines if not _is_disabled_line(ln)]
    if len(kept) == len(fm_lines):
        print(f"not disabled: {rel}")
        return 0
    md.write_text("---\n" + "\n".join(kept) + "\n---\n" + body, encoding="utf-8")
    _rebuild_indexes(vault_root)
    print(f"enabled: {rel}")
    return 0


@command("disable-rule")
def _cmd(ns: argparse.Namespace) -> int:
    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    return run_disable_rule(vault, slug=ns.slug)


@command("enable-rule")
def _cmd_enable(ns: argparse.Namespace) -> int:
    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    return run_enable_rule(vault, slug=ns.slug)
