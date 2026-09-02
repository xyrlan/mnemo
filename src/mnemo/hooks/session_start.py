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


def _build_injection_payload(
    vault_root: Path,
    current_project: str | None = None,
    inject_briefing: bool = False,
) -> str:
    """Return a structured ``mnemo://v1`` envelope, or '' when there's nothing to inject.

    Reads the rule-activation-index.json for per-scope topic lists; degrades to
    ``get_mnemo_topics`` over glob+parse when the index is unavailable. Applies
    ``injection.maxTopicsPerScope`` as a cap on each of the local and universal
    topic lines. Topics are ordered by aggregated ``source_count`` descending,
    with a stable secondary sort by name.

    When ``inject_briefing`` is True and ``current_project`` has at least one
    briefing on disk, appends a ``[last-briefing session=… date=… duration_minutes=…]``
    block (verbatim body) as the last section. The block is omitted on any
    read/parse failure or when no briefing exists.
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
        if current_project:
            local_slugs = idx.get("by_project", {}).get(current_project, {}).get("local_slugs", [])
            local_rules = [
                rules_table[s] for s in local_slugs
                if s in rules_table and not rules_table[s].get("universal")
            ]
            local_counts = _aggregate_topic_counts(local_rules)
            local_topics = [
                t for t, _ in sorted(local_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ][:max_topics]
        universal_slugs = idx.get("universal", {}).get("slugs", [])
        universal_rules = [rules_table[s] for s in universal_slugs if s in rules_table]
        universal_counts = _aggregate_topic_counts(universal_rules)
        universal_topics = [
            t for t, _ in sorted(universal_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:max_topics]
    else:
        vault_wide = get_mnemo_topics(vault_root, scope="vault")
        universal_topics = vault_wide[:max_topics]

    # Topic block. Was an early `return ""` when both lists were empty; v0.10
    # defers the empty check to the end so a briefing-only envelope can still
    # be emitted.
    topic_lines: list[str] = []
    if local_topics or universal_topics:
        header = "mnemo://v1"
        if current_project and local_topics:
            header += f" project={current_project}"
        topic_lines.append(header)
        if local_topics:
            topic_lines.append(f"local: [{', '.join(local_topics)}]")
        if universal_topics:
            topic_lines.append(f"universal: [{', '.join(universal_topics)}]")
        topic_lines.append(
            'Call list_rules_by_topic(topic, query="<your task>") then read_mnemo_rule(slug) BEFORE writing code.'
        )
        topic_lines.append(
            'Use scope="project" for local+universal, scope="local-only" to exclude universal.'
        )

    # v0.10 NEW: append the most recent briefing for current_project, if any.
    briefing_block = ""
    if inject_briefing and current_project:
        try:
            from mnemo.core import briefing as briefing_mod
            rec = briefing_mod.pick_latest_briefing(vault_root, current_project)
            if rec is not None:
                fm = rec.frontmatter
                framing = (
                    f"[last-briefing session={fm.get('session_id', rec.path.stem)} "
                    f"date={fm.get('date', '')} "
                    f"duration_minutes={fm.get('duration_minutes', '0')}]"
                )
                briefing_block = (
                    "\n\n"
                    + framing
                    + "\n"
                    + rec.body.rstrip()
                    + "\n[/last-briefing]"
                )
        except Exception:
            briefing_block = ""

    # v0.11 NEW: append predicted rules from preempt-cache, if fresh.
    preempt_block = ""
    if current_project:
        try:
            from mnemo.autopilot.proposer.preempt import read_preempt_cache

            cache = read_preempt_cache(vault_root=vault_root)
            if (
                cache is not None
                and cache.get("project") == current_project
                and cache.get("slugs")
            ):
                slugs = cache["slugs"]
                preempt_block = (
                    "\n\n[predicted-rules session=preempt "
                    f"slugs={','.join(slugs)}]\n"
                    "These rules are predicted relevant based on current git context. "
                    "You may call read_mnemo_rule(slug) to load any of them.\n"
                    "[/predicted-rules]"
                )
        except Exception:
            preempt_block = ""

    if not topic_lines and not briefing_block and not preempt_block:
        return ""
    return "\n".join(topic_lines) + briefing_block + preempt_block


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


def _warn_about_duplicate_install(vault) -> None:
    """Tell the user once when a plugin install overlaps a `mnemo init` one.

    Only meaningful under the plugin: outside it, mnemo hooks in settings.json
    *are* the install, not a leftover. CLAUDE_PLUGIN_ROOT is how we tell.
    """
    from pathlib import Path

    if not os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return

    from mnemo.install import migration

    state = Path(vault) / ".mnemo" / "plugin-migration-notice"
    if not migration.should_notify(state):
        return

    candidates = [
        Path.home() / ".claude" / "settings.json",
        Path(os.getcwd()) / ".claude" / "settings.json",
    ]
    legacy = migration.find_legacy_installs(candidates)
    if not legacy:
        return

    print(migration.notice(legacy), file=sys.stderr)
    migration.mark_notified(state)


def _spawn_detached_backfill(cwd: str | None = None) -> None:
    """Fire-and-forget background install backfill via subprocess.Popen.

    Invokes ``mnemo backfill --install-run``. Detach semantics match
    session_end's briefing spawn — see hooks/session_end.py:139.

    ``cwd`` is the session's working directory, and it is not decoration:
    ``backfill --install-run`` picks the repo to sweep with
    ``resolve_canonical_agent(os.getcwd())``, and Popen otherwise inherits
    whatever directory the hook process was launched in. Passed only when it
    still exists — a stale path would make Popen raise before the child ran.
    """
    import subprocess

    from mnemo._selfexec import self_argv

    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    kwargs["cwd"] = cwd if cwd and os.path.isdir(cwd) else None

    subprocess.Popen(self_argv("backfill", "--install-run"), **kwargs)


def _maybe_schedule_install_backfill(
    cfg: dict, vault_root, cwd: str | None = None
) -> None:
    """Spawn the one-time install backfill, at most once per vault.

    Two separate pieces of state, because "we launched something" and "a
    backfill happened" are different facts and conflating them silently burns
    the user's only automatic run:

    - ``installRunDone`` says a sweep **finished**. This function only ever
      reads it; ``cmd_backfill`` writes it, and only on a run that got to the
      end. So a child that dies on a missing ``claude`` CLI, expired auth or a
      rate limit — printing its explanation to a ``DEVNULL`` stderr nobody
      will ever see — leaves the one-shot unspent and the next session tries
      again. The cost of that retry is one fast failed CLI invocation and no
      LLM call, which is the right price for not silently abandoning the
      feature this vault was installed for.
    - the spawn lock says a sweep is **in flight**, and is what stops two
      simultaneous sessions from launching two sweeps.

    A ``Popen`` that raises releases the lock immediately: nothing was
    launched, so nothing should be held.
    """
    try:
        from mnemo.core.backfill import ledger as _ledger

        backfill_cfg = cfg.get("backfill") or {}
        if not backfill_cfg.get("enabled", True):
            return
        if not backfill_cfg.get("autoOnFirstSession", True):
            return

        if _ledger.load(vault_root).get("installRunDone"):
            # Retire a lock the finished run leaked. This check returns before
            # `acquire_spawn_lock`, and `acquire` is the only code that reaps a
            # lock by TTL — so past this point a leftover lock is immortal: no
            # session consults it, nothing removes it, and doctor reports a
            # sweep that finished as one that "never finished", forever. Age
            # is irrelevant, because `cmd_backfill` marks before it releases:
            # a lock coexisting with the marker belongs to a process already
            # past its work. This hook is the lock's only consumer, so it is
            # the only thing that can honestly retire one.
            _ledger.release_spawn_lock(vault_root)
            return
        if not _ledger.acquire_spawn_lock(vault_root):
            return  # another session is already sweeping

        try:
            _spawn_detached_backfill(cwd=cwd)
        except Exception:
            _ledger.release_spawn_lock(vault_root)
            raise
    except Exception as exc:
        try:
            from mnemo.core import errors as _e

            _e.log_error(vault_root, "session_start.backfill", exc)
        except Exception:
            pass


def _first_run_notice(vault_root: Path, cfg: dict, project: str) -> str:
    """One line, once per vault, instead of spending LLM calls unasked.

    ``backfill.autoOnFirstSession`` now defaults to False, so a fresh install
    no longer harvests the user's transcript history the moment it is
    installed. That is the right default only if they are told the history is
    *there* — otherwise the feature they installed mnemo for is invisible and
    the change reads as removing it. This is the invitation.

    The one-shot is spent on a notice actually worth showing:

    - a completed sweep (``installRunDone``) has nothing left to invite,
    - zero transcripts leaves the flag unset, so a repo that accumulates
      history later still gets its invitation rather than having burned it
      while empty.

    Fail-silent, like everything else on the session-start path.
    """
    try:
        from mnemo.core.backfill import discover, ledger as _ledger

        backfill_cfg = cfg.get("backfill") or {}
        if not backfill_cfg.get("enabled", True):
            return ""
        led = _ledger.load(vault_root)
        if led.get("installRunDone") or _ledger.notice_shown(led):
            return ""
        n = len(discover.find_transcripts(project=project))
        if n == 0:
            return ""
        _ledger.mark_notice_shown(vault_root)
        return (
            f"[mnemo] first run: {n} past session(s) for this repo can be learned "
            f"with `mnemo backfill` (opt-in, about {n} Haiku calls)."
        )
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.first_run_notice", exc)
        except Exception:
            pass
        return ""


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
        # A plugin install never runs `mnemo init`, so nothing else scaffolds
        # the vault: the hooks below would create only the directories they
        # touch, leaving no HOME.md, no config, and no shared/ for extracted
        # rules to land in. scaffold_vault is idempotent and skips files that
        # already exist, so this is a no-op on every subsequent session.
        try:
            if not (Path(vault) / "HOME.md").exists():
                from mnemo.install import scaffold
                scaffold.scaffold_vault(vault)
        except Exception as e:
            errors.log_error(vault, "session_start.scaffold", e)

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
            session.cleanup_stale(max_age_seconds=48 * 3600)
        except Exception as e:
            errors.log_error(vault, "session_start.cache", e)
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            errors.log_error(vault, "session_start.mirror", e)

        # Rebuild rule-activation index when any of the three consumers needs it:
        # enforcement (PreToolUse deny), enrichment (PreToolUse context), or
        # injection (SessionStart topic list). Reflex is NOT a consumer of this
        # index — it builds its own BM25F index (reflex-index.json) below.
        # Disabled-everything sessions still pay zero cost.
        inj_enabled = bool(cfg.get("injection", {}).get("enabled", False))
        enf_enabled = bool(cfg.get("enforcement", {}).get("enabled", False))
        enr_enabled = bool(cfg.get("enrichment", {}).get("enabled", False))
        reflex_enabled = bool(cfg.get("reflex", {}).get("enabled", False))
        if enf_enabled or enr_enabled or inj_enabled:
            try:
                from mnemo.core import rule_activation
                rule_activation.write_index(vault, rule_activation.build_index(vault))
            except Exception as exc:
                errors.log_error(vault, "session_start.rule_activation_index", exc)

        if reflex_enabled:
            try:
                from mnemo.core.reflex import index as reflex_index
                reflex_index.write_index(vault, reflex_index.build_index(vault))
            except Exception as exc:
                errors.log_error(vault, "session_start.reflex_index", exc)

        # A brand-new vault injects on 0% of prompts until something puts rules
        # in it. Runs at most once per vault, capped, detached — one LLM call
        # per session harvested is minutes of work and must not touch the
        # prompt path. Must stay below `errors.should_run`, which is the kill
        # switch for every hook; it does not depend on scaffolding, since the
        # lock and ledger writes create `.mnemo/` themselves.
        _maybe_schedule_install_backfill(cfg, vault, cwd)

        if cfg.get("capture", {}).get("sessionStartEnd", True):
            source = payload.get("source", "startup")
            try:
                log_writer.append_line(ainfo.name, f"🟢 session started ({source})", cfg)
            except Exception as e:
                errors.log_error(vault, "session_start.log", e)

        # Plugin installs cannot rewrite settings.json, so a leftover
        # `mnemo init` from before the plugin keeps firing alongside it and
        # everything happens twice. Report it once, on stderr — stdout carries
        # the injection envelope and must stay pure JSON.
        try:
            _warn_about_duplicate_install(vault)
        except Exception as e:
            errors.log_error(vault, "session_start.migration_notice", e)

        # v0.5 injection — opt-in, fail-silent. Must run last so the JSON
        # envelope is the only thing on stdout.
        if cfg.get("injection", {}).get("enabled", False):
            try:
                canonical_name = agent.resolve_canonical_agent(cwd).name
                inject_briefing = bool(cfg.get("briefings", {}).get("injectLastOnSessionStart", True))
                payload_text = _build_injection_payload(
                    vault,
                    current_project=canonical_name,
                    inject_briefing=inject_briefing,
                )
                # The notice stands on its own: a brand-new vault has no
                # topics and no briefing, so the payload it would ride along
                # with is empty on exactly the session the notice exists for.
                notice = _first_run_notice(vault, cfg, canonical_name)
                if notice:
                    payload_text = (
                        payload_text + "\n\n" + notice if payload_text else notice
                    )
                if payload_text:
                    _emit_injection(payload_text)
                    try:
                        from mnemo.core.mcp import access_log as _al
                        _al.record_session_start_inject(
                            vault,
                            envelope_bytes=len(payload_text.encode("utf-8")),
                            included_briefing=("[last-briefing" in payload_text),
                            project=canonical_name,
                            agent=canonical_name,
                        )
                    except Exception as exc:
                        errors.log_error(vault, "session_start.inject_telemetry", exc)
            except Exception as e:
                errors.log_error(vault, "session_start.injection", e)

        # autopilot — fire any due hook-driven operations. Always best-effort:
        # any failure here is logged + swallowed, must never block the session.
        try:
            from mnemo.autopilot.core.scheduler import run_due_jobs
            run_due_jobs(vault_root=vault)
        except Exception as e:
            errors.log_error(vault, "session_start.autopilot", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_start.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
