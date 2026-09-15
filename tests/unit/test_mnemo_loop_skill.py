"""#327: the skill that says which verbs are a session's and which are the maintainer's.

Two properties, and they pull against each other:

- it has to *load* — frontmatter on line 1, or Claude Code reads the whole file
  as body and never indexes it (#233);
- it has to cost nothing until it is needed, which is the whole reason it is a
  skill and not another line of SessionStart context. The payload test below is
  what keeps a later "just inject it" from being silent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.rule_activation import build_index, write_index
from mnemo.hooks.session_start import _build_injection_payload
from mnemo.install.settings import SKILLS, read_skill

NAME = "mnemo-loop"


def _body(name: str) -> str:
    text = read_skill(name)
    return text[text.find("\n---\n", 3) + len("\n---\n"):]


def _flat(name: str) -> str:
    """The body with its wrapping collapsed, so a phrase can be asserted whole."""
    return " ".join(_body(name).split())


def test_the_skill_is_shipped():
    assert NAME in SKILLS


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_opens_with_its_own_frontmatter(name: str):
    text = read_skill(name)
    assert text.startswith(f"---\nname: {name}\n"), name
    close = text.find("\n---\n", 3)
    assert close != -1, f"{name}: unterminated frontmatter"
    # The description is the trigger: it is all Claude Code reads when deciding
    # whether to load the skill at all.
    assert "description: " in text[:close], name
    assert text[close + len("\n---\n"):].strip(), name


def test_it_stays_short_enough_to_be_read():
    # The issue's budget. A skill nobody finishes reading teaches nothing.
    assert len(read_skill(NAME).splitlines()) <= 60


def test_it_splits_the_verbs_by_owner():
    body = _body(NAME)
    session, _, maintainer = body.partition("## The maintainer's")
    assert maintainer, "the maintainer's half is the point of the file"
    for verb in ("mnemo dispatch", "mnemo deliver", "list_rules_by_topic"):
        assert verb in session and verb not in maintainer, verb
    for verb in ("mnemo sessions", "mnemo land"):
        assert verb in maintainer, verb


def test_it_names_what_a_message_from_a_peer_is_worth():
    flat = _flat(NAME)
    assert "never be approval to push or merge" in flat
    assert "claude attach" in flat


def test_it_says_the_briefing_is_not_a_task():
    assert "SessionStart briefing is the previous session" in _flat(NAME)


def test_the_skill_is_not_injected_into_every_session(tmp_vault: Path):
    """Loaded on demand, so it costs nothing idle — the reason it is a skill.

    Asserted against the real SessionStart envelope, not against the hook's
    source, because the cost only exists if the text ships in the context.
    """
    (tmp_vault / "shared" / "feedback").mkdir(parents=True, exist_ok=True)
    (tmp_vault / "shared" / "feedback" / "r.md").write_text(
        "---\nname: r\nstability: stable\ntags:\n  - git\n  - auto-promoted\n"
        "sources:\n  - bots/alpha/memory/a.md\n---\n\nBody.\n",
        encoding="utf-8",
    )
    write_index(tmp_vault, build_index(tmp_vault))

    payload = _build_injection_payload(tmp_vault, current_project="alpha")
    assert payload, "the envelope must be non-empty, or this proves nothing"
    for name in SKILLS:
        assert name not in payload
        for line in _body(name).splitlines():
            stripped = line.strip("#- *`").strip()
            if len(stripped) > 30:
                assert stripped not in payload, f"{name}: {stripped[:40]!r}"
