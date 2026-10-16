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

import contextlib
import io
import json
import os
import sys
from typing import Optional, Tuple

_ENFORCE_TOOL = "Bash"
_ENRICH_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit"})
#: The tools that write the file `file_path` names (#637). Read is not one:
#: Claude Code reads the account spelling of a memory file without asking.
_MEMORY_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})


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

    if _is_grok(payload):
        return _grok(payload)
    return _run(payload)


def _run(payload: dict) -> int:
    # A memory write spelled through an account dir's linked `projects` goes
    # to its real path (#637). Before every gate below: it reads no vault and
    # no config, and a session in a temp dir or with enforcement and
    # enrichment both off is stopped by the same prompt.
    rewrite, redirect = _memory_write(payload)
    if redirect is not None:
        _emit_deny(_Reason(redirect))
        return 0
    if not _rules(payload, rewrite) and rewrite is not None:
        _emit_rewrite(rewrite)
    return 0


def _is_grok(payload: dict) -> bool:
    try:
        from mnemo.core.grok import hooks as grok

        return grok.is_grok_payload(payload)
    except Exception:  # noqa: BLE001 — hook must never propagate
        return False


def _grok(payload: dict) -> int:
    """The same hook for a Grok Build session (spec 2026-10-16-grok-host).

    Grok names its tools its own way (``run_terminal_command``, ``read_file``
    with ``target_file``, ``write``, ``search_replace``, ``hashline_edit``), so
    the payload is put in Claude's vocabulary first and every gate below runs
    as written. Then this call carries what UserPromptSubmit and SessionStart
    could not hand the model themselves (:mod:`mnemo.core.grok.hooks`): the
    context waiting for this session goes out ahead of any enrichment, once.
    A denied call takes none; the next call does. Fail-open throughout.
    """
    try:
        from mnemo.core.grok import hooks as grok

        if not grok.claim_once(payload):
            return 0
        payload = grok.normalize(payload)
    except Exception:  # noqa: BLE001
        return 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _run(payload)
    out = buf.getvalue()
    try:
        if not grok.is_deny(out):
            out = grok.with_context(out, _grok_pending(payload))
    except Exception:  # noqa: BLE001
        pass
    if out:
        try:
            sys.stdout.write(out)
            sys.stdout.flush()
        except Exception:
            pass
    return 0


def _grok_pending(payload: dict) -> str:
    """This Grok session's waiting context, taken; ``""`` when there is none."""
    try:
        from mnemo.core import config, paths
        from mnemo.core.grok import hooks as grok

        sid = grok.session_id(payload)
        if not sid:
            return ""
        return grok.take(paths.vault_root(config.load_config()), sid)
    except Exception:  # noqa: BLE001
        return ""


def _memory_write(payload: dict) -> Tuple[Optional[dict], Optional[str]]:
    """``(rewrite, redirect)`` for a write to auto-memory markdown that an
    account dir reaches through a link (#637); ``(None, None)`` for every
    other call. See :mod:`mnemo.core.memory_path` for which paths those are.

    A Write that creates the file is *rewritten*: the tool input comes back
    with ``file_path`` as the real path and no other field changed. An Edit,
    a MultiEdit or a Write over a file that exists cannot be: Claude Code
    (2.1.296) checks "was this file read" twice, on the path the model asked
    for before this hook runs and on the path the tool ends up with, so a
    rewritten edit fails with "File has not been read yet" whichever spelling
    the Read used. Those are *redirected*: denied with a reason that names
    the real path, which the model then reads and edits itself.

    Neither approves anything. Fail-open: any error leaves the call alone.
    """
    try:
        tool_name = payload.get("tool_name")
        if tool_name not in _MEMORY_WRITE_TOOLS:
            return None, None
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return None, None
        from mnemo.core import memory_path

        real = memory_path.real_memory_path(
            tool_input.get("file_path"), os.environ.get(memory_path.CONFIG_DIR_ENV),
        )
        if real is None:
            return None, None
        if tool_name == "Write" and not os.path.lexists(real):
            return dict(tool_input, file_path=real), None
        return None, memory_path.redirect_reason(tool_input["file_path"], real)
    except Exception:  # noqa: BLE001 — hook must never propagate
        return None, None


def _rules(payload: dict, rewrite: Optional[dict]) -> bool:
    """Enforcement and enrichment. True when an envelope went out: a deny, or
    an enrichment that carries *rewrite* with it, since a hook prints one."""
    try:
        from mnemo.core import config, errors, paths
        from mnemo.core.hook_guard import throwaway_session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        # Before every other gate (#597): publishing a private repo's name is
        # a leak from any session, a throwaway one included, and the check
        # stands on <vault>/.mnemo/private-names.tsv alone, not on config.
        if payload.get("tool_name") == _ENFORCE_TOOL and _private_name_deny(payload, vault):
            return True
        # A session in a temp, pytest or job-scratch dir writes nothing into
        # a vault that outlives it (#420). See hook_guard.throwaway_session.
        if throwaway_session(payload.get("cwd") or os.getcwd(), vault):
            return False

        enf_cfg = cfg.get("enforcement", {}) or {}
        enr_cfg = cfg.get("enrichment", {}) or {}
        enf_enabled = bool(enf_cfg.get("enabled", False))
        enr_enabled = bool(enr_cfg.get("enabled", False))
        if not (enf_enabled or enr_enabled):
            return False

        if not errors.should_run(vault):
            return False

        from mnemo.core import rule_activation as ra
        from mnemo.core.agent import resolve_canonical_agent

        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        cwd = payload.get("cwd") or ""
        if not tool_name:
            return False

        index = ra.load_index(vault)
        if index is None:
            return False

        project = resolve_canonical_agent(cwd).name

        # Enforcement first — if a deny fires, never continue to enrichment
        if enf_enabled and tool_name == _ENFORCE_TOOL:
            command = tool_input.get("command") or ""
            hit = ra.match_bash_enforce(index, project, command)
            if hit is not None:
                _emit_deny(hit)
                ra.log_denial(vault, hit, tool_input)
                return True

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
                            return False  # silent: cap reached
                        seen = session_state.read_enriched_slugs(vault, sid)
                        hits = [h for h in hits if h.slug not in seen][:room]
                    except Exception:
                        pass

                    if hits:
                        _emit_enrich(hits, rewrite)
                        ra.log_enrichment(vault, hits, tool_name, tool_input)
                        try:
                            import time as _time
                            session_state.record_enrichment(
                                vault, sid=sid, slugs=[h.slug for h in hits],
                                now_ts=int(_time.time()),
                            )
                        except Exception:
                            pass
                        return True

    except Exception as exc:  # noqa: BLE001 — hook must never propagate
        try:
            from mnemo.core import config as _cfg, errors as _err, paths as _paths
            _err.log_error(_paths.vault_root(_cfg.load_config()), "pre_tool_use.outer", exc)
        except Exception:
            pass
    return False


def _private_name_deny(payload: dict, vault) -> bool:
    """Deny a Bash command that would publish a private repo's name (#597).

    True when the deny envelope went out. Fail-open: a broken list or a git
    that errors is logged to ``.errors.log`` and the command runs.
    """
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return False
    try:
        from mnemo.core import private_names

        reason = private_names.check(command, str(payload.get("cwd") or ""), vault)
    except Exception as exc:  # noqa: BLE001 — fail open
        try:
            from mnemo.core import errors
            errors.log_error(vault, "pre_tool_use.private_names", exc)
        except Exception:
            pass
        return False
    if reason is None:
        return False
    _emit_deny(_Reason(reason))
    return True


class _Reason:
    """The shape :func:`_emit_deny` reads: a reason and no rule path."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


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


def _emit_rewrite(updated_input: dict) -> None:
    """The rewritten input alone: no ``permissionDecision``, so Claude Code's
    own handling of the new path is still what decides (#637)."""
    try:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated_input,
            },
        }))
        sys.stdout.flush()
    except Exception:
        pass


def _emit_enrich(hits: list, updated_input: Optional[dict] = None) -> None:
    try:
        max_rules = 3  # safety cap; rule_activation also caps
        parts = []
        for h in hits[:max_rules]:
            parts.append(f"• mnemo rule [[{h.slug}]]:\n{h.rule_body_preview}")
        text = "\n\n".join(parts)
        out = {"hookEventName": "PreToolUse", "additionalContext": text}
        if updated_input is not None:
            out["updatedInput"] = updated_input
        sys.stdout.write(json.dumps({"hookSpecificOutput": out}))
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
