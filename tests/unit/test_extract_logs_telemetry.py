"""extract.run_once writes one llm.call entry per consolidation chunk."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.core import extract as extract_mod
from mnemo.core.llm import LLMResponse


def _seed_memory_file(vault: Path, agent: str, slug: str, body: str = "x") -> Path:
    d = vault / "bots" / agent / "memory"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"feedback_{slug}.md"
    p.write_text(
        f"---\ntype: feedback\nname: {slug}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def test_extract_logs_llm_call_per_chunk(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "shared" / "_inbox" / "feedback").mkdir(parents=True)
    (vault / "shared" / "feedback").mkdir(parents=True)
    _seed_memory_file(vault, "myagent", "rule-one", body="some feedback content")

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "subprocessTimeout": 60,
            "chunkSize": 5,
        },
    }

    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)

    fake_response = LLMResponse(
        text='[{"slug": "rule-one", "name": "Rule One", "body": "x", "tags": ["a"]}]',
        total_cost_usd=0.001,
        input_tokens=2000,
        output_tokens=200,
        api_key_source="none",
        raw={},
    )
    # The actual public entry point in extract/__init__.py is `run_extraction`
    # (around line 439). It takes cfg first; vault is derived internally via
    # paths.vault_root(cfg). Mock llm.call via monkeypatch on the module
    # attribute (not patch()).
    monkeypatch.setattr("mnemo.core.extract.llm.call", lambda *a, **kw: fake_response)
    extract_mod.run_extraction(cfg)

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    consolidation = [e for e in entries if e.get("tool") == "llm.call"
                     and e.get("purpose", "").startswith("consolidation:")]
    assert len(consolidation) >= 1
    e = consolidation[0]
    assert e["model"] == "claude-haiku-4-5"
    assert e["usage"]["input_tokens"] == 2000
    assert e["usage"]["output_tokens"] == 200
