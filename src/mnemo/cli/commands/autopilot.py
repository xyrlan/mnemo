"""``mnemo autopilot {on,off,pause,status,propose,preempt,proposals}`` — autopilot control."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.cli.parser import command


def _vault() -> Path:
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    return cli._resolve_vault()


def _cwd() -> Path:
    return Path(os.getcwd())


@command("autopilot")
def cmd_autopilot(args: argparse.Namespace) -> int:
    action = getattr(args, "autopilot_action", None)
    handler = {
        "on": _do_on,
        "off": _do_off,
        "pause": _do_pause,
        "status": _do_status,
        "digest": _do_digest,
        "collect-misses": _do_collect_misses,
        "self-fix": _do_selffix,
        "tune": _do_tune,
        "propose": _do_propose,
        "preempt": _do_preempt,
        "proposals": _do_proposals,
    }.get(action)
    if handler is None:
        print("usage: mnemo autopilot {on,off,pause,status,digest,collect-misses,self-fix,tune,propose,preempt,proposals}")
        return 2
    return handler(args)


def _do_on(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state
    from mnemo.autopilot.core.frozen_recall import freeze_current
    from mnemo.autopilot.core.labels import ensure_label_exists

    vault = _vault()
    set_state(vault_root=vault, state="on", source="cli")
    # bootstrap frozen recall if recall-cases.json exists; ignore otherwise
    try:
        freeze_current(vault_root=vault)
    except FileNotFoundError:
        pass
    ensure_label_exists()
    print("autopilot: on")
    print("(operations fire on Claude Code SessionStart/SessionEnd hooks)")
    return 0


def _do_off(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state

    vault = _vault()
    set_state(vault_root=vault, state="off", source="cli")
    print("autopilot: off")
    return 0


def _do_pause(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state

    vault = _vault()
    hours = max(1, int(getattr(args, "hours", 24) or 24))
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=vault, state="paused", paused_until=until, source="cli")
    print(f"autopilot: paused for {hours}h (until {until})")
    return 0


def _do_selffix(args: argparse.Namespace) -> int:
    from mnemo.cli.commands.selffix import cmd_selffix
    return cmd_selffix(args)


def _do_tune(args: argparse.Namespace) -> int:
    target = getattr(args, "tune_target", None)
    if target is None:
        print("usage: mnemo autopilot tune {bm25,reflex,all} [--dry-run]")
        return 2
    dry_run = getattr(args, "dry_run", False)
    project = getattr(args, "project", None)

    if target in ("bm25", "all"):
        _tune_bm25(vault=_vault(), dry_run=dry_run)
    if target in ("reflex", "all"):
        _tune_reflex(vault=_vault(), dry_run=dry_run, project=project)
    return 0


def _tune_bm25(vault: Path, dry_run: bool) -> None:
    from mnemo.autopilot.tuner.bm25_tuner import (
        grid_search,
        open_bm25_tune_pr,
        meets_acceptance,
        DEFAULT_BM25_CONFIG,
    )
    from mnemo.autopilot.tuner._scorer import score_config, Case
    from mnemo.autopilot.core.frozen_recall import FrozenSetMissing
    import json as _json

    try:
        from mnemo.autopilot.core.frozen_recall import load_frozen
        fh = load_frozen(vault_root=vault)
        with fh:
            raw = _json.load(fh)
        cases_data = raw if isinstance(raw, list) else raw.get("cases", [])
        cases = [
            Case(id=c.get("id", ""), project=c.get("project", ""),
                 topic=c.get("topic", ""), expect_slug=c.get("expect_slug", ""))
            for c in cases_data if isinstance(c, dict) and c.get("expect_slug")
        ]
    except FrozenSetMissing:
        print("[bm25-tuner] no frozen recall set — skipping (run `mnemo autopilot on` first)")
        return

    if not cases:
        print("[bm25-tuner] frozen set is empty — skipping")
        return

    def _factory(project: str, query_tokens: list) -> dict:
        try:
            from mnemo.core.reflex.index import load_index
            idx = load_index(vault)
            if idx is None:
                return {"doc_count": 0, "docs": {}, "postings": {}, "avg_field_length": {}}
            return idx
        except Exception:
            return {"doc_count": 0, "docs": {}, "postings": {}, "avg_field_length": {}}

    print("[bm25-tuner] Running grid search (this may take a moment)…")
    best = grid_search(vault_root=vault, index_factory=_factory)

    if best is None:
        print("[bm25-tuner] No config improved on baseline — no proposal.")
        return

    # Score before/after for the PR call
    before = score_config(DEFAULT_BM25_CONFIG, cases=cases, index_factory=_factory)
    after = score_config(best, cases=cases, index_factory=_factory)

    open_bm25_tune_pr(best, before, after, vault_root=vault, dry_run=dry_run)


def _tune_reflex(vault: Path, dry_run: bool, project: str | None) -> None:
    from mnemo.autopilot.tuner.reflex_calibrator import (
        analyze_reflex_log,
        calibrate_thresholds,
        open_reflex_calibration_pr,
    )

    stats_map = analyze_reflex_log(vault_root=vault, project=project)
    if not stats_map:
        print("[reflex-calibrator] no reflex log data — skipping")
        return

    per_project = {}
    for proj, stats in stats_map.items():
        cfg = calibrate_thresholds(stats)
        if cfg is None:
            print(f"[reflex-calibrator] {proj}: insufficient data (< 100 prompts) — skipping")
        per_project[proj] = cfg

    open_reflex_calibration_pr(per_project, vault_root=vault, dry_run=dry_run)


def _do_status(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import get_state, is_active
    from mnemo.autopilot.core.scheduler import status_summary
    from mnemo.autopilot.core._dirs import autopilot_budget_path
    import json

    vault = _vault()
    state = get_state(vault_root=vault)
    active = is_active(vault_root=vault)
    print(f"State: {state} ({'active' if active else 'inactive'})")

    bp = autopilot_budget_path(vault)
    if bp.exists():
        data = json.loads(bp.read_text())
        print(f"Budget window start: {data.get('window_start')}")
        counts = data.get("counts", {})
        if counts:
            print("Counts today:")
            for k, v in sorted(counts.items()):
                print(f"  {k}: {v}")
        else:
            print("Counts today: (none)")
        recent = data.get("recent_outcomes", [])
        if recent:
            print("Recent outcomes:")
            for o in recent[-5:]:
                print(f"  PR #{o['pr']}: {o['outcome']} @ {o['ts']}")
    else:
        print("Budget: (no activity yet)")

    print("Hook-driven operations:")
    for op in status_summary(vault_root=vault):
        last = op["last_run_at"] or "never"
        due = "DUE" if op["due"] else "ok"
        print(f"  {op['name']:30s}  every {op['interval_days']}d  last={last}  [{due}]")
    return 0


def _parse_since_days(since_str: str) -> int:
    """Parse a ``<N>d`` string into an integer number of days (default 7)."""
    since_str = (since_str or "7d").strip().lower()
    if since_str.endswith("d"):
        try:
            return max(1, int(since_str[:-1]))
        except ValueError:
            pass
    try:
        return max(1, int(since_str))
    except ValueError:
        return 7


def _do_digest(args: argparse.Namespace) -> int:
    from mnemo.autopilot.insights.digest import (
        generate_digest,
        write_digest,
        post_digest_issue,
    )

    vault = _vault()
    since_days = _parse_since_days(getattr(args, "since", "7d") or "7d")
    digest = generate_digest(vault_root=vault, since_days=since_days)
    path = write_digest(vault_root=vault, digest=digest)
    print(str(path))

    if getattr(args, "post", False):
        issue_num = post_digest_issue(digest=digest)
        if issue_num is not None:
            print(f"issue created: #{issue_num}")
        else:
            print("issue: (not created — gh unavailable or error)")

    return 0


def _do_collect_misses(args: argparse.Namespace) -> int:
    from mnemo.autopilot.insights.miss_collector import collect_recall_misses

    vault = _vault()
    count = collect_recall_misses(vault_root=vault)
    print(f"{count} new proposal(s) written")
    return 0


def _do_propose(args: argparse.Namespace) -> int:
    """Run end-of-session rule analysis for a given session ID."""
    from mnemo.autopilot.proposer.eos_extractor import analyze_session
    from mnemo.core import config, paths, agent as agent_mod

    session_id = getattr(args, "session_id", None) or "unknown"
    vault = _vault()
    cwd = _cwd()

    try:
        cfg = config.load_config()
        project = agent_mod.resolve_canonical_agent(str(cwd)).name
    except Exception:
        project = "unknown"

    candidates = analyze_session(
        session_id=session_id,
        project=project,
        vault_root=vault,
        cwd=cwd,
    )
    print(f"propose: {len(candidates)} candidate(s) for session {session_id!r}")
    for c in candidates:
        print(f"  [{c.confidence:.2f}] {c.slug_hint}: {c.title}")
    return 0


def _do_preempt(args: argparse.Namespace) -> int:
    """Run pre-emptive rule prediction and write preempt-cache.json."""
    from mnemo.autopilot.proposer._hooks import run_preempt_sync
    from mnemo.core import agent as agent_mod

    vault = _vault()
    cwd = _cwd()

    try:
        project = agent_mod.resolve_canonical_agent(str(cwd)).name
    except Exception:
        project = "unknown"

    slugs = run_preempt_sync(vault_root=vault, project=project, cwd=str(cwd))
    print(f"preempt: {len(slugs)} predicted slug(s) written to .mnemo/preempt-cache.json")
    for s in slugs:
        print(f"  {s}")
    return 0


def _do_proposals(args: argparse.Namespace) -> int:
    """Dispatch to proposals subcommands: list or review."""
    sub_action = getattr(args, "proposals_action", None)
    handler = {
        "list": _do_proposals_list,
        "review": _do_proposals_review,
    }.get(sub_action)
    if handler is None:
        print("usage: mnemo autopilot proposals {list,review}")
        return 2
    return handler(args)


def _do_proposals_list(args: argparse.Namespace) -> int:
    """List proposals from the queue with optional filters."""
    from mnemo.autopilot.core.proposals import list_proposals

    vault = _vault()
    status_filter = getattr(args, "status", None)
    kind_filter = getattr(args, "kind", None)
    project_filter = getattr(args, "project", None)

    proposals = list_proposals(
        vault_root=vault,
        status=status_filter,
        kind=kind_filter,
        project=project_filter,
    )
    if not proposals:
        print("proposals: (none)")
        return 0
    print(f"{'ID':<36}  {'KIND':<16}  {'CONF':>4}  {'STATUS':<10}  SOURCE")
    print("-" * 80)
    for p in proposals:
        short_id = p.id[-20:] if len(p.id) > 20 else p.id
        print(f"{short_id:<36}  {p.kind:<16}  {p.confidence:4.2f}  {p.status:<10}  {p.source}")
    print(f"\n{len(proposals)} proposal(s)")
    return 0


def _do_proposals_review(args: argparse.Namespace) -> int:
    """Show a proposal and accept/reject it (interactive or via flags)."""
    from mnemo.autopilot.core.proposals import list_proposals, update_status
    import json as _json

    vault = _vault()
    proposal_id = getattr(args, "id", None)
    accept = getattr(args, "accept", False)
    reject = getattr(args, "reject", False)

    if proposal_id:
        # Find the specific proposal
        all_p = list_proposals(vault_root=vault)
        matches = [p for p in all_p if p.id == proposal_id or p.id.endswith(proposal_id)]
        if not matches:
            print(f"proposal {proposal_id!r} not found")
            return 1
        proposals = matches[:1]
    else:
        proposals = list_proposals(vault_root=vault, status="pending")
        if not proposals:
            print("no pending proposals to review")
            return 0
        proposals = proposals[:1]

    p = proposals[0]
    print(f"ID:         {p.id}")
    print(f"Kind:       {p.kind}")
    print(f"Source:     {p.source}")
    print(f"Project:    {p.project or '(none)'}")
    print(f"Confidence: {p.confidence:.2f}")
    print(f"Status:     {p.status}")
    print(f"Created:    {p.created_at}")
    print("Payload:")
    print(_json.dumps(p.payload, indent=2))
    print()

    if accept:
        update_status(vault_root=vault, proposal_id=p.id, status="accepted")
        print("accepted.")
        return 0
    if reject:
        update_status(vault_root=vault, proposal_id=p.id, status="rejected")
        print("rejected.")
        return 0

    # Interactive
    try:
        choice = input("Accept, Reject, or Skip? [a/r/s] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nskipped.")
        return 0

    if choice == "a":
        update_status(vault_root=vault, proposal_id=p.id, status="accepted")
        print("accepted.")
    elif choice == "r":
        update_status(vault_root=vault, proposal_id=p.id, status="rejected")
        print("rejected.")
    else:
        print("skipped.")
    return 0
