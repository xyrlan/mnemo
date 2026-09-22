"""UserPromptSubmit hook — Prompt Reflex.

Fail-open absolute: any exception returns exit 0 with empty stdout. The
hook runs on every prompt; a regression here would stall every Claude
turn. Follow the defensive patterns from pre_tool_use.py / session_start.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


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
    if not isinstance(payload, dict):
        return 0

    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core import errors, paths
        from mnemo.core.agent import resolve_agent, resolve_canonical_agent
        from mnemo.core.mcp import session_state
        from mnemo.core.reflex.decide import decide
        from mnemo.core.reflex.index import load_index
        from mnemo.core.reflex.project_config import load_project_thresholds

        cfg = cfg_mod.load_config()
        reflex_cfg = cfg.get("reflex") or {}
        if not bool(reflex_cfg.get("enabled", False)):
            return 0

        vault = paths.vault_root(cfg)
        # A session in a temp, pytest or job-scratch dir writes nothing into
        # a vault that outlives it (#420). See hook_guard.throwaway_session.
        if throwaway_session(payload.get("cwd") or str(Path.cwd()), vault):
            return 0
        if not errors.should_run(vault):
            return 0

        cwd = payload.get("cwd") or str(Path.cwd())
        project = resolve_canonical_agent(cwd).name
        tree_root = resolve_agent(cwd).repo_root
        sid = str(payload.get("session_id") or "unknown")
        prompt_raw = str(payload.get("prompt") or payload.get("user_message") or "")

        now_ts = int(time.time())

        # Session GC + cap check
        session_state.gc_old_sessions(vault, now_ts=now_ts)
        emissions = session_state.read_emission_counts(vault, sid)
        max_per = int(reflex_cfg.get("maxEmissionsPerSession", 10))
        if emissions["reflex_count"] >= max_per:
            _log_silence(vault, sid, project, prompt_raw, reason="session_cap_reached")
            return 0

        # Rank + triple-gate. The decision itself is pure and shared with
        # `mnemo replay` (core.reflex.decide) so a replay measures this hook
        # and not a copy of it. Per-project calibration (written by the
        # autopilot's reflex_calibrator) wins over global config, per key.
        # The index is passed as a loader so a prompt the token pre-gate
        # rejects never pays for reading it.
        overrides = load_project_thresholds(vault, project)
        decision = decide(lambda: load_index(vault), project=project,
                          prompt=prompt_raw, reflex_cfg=reflex_cfg,
                          overrides=overrides)
        # The receipt: the ranking and the numbers this decision was made on.
        # Both are empty when the decision was made before ranking ran
        # (`below_min_tokens`, `index_missing`), and `_log_silence` omits the
        # keys — a silence there is "retrieval never ran", not "found nothing".
        # See `core.reflex.receipts`.
        receipt = _receipt(decision.scores)
        gate_thresholds = decision.thresholds

        # #412: with `reflex.judge` on, a judge that reads the (prompt, rule)
        # pair replaces the accept step — the pool is the top of the *ranking*,
        # including the prompts the gates silenced, and the judge alone
        # decides. Every failure returns None and the shipped path below runs
        # exactly as it does today. `judge_row` is the log's receipt for the
        # stage and is written on whichever branch ends up answering.
        survivors: list | None = None
        judge_row = None
        exported: list = []
        if decision.scores and _judge_configured(reflex_cfg):
            from mnemo.core.reflex import judge as judge_stage

            chosen = judge_stage.settings(cfg)
            if chosen["provider"] != "none":
                from mnemo.core.export.manifest import exported_slugs_for

                pool = [slug for slug, _score in decision.scores[:chosen["candidates"]]]
                exported = sorted(exported_slugs_for(vault, project, repo_root=tree_root)
                                  & set(pool))
                pool = [s for s in pool if s not in exported]
                if not pool:
                    _log_silence(vault, sid, project, prompt_raw, reason="all_exported",
                                 candidates=receipt, thresholds=gate_thresholds,
                                 exported=exported)
                    return 0
                cache = session_state.read_injected_cache(vault, sid)
                pool = [s for s in pool if s not in cache]
                if not pool:
                    _log_silence(vault, sid, project, prompt_raw, reason="deduped",
                                 candidates=receipt, thresholds=gate_thresholds,
                                 exported=exported)
                    return 0
                picks, judge_row = judge_stage.ask(
                    vault, prompt=prompt_raw, slugs=pool, chosen_settings=chosen,
                    project=project)
                if picks is not None:
                    if not picks:
                        _log_silence(vault, sid, project, prompt_raw,
                                     reason=judge_stage.SILENCE_REASON,
                                     candidates=receipt, thresholds=gate_thresholds,
                                     exported=exported, judge=judge_row)
                        return 0
                    survivors = picks

        if survivors is None:
            if not decision.accepted:
                _log_silence(vault, sid, project, prompt_raw,
                             reason=decision.silence_reason or "index_missing",
                             candidates=receipt, thresholds=gate_thresholds,
                             judge=judge_row)
                return 0

            # Rules already exported into this tree's rules file are loaded by
            # Claude Code itself; injecting them again is a repeat. Checked only
            # against what the gates actually accepted — export suppresses
            # output, it does not re-rank input (subtracting before scoring
            # could let a weaker rule win a comparison the un-exported vault
            # would have refused).
            from mnemo.core.export.manifest import exported_slugs_for

            exported = sorted(exported_slugs_for(vault, project, repo_root=tree_root)
                              & set(decision.accepted))
            accepted = [s for s in decision.accepted if s not in exported]
            if not accepted:
                _log_silence(vault, sid, project, prompt_raw, reason="all_exported",
                             candidates=receipt, thresholds=gate_thresholds,
                             exported=exported, judge=judge_row)
                return 0

            # Dedupe against what this session was already told today (#361)
            cache = session_state.read_injected_cache(vault, sid)
            survivors = [s for s in accepted if s not in cache]
            if not survivors:
                _log_silence(vault, sid, project, prompt_raw, reason="deduped",
                             candidates=receipt, thresholds=gate_thresholds,
                             exported=exported, judge=judge_row)
                return 0

        _emit_reflex_context(decision.index, survivors)
        for slug in survivors:
            session_state.add_injection(vault, slug=slug, sid=sid, now_ts=now_ts)
            session_state.bump_emission(vault, sid=sid, kind="reflex", now_ts=now_ts)

        score_map = dict(decision.scores)
        _log_emission(vault, sid, project, prompt_raw, survivors,
                      scores=[score_map.get(s, 0.0) for s in survivors],
                      candidates=receipt, thresholds=gate_thresholds,
                      exported=exported, judge=judge_row)
    except Exception as exc:  # noqa: BLE001 — hook must never propagate
        try:
            from mnemo.core import config as _cfg, errors as _err, paths as _paths
            _err.log_error(_paths.vault_root(_cfg.load_config()), "user_prompt_submit.outer", exc)
        except Exception:
            pass
    return 0


def _judge_configured(reflex_cfg: dict) -> bool:
    """Is `reflex.judge` on? Read straight off the config, on purpose.

    This is :func:`mnemo.core.reflex.judge.enabled` spelled out here so that a
    vault with the stage off — the default — never imports that module, and
    with it :mod:`ssl` and :mod:`urllib.request`, on a hook that runs before
    every prompt. ``tests/unit/test_reflex_judge.py`` pins the two answers
    equal over every shape of the block.
    """
    provider = ((reflex_cfg or {}).get("judge") or {}).get("provider")
    return bool(provider) and str(provider).lower() != "none"


def _emit_reflex_context(index: dict, slugs: list[str]) -> None:
    lines = ["mnemo reflex context:"]
    docs = index.get("docs") or {}
    for slug in slugs:
        preview = (docs.get(slug) or {}).get("preview", "")
        preview_line = preview.replace("\n", " ").strip()
        lines.append(f"• [[{slug}]]: {preview_line} (call read_mnemo_rule if you need the full file).")
    text = "\n".join(lines)
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        },
    }))
    sys.stdout.flush()


def _prompt_hash(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest[:12]}"


# How much of the ranking a receipt keeps. The log rotates at 1MB and four
# other consumers read it (the calibrator, the digest, the dead-rule sweep,
# `mnemo why`), so every byte added per prompt shortens the window all of them
# see. Three is what the explanations need: the winner, the runner-up the
# relative gate is measured against, and one line of context under them.
_RECEIPT_DEPTH = 3


def _receipt(scores: list[tuple[str, float]]) -> list[list]:
    """The top of the ranking, JSON-shaped and rounded.

    Deliberately a *separate* key from ``emitted``: ``dead_rule_sweep`` reads
    ``emitted`` as proof a rule is still alive, so writing near-misses there
    would keep dead rules alive forever on the strength of never firing.
    """
    return [[slug, round(float(score), 4)] for slug, score in scores[:_RECEIPT_DEPTH]]


def _log_silence(vault_root, sid: str, project: str, prompt: str, *, reason: str,
                 candidates: list | None = None,
                 thresholds: dict | None = None,
                 exported: list | None = None,
                 judge: dict | None = None) -> None:
    try:
        from mnemo.core.reflex.tokenizer import tokenize_query as _tq
        prompt_tokens_len = len(set(_tq(prompt)))
    except Exception:
        prompt_tokens_len = 0
    entry = {
        "session_id": sid,
        "project": project,
        "prompt_hash": _prompt_hash(prompt),
        "prompt_tokens": prompt_tokens_len,
        "emitted": [],
        "scores": [],
        "silence_reason": reason,
    }
    # Omitted, not empty, when nothing was scored — `below_min_tokens` and
    # `index_missing` fire before ranking, and an empty list there would read
    # as "retrieval looked and found nothing" rather than "retrieval never ran".
    if candidates:
        entry["candidates"] = candidates
    if thresholds:
        entry["thresholds"] = thresholds
    if exported:
        entry["exported"] = exported
    # Only when the judge stage ran (#412). With `reflex.judge` off — the
    # default — a row here is byte-identical to the one this hook wrote before
    # the stage existed, which `test_hook_user_prompt_submit.py` pins.
    if judge:
        entry["judge"] = judge
    _record_log(vault_root, entry)


def _log_emission(vault_root, sid: str, project: str, prompt: str,
                  emitted: list[str], *, scores: list[float],
                  candidates: list | None = None,
                  thresholds: dict | None = None,
                  exported: list | None = None,
                  judge: dict | None = None) -> None:
    entry = {
        "session_id": sid,
        "project": project,
        "prompt_hash": _prompt_hash(prompt),
        "emitted": emitted,
        "scores": scores,
        "silence_reason": None,
    }
    if candidates:
        entry["candidates"] = candidates
    # With per-project calibration in play, "which thresholds admitted this"
    # varies by project — record them on emissions too, not just silences.
    if thresholds:
        entry["thresholds"] = thresholds
    if exported:
        entry["exported"] = exported
    # Only when the judge stage ran (#412). With `reflex.judge` off — the
    # default — a row here is byte-identical to the one this hook wrote before
    # the stage existed, which `test_hook_user_prompt_submit.py` pins.
    if judge:
        entry["judge"] = judge
    _record_log(vault_root, entry)


def _record_log(vault_root, entry: dict) -> None:
    try:
        from datetime import datetime, timezone
        from mnemo.core.log_utils import rotate_if_needed
        entry.setdefault("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        log_path = Path(vault_root) / ".mnemo" / "reflex-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(log_path, 1_048_576)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
    except Exception:
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
