# src/mnemo/hooks/pre_tool_use.py
"""PreToolUse hook entry point.

Two responsibilities:

1. Enforcement: if a Bash command matches a deny rule, emit a deny envelope so
   Claude Code rejects the tool call before it runs.
2. Enrichment: if a Read/Edit/Write/MultiEdit path matches an activates_on
   rule whose glob names that file, emit an additionalContext envelope so
   Claude Code prepends the rule body — once per rule per session (#271).

Fail-open absolute: any exception at any stage returns exit code 0 with empty
stdout. This hook MUST NEVER block Claude Code from running.
"""
from __future__ import annotations

import json
import sys

_ENFORCE_TOOL = "Bash"
_ENRICH_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit"})


def main() -> int:
    # Nothing at all inside a session mnemo launched for itself (#329): the
    # `claude --print` helpers that brief and extract run under the user's own
    # settings, mnemo's hooks included, so an unguarded hook here schedules the
    # work whose helper is running it. See :mod:`mnemo.core.hook_guard`.
    from mnemo.core.hook_guard import hooks_off

    if hooks_off():
        return 0

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
        from mnemo.core.agent import resolve_canonical_agent

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

        project = resolve_canonical_agent(cwd).name

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
                hits = ra.match_path_enrich(
                    index, project, _repo_relative(file_path), tool_name,
                )
                if hits:
                    from mnemo.core.mcp import session_state
                    sid = str(payload.get("session_id") or "unknown")
                    # Once per slug per session, within
                    # enrichment.maxEmissionsPerSession. Fail-open: broken
                    # session state never blocks enrichment.
                    try:
                        max_enrich = int(enr_cfg.get("maxEmissionsPerSession", 15))
                        counts = session_state.read_emission_counts(vault, sid)
                        room = max_enrich - counts["enrich_count"]
                        if room <= 0:
                            return 0  # silent: cap reached
                        seen = session_state.read_enriched_slugs(vault, sid)
                        hits = [h for h in hits if h.slug not in seen][:room]
                    except Exception:
                        pass

                    if hits:
                        _emit_enrich(hits)
                        ra.log_enrichment(vault, hits, tool_name, tool_input)
                        try:
                            import time as _time
                            session_state.record_enrichment(
                                vault, sid=sid, slugs=[h.slug for h in hits],
                                now_ts=int(_time.time()),
                            )
                        except Exception:
                            pass

    except Exception as exc:  # noqa: BLE001 — hook must never propagate
        try:
            from mnemo.core import config as _cfg, errors as _err, paths as _paths
            _err.log_error(_paths.vault_root(_cfg.load_config()), "pre_tool_use.outer", exc)
        except Exception:
            pass
    return 0


def _repo_relative(file_path: str) -> str:
    """*file_path* relative to the git root enclosing it, POSIX-separated.

    Claude Code hands PreToolUse an absolute ``file_path`` while path globs are
    written relative to the repo; matched raw, a leading ``/`` defeats even
    ``**/`` — 0 of 3660 real Edit/Write calls ever matched (#271). The root is
    the file's own (a worktree's root, in a worktree). Both sides are resolved
    so a symlinked prefix cannot break the comparison. A path outside any repo
    comes back unchanged.
    """
    try:
        from pathlib import Path
        from mnemo.core.agent import _find_git_root

        path = Path(file_path)
        if not path.is_absolute():
            return file_path.replace("\\", "/")
        root = _find_git_root(path.parent)
        if root is None:
            return file_path
        return path.resolve().relative_to(root).as_posix()
    except Exception:
        return file_path


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
