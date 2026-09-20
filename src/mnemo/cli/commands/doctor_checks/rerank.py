"""Doctor check: the rerank stage is configured and has no key to run with (#406).

The stage falls back to the local BM25F order on every gap and tells the agent
nothing — a provider being down must not break a tool call. That silence is
right for the agent and wrong for the maintainer: someone who sets
``recall.rerank.provider`` and never gives the MCP server a key sees a list
that looks exactly as it did before, and has nothing to read that says why.

So this row answers one question, locally and with no network: would a call
made right now find a key? Off is ok. On with a key is ok, and says where the
key comes from, because ``TYPESAFE_API_KEY`` exported in one shell is
precisely the thing that will not be there next time. On with no key is the
failure, and it names the command that fixes it.
"""
from __future__ import annotations

from pathlib import Path


def check_rerank(cfg: dict | None = None) -> list[str] | None:
    """Return finding lines, or None when there is nothing to report.

    Both stages that can call the provider are covered: ``recall.rerank`` on
    the MCP path and ``reflex.judge`` on the prompt path (#412). They share a
    key but not a process, and either one can be set with nothing to run on.

    ``cfg`` is injectable so a test does not have to own the config file.
    """
    from mnemo.core import config as cfg_mod
    from mnemo.core import secrets
    from mnemo.core.mcp import rerank as mcp_rerank
    from mnemo.core.reflex import judge as reflex_judge

    loaded = cfg if cfg is not None else cfg_mod.load_config()
    findings: list[str] = []

    chosen = mcp_rerank.settings(loaded)
    if chosen["provider"] != "none" and mcp_rerank.key_source(chosen) == "none":
        findings += [
            "rerank: recall.rerank.provider is %r but no key resolves, so every "
            "list_rules_by_topic call is falling back to the local BM25F order "
            "(access log: rerank.status = no_key)." % chosen["provider"],
            "    → run `mnemo rerank --setup` — it asks for the key, tests it once, "
            "and stores it in %s, which the MCP server reads whatever started it "
            "(%s only reaches the server when `claude` was launched from the shell "
            "that exported it)" % (secrets.path(), chosen["keyEnv"]),
        ]

    judge = reflex_judge.settings(loaded)
    if judge["provider"] != "none" and reflex_judge.key_source(judge) == "none":
        findings += [
            "rerank: reflex.judge.provider is %r but no key resolves, so every "
            "prompt is falling back to the lexical gates "
            "(reflex log: judge.status = no_key)." % judge["provider"],
            "    → run `mnemo rerank --setup` — one key serves both stages, and "
            "it is stored in %s where the hook reads it" % secrets.path(),
        ]
    return findings or None


def _doctor_check_rerank(vault: Path) -> bool:
    """Doctor-registry adapter — True when silent, False on a finding.

    ``vault`` is unused: the key is a property of this machine and this
    config, not of a vault.
    """
    try:
        findings = check_rerank()
    except Exception:
        # A diagnostic must never be what breaks `mnemo doctor`.
        return True
    if not findings:
        return True
    for msg in findings:
        print(f"  ⚠ {msg}" if not msg.startswith("    ") else f"  {msg}")
    return False
