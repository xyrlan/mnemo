from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

# The fixture's target rule is about mocking Prisma with jest-mock-extended;
# this prompt clears the default gates against it (see tests/conftest.py
# ``synthetic_index`` — it takes the vault only and returns nothing).
PROMPT = "mock the prisma client in tests with jest-mock-extended"


def _run_hook(monkeypatch, vault: Path, repo: Path, prompt: str) -> tuple:
    from mnemo.hooks import user_prompt_submit as hook

    cfg = {"vaultRoot": str(vault), "reflex": {"enabled": True,
           "thresholds": {"minQueryTokens": 1, "termOverlapMin": 1, "relativeGap": 1.0, "absoluteFloor": 0.0}}}
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s1", "cwd": str(repo), "prompt": prompt})))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert hook.main() == 0
    log = vault / ".mnemo" / "reflex-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return out.getvalue(), entries


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    return root


def _export_file(repo: Path) -> None:
    p = repo / ".claude" / "rules" / "mnemo.md"
    p.parent.mkdir(parents=True)
    p.write_text("<!-- mnemo:start — x -->\n## Rules\n\n### P  `use-prisma-mock`\nbody\n\n<!-- mnemo:end -->\n", encoding="utf-8")


def test_exported_slug_is_not_injected_and_is_recorded(tmp_vault: Path, repo: Path, synthetic_index, monkeypatch):
    from mnemo.core.export import manifest as M

    synthetic_index(tmp_vault)          # seeds `use-prisma-mock`, universal, + noise rules
    _export_file(repo)
    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd=str(repo.resolve()),
                     path=".claude/rules/mnemo.md", rules={"use-prisma-mock": "h"})

    out, entries = _run_hook(monkeypatch, tmp_vault, repo, PROMPT)
    e = entries[-1]
    assert "use-prisma-mock" not in out
    assert e["silence_reason"] == "all_exported"
    assert e["exported"] == ["use-prisma-mock"]
    assert any(c[0] == "use-prisma-mock" for c in e["candidates"])   # still visible in the receipt with its score


def test_no_exported_file_still_injects(tmp_vault: Path, repo: Path, synthetic_index, monkeypatch):
    from mnemo.core.export import manifest as M

    synthetic_index(tmp_vault)
    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/somewhere/else",
                     path=".claude/rules/mnemo.md", rules={"use-prisma-mock": "h"})

    out, entries = _run_hook(monkeypatch, tmp_vault, repo, PROMPT)
    assert "use-prisma-mock" in out
    assert "exported" not in entries[-1]


def test_exported_rule_not_among_accepted_slugs_is_not_recorded(tmp_vault: Path, repo: Path, synthetic_index, monkeypatch):
    """The manifest exports a rule that never won the gates (a noise rule);
    the accepted rule injects normally and nothing was suppressed."""
    from mnemo.core.export import manifest as M

    synthetic_index(tmp_vault)
    p = repo / ".claude" / "rules" / "mnemo.md"
    p.parent.mkdir(parents=True)
    p.write_text("<!-- mnemo:start — x -->\n## Rules\n\n### P  `use-yarn`\nbody\n\n<!-- mnemo:end -->\n", encoding="utf-8")
    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd=str(repo.resolve()),
                     path=".claude/rules/mnemo.md", rules={"use-yarn": "h"})

    out, entries = _run_hook(monkeypatch, tmp_vault, repo, PROMPT)
    assert "use-prisma-mock" in out
    assert "exported" not in entries[-1]


def test_why_prints_exported_lines():
    from mnemo.core.reflex import receipts

    emission = {"ts": "2026-09-02T09:41:24Z", "emitted": ["b"], "scores": [3.0],
                "silence_reason": None, "candidates": [["b", 3.0]], "exported": ["a"]}
    silence = {"ts": "2026-09-02T09:42:00Z", "emitted": [], "scores": [],
               "silence_reason": "all_exported", "exported": ["a", "c"]}
    text = receipts.format_human([emission, silence])
    assert "injected  b (3.00)" in text
    assert "exported  a (already in your rules file)" in text
    assert "silent    every matching rule is already in your rules file (a, c)" in text


def test_why_exported_line_comes_before_the_candidate_table():
    from mnemo.core.reflex import receipts

    silence = {"ts": "2026-09-02T09:43:00Z", "emitted": [], "scores": [],
               "silence_reason": "relative_gap_fail", "exported": ["z"],
               "candidates": [["strong", 6.0], ["weak", 5.0]],
               "thresholds": {"relative_gap": 1.5}}
    text = receipts.format_human([silence])
    lines = text.splitlines()
    exported_idx = next(i for i, l in enumerate(lines) if "exported  z" in l)
    table_idx = next(i for i, l in enumerate(lines) if l.strip().startswith("strong"))
    assert exported_idx < table_idx
