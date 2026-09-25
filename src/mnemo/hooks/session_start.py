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
import re
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


_PRUNE_MARKER = ".mnemo/briefings-prune.last"
_PRUNE_INTERVAL = 7 * 86400


def _maybe_prune_briefings(vault: Path, cfg: dict) -> None:
    """Run ``briefing.prune`` at most once per week (#116). Cheap but not free:
    it parses every live rule's frontmatter to build the protected set."""
    from mnemo.core import briefing as briefing_mod

    marker = Path(vault) / _PRUNE_MARKER
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < _PRUNE_INTERVAL:
            return
    except OSError:
        pass
    briefing_mod.prune(vault, cfg)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def _maybe_repair_hook_matchers(vault: Path, cfg: dict, cwd: str | None = None) -> None:
    """Bring an installed hook matcher up to what this version ships (#337).

    ``mnemo init`` writes each matcher once, so a release that widens one —
    #271 added ``Read`` to ``PreToolUse`` — reaches nobody who already has
    mnemo installed. Before this, only ``mnemo doctor`` said so, and ``doctor``
    is opt-in: the maintainer's own install ran a week at half reach (23 notes
    where the current matcher delivers 40) with ``status`` calling it healthy.

    The write is the narrow one #303 built for exactly this: mnemo's own hook
    entries in ``settings.json``, after a backup, leaving config, statusLine,
    MCP and other tools' hooks alone. It happens once per distinct drift, so a
    matcher the user narrows back by hand afterwards is theirs to keep.

    The current session already loaded its hooks and keeps them; the notice
    goes to stderr because stdout carries the injection envelope.
    """
    from mnemo.install import hook_drift

    if not cfg.get("install", {}).get("autoRepairHooks", True):
        return
    for notice in hook_drift.auto_repair(vault, cwd=Path(cwd) if cwd else None):
        print(notice, file=sys.stderr)


# Sources whose context already holds this session's briefing, or should not
# have it pushed back in. Claude Code records the source in each hook record's
# ``hookName`` (``SessionStart:resume``), which is how #352 counted re-fires:
# over 1259 SessionStart records on the maintainer's machine, every repeated
# briefing came from ``resume`` (44) or ``fork`` (1).
#
# - ``resume``: the reloaded history already carries the first injection.
# - ``fork``: a forked session starts from a copy of that same history.
# - ``compact``: fires because the context is full; the compaction summary
#   already covers what the session kept, and a ~7 KB briefing would take
#   the space compaction just freed.
#
# ``clear`` is deliberately NOT here. ``/clear`` empties the context, so
# nothing is left to duplicate, and it is how people start a new task in the
# same terminal: 397 of those 1259 records were ``clear``, and 110 transcripts
# got their only briefing that way. Treat it as a cold start.
#
# A source not listed here, including one Claude Code adds later, keeps
# the briefing, which is how the hook behaved before this list existed.
_BRIEFING_ALREADY_IN_CONTEXT = frozenset({"resume", "fork", "compact"})


def _briefing_wanted(cfg: dict, source: str) -> bool:
    """Whether this SessionStart should carry the ``[last-briefing]`` block.

    Only the briefing is gated. The topic envelope is a few hundred bytes and
    carries the instruction to call ``list_rules_by_topic`` at all, so it
    still goes out on a resume.
    """
    if not cfg.get("briefings", {}).get("injectLastOnSessionStart", True):
        return False
    return source not in _BRIEFING_ALREADY_IN_CONTEXT


def _build_injection_payload(
    vault_root: Path,
    current_project: str | None = None,
    inject_briefing: bool = False,
    session_id: str | None = None,
    source: str | None = None,
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
    read/parse failure or when no briefing exists. ``session_id`` and
    ``source`` describe the session receiving it, for the briefing-log row.
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

    # v0.10 NEW: append a briefing for current_project, if any.
    briefing_block = ""
    if inject_briefing and current_project:
        try:
            from mnemo.core import briefing as briefing_mod
            from mnemo.core import briefing_select
            # query=None is newest-wins. The hook runs before the first prompt
            # exists, and the one task signal it does have — the branch — picked
            # the best-matching briefing no more often than newest-wins on the
            # real vault (tools/measure_briefing_query.py).
            rec = briefing_select.pick(vault_root, current_project, query=None)
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
                # The hook is this builder's only caller and emits whatever it
                # returns, so building the block is handing it to the session.
                # Its own try: telemetry must never cost the session its briefing.
                try:
                    briefing_mod.record_briefing_read(
                        vault_root, rec, reader_session_id=session_id, source=source,
                    )
                except Exception:
                    pass
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


def _spawn_detached(args: list, cwd: str | None = None) -> None:
    """Fire-and-forget ``mnemo <args>`` through :func:`mnemo._detach.spawn`.

    Detach semantics match session_end's spawns. ``SessionEnd`` starts the
    ``pr-follow`` watcher through here too, so the worker is orphaned for the
    same reason theirs are (#475).

    ``cwd`` is the session's working directory, and it is not decoration: a
    spawned command picks the repo it is about with
    ``resolve_canonical_agent(os.getcwd())``, and Popen otherwise inherits
    whatever directory the hook process was launched in. Passed only when it
    still exists — a stale path would make Popen raise before the child ran.
    """
    from mnemo._detach import spawn
    from mnemo._selfexec import self_argv

    spawn(self_argv(*args), cwd=cwd if cwd and os.path.isdir(cwd) else None)


def _spawn_detached_backfill(cwd: str | None = None) -> None:
    """Fire-and-forget background install backfill: ``mnemo backfill --install-run``."""
    _spawn_detached(["backfill", "--install-run"], cwd=cwd)


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

        from mnemo.core.config import DEFAULTS

        backfill_cfg = cfg.get("backfill") or {}
        if not backfill_cfg.get("enabled", True):
            return
        # The fallback is the documented default, read from the one place it
        # is defined. `load_config` deep-merges DEFAULTS, so in production the
        # key is always present and a wrong literal here is never exercised —
        # which is exactly how it sat at the opposite of the docs (#234).
        if not backfill_cfg.get(
            "autoOnFirstSession", DEFAULTS["backfill"]["autoOnFirstSession"]
        ):
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
    """One line, once per project, instead of spending LLM calls unasked.

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

    Shown once per *project*, not once per vault: "for this repo" is per
    project, and a second repo sharing this vault has its own history to
    invite. The call estimate is capped at ``backfill.installCap`` (20) —
    the actual run also skips already-harvested sessions and ones with no
    file mutations, so the true cost is usually lower than even this bound;
    the notice says so and points at ``--dry-run`` for the exact number.

    Fail-silent, like everything else on the session-start path.
    """
    try:
        from mnemo.core.backfill import discover as _discover
        from mnemo.core.backfill import ledger as _ledger

        backfill_cfg = cfg.get("backfill") or {}
        if not backfill_cfg.get("enabled", True):
            return ""
        led = _ledger.load(vault_root)
        if led.get("installRunDone") or _ledger.notice_shown(led, project):
            return ""
        n = len(_discover.find_transcripts(project=project))
        if n == 0:
            return ""
        _ledger.mark_notice_shown(vault_root, project)
        cap = int(backfill_cfg.get("installCap", 20))
        calls = min(n, cap)
        return (
            f"[mnemo] first run: {n} past session(s) for this repo can be learned "
            f"with `mnemo backfill` (opt-in, up to {calls} Haiku calls; "
            f"`mnemo backfill --dry-run` shows the exact cost)."
        )
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.first_run_notice", exc)
        except Exception:
            pass
        return ""


def _has_live_rule(shared: Path) -> bool:
    """True when any rule page sits directly in a live ``shared/<type>/`` dir.

    ``_inbox`` and ``_archive`` (every underscore dir) are not live, and a
    ``.proposed.md`` sibling is a rewrite, not a rule — same exclusions as
    ``filters.iter_shared_pages``, without its sorted full walk.
    """
    from mnemo.core import filters as _filters

    try:
        with os.scandir(shared) as dirs:
            for d in dirs:
                if not d.is_dir() or d.name.startswith("_"):
                    continue
                with os.scandir(d.path) as pages:
                    for f in pages:
                        if (
                            f.is_file()
                            and f.name.endswith(".md")
                            and not _filters.is_proposed_sibling(Path(f.path))
                        ):
                            return True
    except OSError:
        return False
    return False


def _staged_backfill_notice(vault_root: Path) -> str:
    """One line while reconstructed rules wait in ``_inbox`` and nothing is live.

    The first-run sweep stages everything it produces in ``shared/_inbox/``
    (backfilled pages are reconstructions and are never auto-promoted), and
    the reflex injects nothing from there. On a fresh vault that makes the
    sweep's entire output invisible from inside a session: the vault proper
    is still empty, so the next session injects nothing, and "the sweep
    staged twenty rules" is indistinguishable from "mnemo did nothing". The
    only surface that listed them was ``mnemo doctor`` (#234).

    Deliberately **stateless** — no ``firstRunNoticeShown``-style marker. A
    once-ever marker is spent the first time its condition is checked, not
    the first time it is true, which is how #229's warning went silent for
    three days. This reads the vault every session start and repeats while
    the condition holds; it stops on its own the moment the user acts,
    because the review move it asks for (keeper into ``shared/<type>/``, the
    rest deleted) is what clears it.

    Deliberately **narrow** — only while there is *no live rule at all*. That
    is the state the issue measured for, and the only one in which silence
    reads as failure. Once anything is live the user sees injection, and
    ``doctor`` still lists whatever is staged.

    Read-only: it counts, it never promotes. Fail-silent, like the rest of
    the session-start path; a single unreadable staged page is skipped, not
    fatal.
    """
    try:
        from mnemo.core import filters as _filters
        from mnemo.core.backfill.origin import is_backfill_frontmatter

        shared = Path(vault_root) / "shared"
        # The common case is a vault with live rules, and it must cost next to
        # nothing: one look into each live type dir, no full walk. The walk
        # below is only reached by a vault that is empty apart from _inbox.
        if _has_live_rule(shared):
            return ""
        staged = 0
        for md in _filters.iter_shared_pages(vault_root, include_inbox=True):
            rel = md.relative_to(shared).parts[:-1]
            if _filters.INBOX_DIR not in rel:
                return ""  # a live rule exists; the vault is no longer empty
            try:
                fm = _filters.parse_frontmatter(
                    md.read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                continue
            if is_backfill_frontmatter(fm):
                staged += 1
        if not staged:
            return ""
        return (
            f"[mnemo] {staged} rule(s) reconstructed from your past sessions "
            f"are staged in shared/{_filters.INBOX_DIR}/ and nothing is live yet "
            "— read each one, move the keepers to shared/<same type>/, delete "
            "the rest (`mnemo doctor` lists them)."
        )
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.staged_backfill_notice", exc)
        except Exception:
            pass
        return ""


def _share_import_notice(vault_root: Path, cfg: dict, project: str, cwd: str) -> str:
    """One line when the repo carries published rules this vault has not seen.

    ``mnemo publish`` (share-rules, #245) checks a rules tree into the repo
    at ``<repo>/.mnemo-shared/``; a newcomer's clone already has it, and
    nothing else tells them so. This is the invitation to ``mnemo import``.

    Same per-project discipline as the backfill invitation, with one
    refinement: the marker is a digest of *what is pending*, not a bare
    flag. A tree that gains rules after the first invitation invites again;
    one the user read and chose not to import stays quiet. Never once-ever —
    #229's once-ever warning went silent for three days.

    Writes only the marker and, on first use, the vault id that "is this
    mine" needs. ``cfg`` is taken for parity with the other notices; nothing
    in it is read yet. Fail-silent, like the rest of the session-start path;
    a tree that cannot be read is "nothing pending".
    """
    try:
        from mnemo.core import agent as _agent
        from mnemo.core.share import imports as _imports
        from mnemo.core.share.format import SHARE_DIR

        tree = Path(_agent.resolve_agent(cwd).repo_root) / SHARE_DIR
        if not tree.is_dir():
            return ""
        pending = _imports.pending_hashes(vault_root, tree)
        if not pending:
            return ""
        digest = _imports.pending_digest(pending)
        if _imports.notice_shown(_imports.load_ledger(vault_root), project, digest):
            return ""
        _imports.mark_notice_shown(vault_root, project, digest)
        n = len(pending)
        return (
            f"[mnemo] this repo publishes {n} rule(s) your vault has not imported "
            f"({SHARE_DIR}/) — `mnemo import --dry-run` shows what would be staged, "
            "`mnemo import` stages them in shared/_inbox/ for review."
        )
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.share_import_notice", exc)
        except Exception:
            pass
        return ""


#: Bullets per session. This block rides on the session-start prompt, so the
#: cap is a budget, not a preference; the tail line points at `mnemo status`
#: for the overflow rather than spending more of the prompt on it.
_LEARNED_MAX = 5
#: Evidence quotes are one line of a bullet, not a paragraph.
_QUOTE_MAX = 80
_NAME_MAX = 80
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
#: A staged page's key is ``<type>/<slug>``, and it is advertised inside a
#: command the user may paste into a shell — same guard as ``_SLUG_OK``, one
#: separator wider.
_KEY_OK = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
#: A staged page's description is the whole basis for judging it from the
#: prompt, and a long one would cost more than the page is worth.
_DESC_MAX = 100


def _one_line(value: object, limit: int) -> str:
    """Collapse any whitespace (incl. CR/LF) to one space and cap the length.

    Rule names and quotes are LLM-written or user-typed text injected into the
    agent's context. Without this, a newline inside a name could end the
    ``[/mnemo learned]`` fence early and put text outside any mnemo-attributed
    block.
    """
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[:limit] + "…"


def _learned_block(vault_root: Path, cfg: dict, project: str) -> str:
    """What extraction promoted since this project last looked, each with its undo.

    Extraction has always written rules silently. That is fine while the user
    reads the vault and wrong the moment mnemo is trusted to inject rules on
    its own: a rule nobody knows exists is a rule nobody can correct, so the
    bad ones accrue. This block is the disclosure half of that trade, and the
    ``veto:`` suffix is the reason it can stay one line — the correction is
    right there, not three commands away in a vault the user has never opened.

    Only a ``verified`` rule shows the sentence it was learned from. An
    ``inferred`` one has no quote to show, and manufacturing a plausible one
    would make the evidence worthless everywhere it *is* real.

    Announcing marks announced, so the same rule never appears twice — even
    when the render below is truncated at ``_LEARNED_MAX``: the overflow is
    still on disk and ``mnemo status`` still lists it.

    Fail-silent, like everything else on the session-start path.
    """
    try:
        from mnemo.core import learned

        threshold = int((cfg.get("scoping") or {}).get("universalThreshold", 2))
        entries = learned.pending(
            vault_root, project, limit=_LEARNED_MAX, universal_threshold=threshold
        )
        if not entries:
            return ""
        total = learned.pending_count(
            vault_root, project, universal_threshold=threshold
        )

        lines = ["[mnemo learned since your last session]"]
        for e in entries:
            slug = _one_line(e.get("slug"), _NAME_MAX)
            name = _one_line(e.get("name"), _NAME_MAX) or slug
            quote = e.get("quote")
            evidence = ""
            if e.get("confidence") == "verified" and quote:
                evidence = f' (verified from: "{_one_line(quote, _QUOTE_MAX)}")'
            # The veto is a command the user may paste into a shell: only
            # advertise it for a slug that is a plain token.
            veto = f" · veto: mnemo disable-rule {slug}" if _SLUG_OK.match(slug) else ""
            lines.append(f"• {slug} — {name}{evidence}{veto}")
        if total > len(entries):
            lines.append(f"({total - len(entries)} more — mnemo status)")
        lines.append("[/mnemo learned]")

        learned.mark_announced(vault_root, project)
        return "\n".join(lines)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.learned", exc)
        except Exception:
            pass
        return ""


def _staged_offer_block(vault_root: Path, cfg: dict, project: str) -> str:
    """What waits in ``shared/_inbox/`` for this project, with the act that clears it.

    The other half of :func:`_learned_block`. That one discloses what
    extraction *promoted* and hands over the veto; this one discloses what it
    *staged* and hands over the decision, because a staged page is invisible to
    recall and carries nothing while it waits. 194 of them on the real vault on
    2026-09-19, median age 5.3 days, drained only when someone thought to run
    ``mnemo doctor`` (#380).

    Bounded by three numbers under ``inbox`` in the config, not by a
    once-ever marker (#229's once-ever warning went silent for three days): at
    most ``offerMax`` bullets, one block per project per ``offerIntervalHours``,
    and no page repeated inside ``offerCooldownDays``. On the maintainer's own
    vault that is ~2 bullets a day against a briefing that costs ~1783 tokens
    at 90.9% of starts, and the block disappears entirely once the queue is
    empty.

    Offering marks offered, so the next session moves down the queue instead of
    repeating its head — and the ledger row is what makes drain measurable
    (``mnemo inbox --stats``).

    Reads and records; it never promotes. Fail-silent, like the rest of the
    session-start path.
    """
    try:
        from mnemo.core import inbox as inbox_mod

        pages, waiting = inbox_mod.pick_offers(vault_root, project, cfg=cfg)
        if not pages:
            return ""

        lines = [f"[mnemo staged for review — project={project}, {waiting} waiting]"]
        lines.append(
            "Extraction staged these in shared/_inbox/, where nothing reads them. "
            "The decision is the maintainer's: relay this, do not promote anything "
            "yourself."
        )
        for page in pages:
            # A description is LLM-written text going into the agent's context
            # between a fence it must not be able to close. ``_one_line`` stops
            # a newline from ending the block early; the replacement stops a
            # literal ``[/mnemo …]`` in the prose from doing it on one line,
            # which is the half a line-based frontmatter parser still lets
            # through.
            desc = _one_line(page.description, _DESC_MAX) or _one_line(page.name, _NAME_MAX)
            desc = desc.replace("[/mnemo", "[ /mnemo")
            act = (
                f" · promote: mnemo inbox --promote {page.key}"
                if _KEY_OK.match(page.key) else ""
            )
            verdict = f", {page.gate_label}" if page.gate_label else ""
            lines.append(
                f"• {page.key} — {desc} ({page.age_days()}d, {page.reason}{verdict}){act}"
            )
        if waiting > len(pages):
            lines.append(
                f"({waiting - len(pages)} more — `mnemo inbox` lists them, "
                "`mnemo inbox --drop KEY` discards one)"
            )
        lines.append("[/mnemo staged]")

        for page in pages:
            inbox_mod.record(
                vault_root, event=inbox_mod.OFFERED, key=page.key, project=project,
            )
        return "\n".join(lines)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.staged_offer", exc)
        except Exception:
            pass
        return ""


#: A procedure's command line is transcript text — a value some child typed
#: into a shell — so it goes into the prompt through the same guard as a
#: staged page's description, one line and capped.
_COMMAND_MAX = 110


def _procedures_offer_block(
    vault_root: Path, cfg: dict, repo: str, cwd: str | None, session_id: str | None
) -> str:
    """A procedure children of this repo keep rediscovering, and the act that ends it.

    ``mnemo procedures`` (#392) and its ``doctor`` row are both pulls, and #385
    priced a pull at 5 of 182 children. This is the push, built like #380's:
    bounded by config, silent when there is nothing, and offering only — the
    accept is a command the maintainer types, because it writes a line into a
    file mnemo does not own and every session in that repo then reads.

    Two things this block does that the staged one does not:

    * It never fires inside a dispatch worktree. The offer asks for a decision
      about the repo's ``CLAUDE.md``, and a dispatched child cannot make one —
      it is the party that *paid* for the line, not the party that writes it.
      Without this, a dispatch of eight children would spend the day's single
      offer on one of them and the maintainer would never see it.
    * It reads a cache rather than scanning. The scan is ~1.0 s over the
      transcripts on disk and the hook may not pay it, so
      :func:`_maybe_refresh_procedures` spawns it detached instead. What the
      staleness can get wrong is bought back where it would show: the ledger
      is read live, so a decided candidate is never offered, and ``CLAUDE.md``
      is re-read live, so a line the maintainer wrote by hand silences it too.

    Fail-silent, like the rest of the session-start path.
    """
    try:
        from mnemo.core import procedures as procedures_mod

        if _is_dispatch_worktree(cwd):
            return ""
        candidates, waiting = procedures_mod.pick_offers(vault_root, repo, cfg=cfg)
        if not candidates:
            return ""

        lines = [f"[mnemo procedure candidate — repo={repo}, {waiting} undecided]"]
        lines.append(
            "Dispatched children of this repo worked this out for themselves more "
            "than once and its CLAUDE.md does not say it. The decision is the "
            "maintainer's: relay this, do not edit CLAUDE.md yourself."
        )
        for candidate in candidates:
            # The command is built from values children typed; `_one_line`
            # stops a newline ending the block early and the replacement stops
            # a literal `[/mnemo …]` in one of those values from doing it on
            # one line.
            command = _one_line(candidate.command, _COMMAND_MAX).replace(
                "[/mnemo", "[ /mnemo"
            )
            key = _one_line(candidate.key, _NAME_MAX)
            act = (
                f" · accept: mnemo procedures --accept {key}"
                if _SLUG_OK.match(key) else ""
            )
            paid = len(candidate.rediscovered)
            lines.append(
                f"• {key} — `{command}` ({paid} of {candidate.shape_children} "
                f"children ran it the hard way first){act}"
            )
        if waiting > len(candidates):
            lines.append(
                f"({waiting - len(candidates)} more — `mnemo procedures` lists them, "
                "`mnemo procedures --drop KEY` discards one)"
            )
        lines.append("[/mnemo procedures]")

        for candidate in candidates:
            procedures_mod.record(
                vault_root,
                event=procedures_mod.OFFERED,
                repo=candidate.repo,
                key=candidate.key,
                session_id=session_id,
            )
        return "\n".join(lines)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.procedures_offer", exc)
        except Exception:
            pass
        return ""


def _is_dispatch_worktree(cwd: str | None) -> bool:
    """Is this session a dispatched child? Never raises."""
    try:
        from mnemo.core.dispatch import issue_for_cwd

        return issue_for_cwd(cwd) is not None
    except Exception:  # noqa: BLE001
        return False


def _offer_block(
    vault_root: Path, cfg: dict, project: str, cwd: str | None, session_id: str | None
) -> str:
    """One offer block per session start, alternating between the two queues.

    Two blocks in one prompt is a nag, and the two queues have no shared unit
    to budget in — a staged page and a proposed ``CLAUDE.md`` line are not
    comparable — so the budget is the *slot*: at most one block, whichever
    queue has gone longest without using it. Both ledgers already record when
    a project was last shown a block, so "longest" is a read, not new state,
    and a queue that has never been offered wins outright.

    The loser is not merely postponed, it is skipped: it keeps its own
    interval, so tomorrow it wins the slot and the winner today waits. What
    stops the alternation from wasting the slot is the fallback — a winner
    with nothing to say hands it straight back, so a repo with an empty inbox
    and one candidate is offered the candidate every day it is due, and the
    behaviour before this existed is exactly what a repo with no candidates
    still gets.

    Worst case on the prompt is therefore one block: ~190 tokens for the
    staged one (#380) or ~100–140 for this one — 398 bytes for ``mnemo`` and
    547 for ``mnemo-desktop`` on the maintainer's vault on 2026-09-19,
    ``tools/measure_procedure_offer.py`` — against a briefing that costs
    ~1783 at 90.9% of session starts. Arbitrating costs 0.17 ms more per
    session start than the staged block alone did, measured the same way.
    """
    from mnemo.core import inbox as inbox_mod
    from mnemo.core import procedures as procedures_mod

    def _staged() -> str:
        return _staged_offer_block(vault_root, cfg, project)

    def _procedures() -> str:
        return _procedures_offer_block(vault_root, cfg, project, cwd, session_id)

    builders = [_staged, _procedures]
    try:
        staged_at = inbox_mod.last_block_at(vault_root, project)
        procedures_at = procedures_mod.last_block_at(
            procedures_mod.ledger_rows(Path(vault_root)), project
        )
        # None sorts first: a queue never offered has waited longest.
        if procedures_at is None and staged_at is not None:
            builders.reverse()
        elif (
            procedures_at is not None
            and staged_at is not None
            and procedures_at < staged_at
        ):
            builders.reverse()
    except Exception as exc:  # noqa: BLE001 — the order is not worth a session
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.offer_order", exc)
        except Exception:
            pass

    for builder in builders:
        block = builder()
        if block:
            return block
    return ""


def _maybe_refresh_procedures(cfg: dict, vault_root, cwd: str | None = None) -> None:
    """Spawn the procedure scan detached, at most once per refresh interval.

    The scan reads every dispatch transcript on disk — ~1.0 s over the 184
    there on 2026-09-19 — and the session-start path must not pay that. So it
    is spawned, not run: the block this session emits reads the cache the
    *previous* refresh wrote, and the one this call starts is for tomorrow.
    The first session in a fresh vault therefore offers nothing and starts the
    scan, which is the right way round — nothing is offered before it has been
    measured.

    The marker is stamped before the spawn, so a scan that cannot run (no
    ``~/.claude/projects``, no transcripts, a crash) costs one interval rather
    than a spawn on every session start (#229, #234). Two sessions starting
    within milliseconds of each other can still both spawn; the cost of that
    race is one redundant background second, and the write is atomic.
    """
    try:
        from mnemo.core import procedures as procedures_mod

        if not procedures_mod.scan_is_due(Path(vault_root), cfg):
            return
        procedures_mod.mark_scan(Path(vault_root))
        _spawn_detached(["procedures", "--refresh"], cwd=cwd)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.procedures_refresh", exc)
        except Exception:
            pass


def main() -> int:
    # Nothing at all inside a session mnemo launched for itself (#329): the
    # `claude --print` helpers that brief and extract run under the user's own
    # settings, mnemo's hooks included, so an unguarded hook here schedules the
    # work whose helper is running it. See :mod:`mnemo.core.hook_guard`.
    from mnemo.core.hook_guard import hooks_off, throwaway_session

    if hooks_off():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, mirror, paths, session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        # A session in a temp, pytest or job-scratch dir writes nothing into
        # a vault that outlives it (#420). See hook_guard.throwaway_session.
        if throwaway_session(payload.get("cwd") or os.getcwd(), vault):
            return 0
        if not errors.should_run(vault):
            # The breaker is the one failure a user cannot see from inside a
            # session: every hook goes quiet and mnemo just "stops". Say so
            # once per session start (#115); the other hooks stay silent on
            # purpose — PreToolUse output would read as a denial.
            try:
                _emit_injection("[mnemo] paused: " + errors.remedy_line(vault))
            except Exception:
                pass
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
        # Canonical (#225): this name is cached for session_end (which writes
        # the day's log under it) and used for the session-start log line
        # below. Resolving naively filed both under the worktree's own
        # basename, creating one orphan `bots/<repo>-wt-*/` namespace per
        # dispatched worktree while injection (further down) used the
        # canonical name. `repo_root` stays the tree actually being worked in.
        ainfo = agent.resolve_canonical_agent(cwd)
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
            # #357: the only moment this session's inbox address is knowable.
            # Claude Code exports the socket and token into the session's own
            # environment and nowhere else, and the transcript records neither,
            # so a child that finishes later can only find its parent if the
            # parent wrote itself down here.
            if bool((cfg.get("dispatch") or {}).get("notifyParent", False)):
                from mnemo.core.sessions import inbox

                inbox.record(vault, inbox.address_from_env())
        except Exception as e:
            errors.log_error(vault, "session_start.inbox_address", e)
        try:
            _maybe_prune_briefings(vault, cfg)
        except Exception as e:
            errors.log_error(vault, "session_start.briefings_prune", e)
        try:
            _maybe_repair_hook_matchers(vault, cfg, cwd)
        except Exception as e:
            errors.log_error(vault, "session_start.hook_repair", e)
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            errors.log_error(vault, "session_start.mirror", e)

        # #114: legacy pages carry name: but no slug:, which keyed every index
        # by display name. Stamp once (marker short-circuits the scan on every
        # later start; it is written only when nothing was left unstamped),
        # then the rebuilds below key by slug.
        try:
            from mnemo.core.migrations import slugs as _slugs
            if not _slugs.marker_present(vault):
                rep = _slugs.stamp_slugs(vault)
                if not rep.skipped:
                    _slugs.write_marker(vault)
        except Exception as exc:
            errors.log_error(vault, "session_start.slug_migration", exc)

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

        # The scan behind the procedure offer: detached, at most once per
        # refresh interval, and for the *next* session's block — never this
        # one's (#397). Below `errors.should_run` like the backfill spawn, and
        # silent to a session either way.
        _maybe_refresh_procedures(cfg, vault, cwd)

        source = str(payload.get("source") or "startup")
        if cfg.get("capture", {}).get("sessionStartEnd", True):
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
                reader_sid = sid if sid != "unknown" else None
                inject_briefing = _briefing_wanted(cfg, source)
                payload_text = _build_injection_payload(
                    vault,
                    current_project=canonical_name,
                    inject_briefing=inject_briefing,
                    session_id=reader_sid,
                    source=source,
                )
                # The notice stands on its own: a brand-new vault has no
                # topics and no briefing, so the payload it would ride along
                # with is empty on exactly the session the notice exists for.
                notice = _first_run_notice(vault, cfg, canonical_name)
                if notice:
                    payload_text = (
                        payload_text + "\n\n" + notice if payload_text else notice
                    )
                # What the sweep left waiting, while the vault is otherwise
                # empty. Same standing as the first-run notice: it is the
                # only news on exactly the session it exists for.
                staged_notice = _staged_backfill_notice(vault)
                if staged_notice:
                    payload_text = (
                        payload_text + "\n\n" + staged_notice
                        if payload_text else staged_notice
                    )
                # Rules another contributor published into this repo, still
                # unimported. Same standing again: on a fresh clone with a
                # fresh vault it is the only news there is.
                share_notice = _share_import_notice(vault, cfg, canonical_name, cwd)
                if share_notice:
                    payload_text = (
                        payload_text + "\n\n" + share_notice
                        if payload_text else share_notice
                    )
                # Same rule as the notice: a vault whose only news is a rule it
                # just learned still has news worth sending.
                learned_block = _learned_block(vault, cfg, canonical_name)
                if learned_block:
                    payload_text = (
                        payload_text + "\n\n" + learned_block
                        if payload_text else learned_block
                    )
                # And the offer: one block, from whichever of the two review
                # queues has gone longest without the slot (#397). Last,
                # because it is the block a session can most afford to lose if
                # anything above it grows.
                offer = _offer_block(vault, cfg, canonical_name, cwd, reader_sid)
                if offer:
                    payload_text = (
                        payload_text + "\n\n" + offer
                        if payload_text else offer
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
                            source=source,
                            session_id=reader_sid,
                        )
                    except Exception as exc:
                        errors.log_error(vault, "session_start.inject_telemetry", exc)
            except Exception as e:
                errors.log_error(vault, "session_start.injection", e)

        # #396: make sure something is alive to wake a rate-limited child when
        # its reset comes round. Not a wake: this costs one roster read and at
        # most one detached spawn, because the account limit that stalls the
        # children silences every hook, so the thing that has to survive the
        # window cannot be this one. See :mod:`mnemo.core.sessions.rewake`.
        try:
            from mnemo.core.sessions import rewake
            rewake.on_session_start(cfg, vault_root=vault)
        except Exception as e:
            errors.log_error(vault, "session_start.rewake", e)

        # #436: a watcher following finished children's PRs that died (a
        # reboot, a kill) while follows were still open is started again. A
        # ledger read and a stat; the watcher does the `gh` calls.
        try:
            from mnemo.core.sessions import pr_follow
            pr_follow.on_session_start(cfg, vault_root=vault)
        except Exception as e:
            errors.log_error(vault, "session_start.pr_follow", e)

        # #503: remove the worktrees of dispatched children whose PR merged.
        # Here, not on the child's SessionEnd, which #502 measured missing for
        # 1 child in 4. A ledger read; at most every half hour per repo, one
        # detached `mnemo tree-sweep` does the `git` and `gh` calls.
        try:
            from mnemo.core.sessions import tree_sweep
            tree_sweep.on_session_start(cfg, vault_root=vault, cwd=cwd)
        except Exception as e:
            errors.log_error(vault, "session_start.tree_sweep", e)

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
