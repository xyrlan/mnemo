"""Tests for v0.7 structured SessionStart injection payload."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.rule_activation import build_index, write_index
from mnemo.hooks.session_start import _build_injection_payload


def _write_feedback(vault: Path, stem: str, *, name: str, tags: list[str], sources: list[str]):
    fm_tags = "\n".join(f"  - {t}" for t in tags)
    fm_sources = "\n".join(f"  - {s}" for s in sources)
    content = (
        "---\n"
        f"name: {name}\n"
        "stability: stable\n"
        f"tags:\n{fm_tags}\n"
        f"sources:\n{fm_sources}\n"
        "---\n\n"
        f"Body of {name}.\n"
    )
    target = vault / "shared" / "feedback" / f"{stem}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def test_injection_envelope_has_project_token(tmp_vault):
    _write_feedback(tmp_vault, "local", name="local-rule",
                    tags=["code-style", "auto-promoted"],
                    sources=["bots/alpha/memory/l.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    payload = _build_injection_payload(tmp_vault, current_project="alpha")
    assert payload.startswith("mnemo://v1 project=alpha")
    assert "local:" in payload
    assert "code-style" in payload


def test_injection_envelope_includes_universal_line(tmp_vault):
    _write_feedback(tmp_vault, "uni", name="uni-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    payload = _build_injection_payload(tmp_vault, current_project="alpha")
    assert "universal:" in payload
    assert "git" in payload


def test_injection_envelope_without_project_omits_project_token(tmp_vault):
    _write_feedback(tmp_vault, "uni", name="uni-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    payload = _build_injection_payload(tmp_vault, current_project=None)
    assert "project=" not in payload
    assert "universal:" in payload


def test_injection_envelope_empty_vault_returns_empty_string(tmp_vault):
    write_index(tmp_vault, build_index(tmp_vault))
    assert _build_injection_payload(tmp_vault, current_project="alpha") == ""


def test_injection_envelope_respects_max_topics(tmp_vault, monkeypatch):
    # Write 30 rules with distinct local topics in alpha
    for i in range(30):
        _write_feedback(tmp_vault, f"r{i}",
                        name=f"r-{i}",
                        tags=[f"topic-{i}", "auto-promoted"],
                        sources=["bots/alpha/memory/x.md"])
    write_index(tmp_vault, build_index(tmp_vault))

    # Force max to 5 via config monkeypatch
    from mnemo.core import config as cfg_mod
    original_load = cfg_mod.load_config
    def patched_load(*a, **kw):
        c = original_load(*a, **kw)
        c["injection"]["maxTopicsPerScope"] = 5
        return c
    monkeypatch.setattr(cfg_mod, "load_config", patched_load)

    payload = _build_injection_payload(tmp_vault, current_project="alpha")
    # Count topics in the local: line — should be exactly 5
    local_line = next(l for l in payload.splitlines() if l.startswith("local:"))
    topics_in_local = [t.strip() for t in local_line.split("[", 1)[1].rstrip("]").split(",")]
    assert len(topics_in_local) == 5
