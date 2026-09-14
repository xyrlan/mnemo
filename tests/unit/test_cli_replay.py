"""``mnemo replay`` end to end through the CLI, on a throwaway HOME."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.cli.runtime import main

SID_A = "aaaaaaaa-0000-0000-0000-000000000001"
SID_B = "bbbbbbbb-0000-0000-0000-000000000002"
PROMPT = "How do I mock prisma in a jest test with typescript"
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _seed_vault(vault: Path) -> None:
    feedback = vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    for sid in (SID_A, SID_B):
        b = vault / "bots" / "alpha" / "briefings" / "sessions" / f"{sid}.md"
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text("# briefing\n", encoding="utf-8")
    # A's briefing carries the quote in ``## Corrections`` — what today's gate reads.
    (vault / "bots" / "alpha" / "briefings" / "sessions" / f"{SID_A}.md").write_text(
        "# briefing\n\n## Corrections\n"
        '- "always mock prisma with jest-mock-extended in tests" → mock prisma that way\n',
        encoding="utf-8",
    )
    learned = (T0 + timedelta(hours=1)).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    (feedback / "use-prisma-mock.md").write_text(
        "---\nname: use-prisma-mock\ntype: feedback\n"
        "description: Always use jest-mock-extended to mock Prisma in tests\n"
        f"extracted_at: {learned}\nconfidence: verified\n"
        "tags:\n  - prisma\n  - testing\n"
        f"sources:\n  - bots/alpha/briefings/sessions/{SID_A}.md\n"
        "evidence:\n  quote: 'always mock prisma with jest-mock-extended in tests'\n"
        f"  source: bots/alpha/briefings/sessions/{SID_A}.md\n"
        "stability: stable\n---\n"
        "Mock the Prisma client in tests using jest-mock-extended.\n",
        encoding="utf-8",
    )
    # A reclassify-era label: verified, quote present, but the cited briefing has
    # no ``## Corrections`` and the source is prose — today's gate cannot re-check it.
    (feedback / "label-only-rule.md").write_text(
        "---\nname: label-only-rule\ntype: feedback\n"
        "description: Keep prisma queries out of react components\n"
        f"extracted_at: {learned}\nconfidence: verified\n"
        "tags:\n  - prisma\n  - react\n"
        f"sources:\n  - bots/alpha/briefings/sessions/{SID_B}.md\n"
        "evidence:\n  quote: 'keep the prisma queries out of the react components please'\n"
        f"  source: 'briefing: bots/alpha/briefings/sessions/{SID_B}.md — user turns, turn 4'\n"
        "stability: stable\n---\n"
        "Prisma queries live in the data layer, never in components.\n",
        encoding="utf-8",
    )
    for i, (name, desc, tag) in enumerate([
        ("use-yarn", "Prefer yarn over npm for installs", "yarn"),
        ("commit-strategy", "Small atomic commits with clear messages", "git"),
        ("review-etiquette", "Be kind and specific in code reviews", "review"),
        ("python-style", "Follow PEP8 and black formatting", "python"),
        ("docs-style", "Write clear, concise documentation", "docs"),
    ]):
        (feedback / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\ntags:\n  - {tag}\n"
            f"extracted_at: 2026-01-01T00:00:00\n"
            f"sources:\n  - bots/noise{i}/memory/x.md\nstability: stable\n---\n"
            f"Body for {name}.\n",
            encoding="utf-8",
        )


def _seed_transcripts(home: Path) -> None:
    d = home / ".claude" / "projects" / "-nowhere-alpha"
    d.mkdir(parents=True)
    for sid, at in ((SID_A, T0 + timedelta(days=1)), (SID_B, T0 + timedelta(days=2))):
        (d / f"{sid}.jsonl").write_text(json.dumps({
            "type": "user", "cwd": "/nowhere/alpha",
            "timestamp": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "message": {"role": "user", "content": PROMPT},
        }) + "\n", encoding="utf-8")


@pytest.fixture
def env(tmp_vault: Path, tmp_home: Path, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault), "reflex": {"enabled": True},
    }))
    return tmp_vault, tmp_home


def test_no_transcripts_is_a_clean_exit(env, capsys):
    vault, _home = env
    _seed_vault(vault)
    assert main(["replay"]) == 2
    assert "nothing to replay" in capsys.readouterr().out


def test_empty_vault_is_a_clean_exit(env, capsys):
    _vault, home = env
    _seed_transcripts(home)
    assert main(["replay"]) == 2
    assert "no rules yet" in capsys.readouterr().out


def test_replay_reports_and_writes(env, capsys):
    vault, home = env
    _seed_vault(vault)
    _seed_transcripts(home)

    assert main(["replay"]) == 0
    out = capsys.readouterr().out

    assert re.search(r"prompts replayed\s+2\s+\(2 sessions", out)
    assert re.search(r"EARLIER session\s+1\s+prompts", out)
    assert re.search(r"citing your own words\s+1\s+prompts", out)
    assert re.search(r"SAME session\s+1\s+prompts", out)
    assert "no rate is printed" in out
    report = json.loads((vault / ".mnemo" / "replay-report.json").read_text())
    assert report["prompts"]["carried"] == 1
    assert report["prompts"]["carried_correction_backed"] == 1
    assert report["prompts"]["carried_gate_verified"] == 1
    assert report["vault"] == {"rules": 7, "correction_backed": 2, "gate_verified": 1, "label_only": 1}
    assert re.search(r"citing your own words\s+1\s+prompts", out)
    assert re.search(r"label only, gate can't check\s+0\s+prompts", out)
    assert "project" not in report


def test_replay_json_and_project_filter(env, capsys):
    vault, home = env
    _seed_vault(vault)
    _seed_transcripts(home)

    assert main(["replay", "--json", "--project", "alpha"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["project"] == "alpha"
    assert report["prompts"]["total"] == 2

    assert main(["replay", "--project", "beta"]) == 2
    assert "for project 'beta'" in capsys.readouterr().out
