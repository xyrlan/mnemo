# src/mnemo/hooks/pre_tool_use.py
"""PreToolUse hook entry point.

Two responsibilities:

1. Enforcement: if a Bash command matches a deny rule, emit a deny envelope so
   Claude Code rejects the tool call before it runs.
2. Enrichment: if an Edit/Write/MultiEdit path matches an activates_on rule,
   emit an additionalContext envelope so Claude Code prepends the rule body.

Fail-open absolute: any exception at any stage returns exit code 0 with empty
stdout. This hook MUST NEVER block Claude Code from running.
"""
from __future__ import annotations

import json
import sys

_ENFORCE_TOOL = "Bash"
_ENRICH_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0

    try:
        from mnemo.core import config, errors, paths

        cfg = config.load_config()
        vault = paths.vault_root(cfg)

        enf_cfg = cfg.get("enforcement", {}) or {}
        enr_cfg = cfg.get("enrichment", {}) or {}
        enf_enabled = bool(enf_cfg.get("enabled", False))
        enr_enabled = bool(enr_cfg.get("enabled", False))
        if not (enf_enabled or enr_enabled):
            return 0

        if not errors.should_run(vault):
            return 0

        from mnemo.core import rule_activation as ra
        from mnemo.core.agent import resolve_agent

        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        cwd = payload.get("cwd") or ""
        if not tool_name:
            return 0

        index = ra.load_index(vault)
        if index is None:
            return 0

        project = resolve_agent(cwd).name

        # Enforcement first — if a deny fires, never continue to enrichment
        if enf_enabled and tool_name == _ENFORCE_TOOL:
            command = tool_input.get("command") or ""
            hit = ra.match_bash_enforce(index, project, command)
            if hit is not None:
                _emit_deny(hit)
                ra.log_denial(vault, hit, tool_input)
                return 0

        # Enrichment for path-based tools
        if enr_enabled and tool_name in _ENRICH_TOOLS:
            file_path = tool_input.get("file_path") or ""
            if file_path:
                hits = ra.match_path_enrich(index, project, file_path, tool_name)
                if hits:
                    # Reflex integration (v0.8):
                    #   1. Enforce enrichment.maxEmissionsPerSession cap.
                    #   2. Filter hits against session-wide injected_cache.
                    try:
                        from mnemo.core.mcp import session_state
                        sid = str(payload.get("session_id") or "unknown")
                        max_enrich = int(enr_cfg.get("maxEmissionsPerSession", 15))
                        counts = session_state.read_emission_counts(vault, sid)
                        if counts["enrich_count"] >= max_enrich:
                            return 0  # silent: cap reached
                        cache = session_state.read_injected_cache(vault)
                        hits = [h for h in hits if h.slug not in cache]
                        if not hits:
                            return 0
                    except Exception:
                        # fail-open — never block enrichment because session-state is broken
                        pass

                    if hits:
                        _emit_enrich(hits)
                        ra.log_enrichment(vault, hits, tool_name, tool_input)
                        # Record emission + cache updates.
                        try:
                            import time as _time
                            now_ts = int(_time.time())
                            for h in hits:
                                session_state.add_injection(vault, slug=h.slug, sid=sid, now_ts=now_ts)
                                session_state.bump_emission(vault, sid=sid, kind="enrich", now_ts=now_ts)
                        except Exception:
                            pass

    except Exception as exc:  # noqa: BLE001 — hook must never propagate
        try:
            from mnemo.core import config as _cfg, errors as _err, paths as _paths
            _err.log_error(_paths.vault_root(_cfg.load_config()), "pre_tool_use.outer", exc)
        except Exception:
            pass
    return 0


def _emit_deny(hit) -> None:
    try:
        lines = [hit.reason]
        if getattr(hit, "path", ""):
            lines.append(f"Rule: {hit.path}")
            lines.append(
                f"Fix: edit the file to remove or narrow the enforce block, "
                f"or run `mnemo disable-rule {hit.slug}`."
            )
        reason = "\n".join(lines)
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }))
        sys.stdout.flush()
    except Exception:
        pass


def _emit_enrich(hits: list) -> None:
    try:
        max_rules = 3  # safety cap; rule_activation also caps
        parts = []
        for h in hits[:max_rules]:
            parts.append(f"• mnemo rule [[{h.slug}]]:\n{h.rule_body_preview}")
        text = "\n\n".join(parts)
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": text,
            },
        }))
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
