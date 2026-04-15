# src/mnemo/hooks/session_start.py
"""SessionStart hook entry point.

Two responsibilities:

1. Cache session metadata + mirror Claude memories + log the start (v0.2+).
2. v0.5: when ``injection.enabled`` is true, emit a JSON payload on stdout
   that Claude Code interprets as ``additionalContext``, listing the topic
   tags Claude can reach via the mnemo MCP server. Disabled by default.

The injection block is wrapped in a defensive try/except — a failure here
must NEVER block Claude session startup, since the hook runs on every new
or resumed conversation.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


def _build_injection_payload(vault_root: Path, current_project: str | None = None) -> str:
    """Return the ~120-token instruction string, or '' when there's nothing to inject.

    When *current_project* is provided, topics are filtered to those owned by
    that project in the rule-activation index. Falls back to the vault-wide
    union when the index is absent or the project has no indexed rules.
    """
    from mnemo.core.mcp.tools import get_mnemo_topics

    vault_wide_topics = get_mnemo_topics(vault_root)

    topics: list[str] = vault_wide_topics  # default: vault-wide union

    if current_project:
        try:
            from mnemo.core import rule_activation
            idx = rule_activation.load_index(vault_root)
            if idx is not None:
                current_project_topics: set[str] = set()
                for rule in idx.get("enrich_by_project", {}).get(current_project, []):
                    for tag in rule.get("topic_tags", []):
                        current_project_topics.add(tag)
                for rule in idx.get("enforce_by_project", {}).get(current_project, []):
                    for tag in rule.get("topic_tags", []):
                        current_project_topics.add(tag)
                if current_project_topics:
                    topics = sorted(current_project_topics)
        except Exception:
            pass  # fall through to vault_wide_topics already assigned

    if not topics:
        return ""
    topics_str = ", ".join(topics)
    return (
        "You have access to the mnemo project brain via MCP. "
        f"Known topics in this vault: [{topics_str}]. "
        "When the current task touches any of these, call "
        "`list_rules_by_topic(topic)` then `read_mnemo_rule(slug)` "
        "BEFORE writing code."
    )


def _emit_injection(payload_text: str, out: object = None) -> None:
    """Write the SessionStart hookSpecificOutput envelope to stdout."""
    out_stream = out if out is not None else sys.stdout
    out_stream.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": payload_text,
        },
    }))
    out_stream.flush()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, mirror, paths, session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cwd = payload.get("cwd") or os.getcwd()
        ainfo = agent.resolve_agent(cwd)
        info = {
            **asdict(ainfo),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "cwd_at_start": cwd,
        }
        try:
            session.save(sid, info)
            session.cleanup_stale()
        except Exception as e:
            errors.log_error(vault, "session_start.cache", e)
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            errors.log_error(vault, "session_start.mirror", e)

        # Rebuild rule-activation index — freshness guarantee in case the user
        # manually edited a rule file between sessions. Only runs when at least
        # one activation flag is on; disabled sessions pay zero cost (no import,
        # no file I/O).
        enf_enabled = bool(cfg.get("enforcement", {}).get("enabled", False))
        enr_enabled = bool(cfg.get("enrichment", {}).get("enabled", False))
        if enf_enabled or enr_enabled:
            try:
                from mnemo.core import rule_activation
                rule_activation.write_index(vault, rule_activation.build_index(vault))
            except Exception as exc:
                errors.log_error(vault, "session_start.rule_activation_index", exc)

        if cfg.get("capture", {}).get("sessionStartEnd", True):
            source = payload.get("source", "startup")
            try:
                log_writer.append_line(ainfo.name, f"🟢 session started ({source})", cfg)
            except Exception as e:
                errors.log_error(vault, "session_start.log", e)

        # v0.5 injection — opt-in, fail-silent. Must run last so the JSON
        # envelope is the only thing on stdout.
        if cfg.get("injection", {}).get("enabled", False):
            try:
                payload_text = _build_injection_payload(vault, current_project=ainfo.name)
                if payload_text:
                    _emit_injection(payload_text)
            except Exception as e:
                errors.log_error(vault, "session_start.injection", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_start.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
