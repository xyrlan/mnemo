"""The docs must describe the code that exists.

Every previous drift was individually small and collectively fatal: docs
claimed two hooks when four shipped, documented config keys that had been
removed, and told users to run `/mnemo status` when the command was `/status`.
Nobody notices until a new user follows the instructions and they don't work.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = [REPO / "README.md", *(REPO / "docs").glob("*.md")]


def _doc_text() -> dict[Path, str]:
    return {p: p.read_text() for p in DOCS}


def test_every_referenced_cli_command_exists():
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()
    known = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            known |= {c for c in action.choices if isinstance(c, str)}

    pattern = re.compile(r"`mnemo ([a-z][a-z-]*)")
    for path, text in _doc_text().items():
        for cmd in set(pattern.findall(text)):
            assert cmd in known, f"{path.name} references `mnemo {cmd}`, which does not exist"


def test_every_referenced_slash_command_exists():
    from mnemo.install.settings import PLUGIN_COMMANDS

    pattern = re.compile(r"/mnemo:([a-z][a-z-]*)")
    for path, text in _doc_text().items():
        for cmd in set(pattern.findall(text)):
            assert cmd in PLUGIN_COMMANDS, (
                f"{path.name} references /mnemo:{cmd}, which the plugin does not ship"
            )


def test_no_doc_uses_the_pre_plugin_slash_syntax():
    """`/mnemo status` was never a real command — the space is wrong.

    Anchored on a preceding space or line start so it does not fire on
    `npx @xyrlan/mnemo uninstall`, where the slash belongs to the package name.
    """
    pattern = re.compile(r"(?:^|(?<=\s))/mnemo (status|doctor|open|fix|init|uninstall|extract)\b",
                         re.MULTILINE)
    for path, text in _doc_text().items():
        assert not pattern.search(text), (
            f"{path.name} uses `/mnemo <cmd>`; the plugin form is `/mnemo:<cmd>`"
        )


def test_every_documented_config_key_exists():
    from mnemo.core.config import DEFAULTS

    def flatten(d: dict, prefix: str = "") -> set[str]:
        out = set()
        for k, v in d.items():
            key = f"{prefix}{k}"
            out.add(key)
            if isinstance(v, dict):
                out |= flatten(v, f"{key}.")
        return out

    known = flatten(DEFAULTS)
    text = (REPO / "docs" / "configuration.md").read_text()
    # Backticked dotted keys, e.g. `extraction.auto.enabled`. Anchored on a
    # real top-level section so backticked filenames (`settings.json`) and
    # paths aren't mistaken for config keys.
    top = "|".join(sorted(DEFAULTS))
    pattern = re.compile(rf"`((?:{top})(?:\.[a-zA-Z*][a-zA-Z]*)+)`")
    for key in set(pattern.findall(text)):
        if key.endswith(".*"):
            continue
        assert key in known, f"configuration.md documents `{key}`, which is not in DEFAULTS"


def test_configuration_keys_are_fully_qualified():
    """Every table row must be copy-pasteable into mnemo.config.json.

    Section-relative keys (`auto.enabled` under a "### extraction" heading)
    read fine in place but are wrong the moment someone copies one out.
    """
    from mnemo.core.config import DEFAULTS

    text = (REPO / "docs" / "configuration.md").read_text()
    top = set(DEFAULTS)
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        key = line.split("`")[1]
        assert key.split(".")[0] in top, f"{key!r} is not rooted at a top-level config key"


def test_internal_links_resolve():
    pattern = re.compile(r"\]\(([^)#][^)]*\.md)\)")
    for path, text in _doc_text().items():
        for link in pattern.findall(text):
            if link.startswith(("http://", "https://")):
                continue
            target = (path.parent / link).resolve()
            assert target.exists(), f"{path.name} links to missing {link}"


def test_docs_name_every_hook_event_that_ships():
    """getting-started.md listed two events for a long time; four ship."""
    from mnemo.install.settings import HOOK_DEFINITIONS

    text = (REPO / "docs" / "getting-started.md").read_text()
    for event in HOOK_DEFINITIONS:
        assert event in text, f"getting-started.md never mentions the {event} hook"


@pytest.mark.parametrize("claim", ["off by default"])
def test_vault_templates_do_not_repeat_the_off_by_default_claim(claim: str):
    """Background features have been on by default since 0.15.0.

    These templates ship into every new vault, so a wrong claim here is the
    first thing a user reads.
    """
    for name in ("HOME.md", "README.md"):
        text = (REPO / "src" / "mnemo" / "templates" / name).read_text()
        assert claim not in text.lower(), f"templates/{name} still says '{claim}'"
