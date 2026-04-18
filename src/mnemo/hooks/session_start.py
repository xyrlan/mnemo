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
    """Return a structured ``mnemo://v1`` envelope, or '' when there's nothing to inject.

    Reads the rule-activation-index.json for per-scope topic lists; degrades to
    ``get_mnemo_topics`` over glob+parse when the index is unavailable. Applies
    ``injection.maxTopicsPerScope`` as a cap on each of the local and universal
    topic lines. Topics are ordered by aggregated ``source_count`` descending,
    with a stable secondary sort by name.
    """
    from mnemo.core import config as cfg_mod
    from mnemo.core import rule_activation
    from mnemo.core.mcp.tools import get_mnemo_topics

    cfg = cfg_mod.load_config()
    max_topics = int(cfg.get("injection", {}).get("maxTopicsPerScope", 15))

    idx = rule_activation.load_index(vault_root)

    def _aggregate_topic_counts(rules_subset: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rule in rules_subset:
            weight = rule.get("source_count", 0)
            for t in rule.get("topic_tags", []):
                counts[t] = counts.get(t, 0) + weight
        return counts

    local_topics: list[str] = []
    universal_topics: list[str] = []

    if idx is not None and "rules" in idx:
        rules_table = idx["rules"]
        # Local (excluding those that are also universal — avoid double listing
        # across both lines).
        if current_project:
            local_slugs = idx.get("by_project", {}).get(current_project, {}).get("local_slugs", [])
            local_rules = [rules_table[s] for s in local_slugs if s in rules_table and not rules_table[s].get("universal")]
            local_counts = _aggregate_topic_counts(local_rules)
            local_topics = [
                t for t, _ in sorted(local_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ][:max_topics]
        # Universal
        universal_slugs = idx.get("universal", {}).get("slugs", [])
        universal_rules = [rules_table[s] for s in universal_slugs if s in rules_table]
        universal_counts = _aggregate_topic_counts(universal_rules)
        universal_topics = [
            t for t, _ in sorted(universal_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:max_topics]
    else:
        # Fallback: use vault-wide topics, split cannot be derived.
        vault_wide = get_mnemo_topics(vault_root, scope="vault")
        universal_topics = vault_wide[:max_topics]

    if not local_topics and not universal_topics:
        return ""

    lines: list[str] = []
    header = "mnemo://v1"
    if current_project and local_topics:
        header += f" project={current_project}"
    lines.append(header)
    if local_topics:
        lines.append(f"local: [{', '.join(local_topics)}]")
    if universal_topics:
        lines.append(f"universal: [{', '.join(universal_topics)}]")
    lines.append(
        "Call list_rules_by_topic(topic) then read_mnemo_rule(slug) BEFORE writing code."
    )
    lines.append(
        'Use scope="project" for local+universal, scope="local-only" to exclude universal.'
    )
    return "\n".join(lines)


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
