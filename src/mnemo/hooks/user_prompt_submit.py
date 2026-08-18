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
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core import errors, paths
        from mnemo.core.agent import resolve_canonical_agent
        from mnemo.core.mcp import session_state
        from mnemo.core.reflex import bm25, gates
        from mnemo.core.reflex.index import load_index
        from mnemo.core.reflex.tokenizer import tokenize_query

        cfg = cfg_mod.load_config()
        reflex_cfg = cfg.get("reflex") or {}
        if not bool(reflex_cfg.get("enabled", False)):
            return 0

        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0

        cwd = payload.get("cwd") or str(Path.cwd())
        project = resolve_canonical_agent(cwd).name
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

        # Pre-gate: min 3 distinct non-stopword tokens.
        thresholds = (reflex_cfg.get("thresholds") or {})
        min_tokens = int(thresholds.get("minQueryTokens", 3))
        q_tokens = tokenize_query(prompt_raw)
        if len(set(q_tokens)) < min_tokens:
            _log_silence(vault, sid, project, prompt_raw, reason="below_min_tokens")
            return 0

        index = load_index(vault)
        if index is None:
            _log_silence(vault, sid, project, prompt_raw, reason="index_missing")
            return 0

        # Candidate slugs — project scope (local + universal).
        candidates = _candidates_for_project(index, project)
        if not candidates:
            _log_silence(vault, sid, project, prompt_raw, reason="index_missing")
            return 0

        # Score
        weights = (reflex_cfg.get("bm25f") or {}).get("fieldWeights") or bm25.DEFAULT_WEIGHTS
        params = reflex_cfg.get("bm25f") or bm25.DEFAULT_PARAMS
        scores = bm25.score_docs(index, query_tokens=q_tokens,
                                 candidate_slugs=candidates,
                                 weights=weights, params=params)

        # Triple-gate. Per-project calibration (written by the autopilot's
        # reflex_calibrator) wins over global config, per key.
        from mnemo.core.reflex.project_config import load_project_thresholds

        overrides = load_project_thresholds(vault, project)
        doc_tokens_by_slug = _doc_token_sets(index, [slug for slug, _ in scores[:2]])
        gate_thresholds = {
            "term_overlap_min": int(overrides.get(
                "term_overlap_min", thresholds.get("termOverlapMin", 2))),
            "relative_gap": float(overrides.get(
                "relative_gap", thresholds.get("relativeGap", 1.5))),
            "absolute_floor": float(overrides.get(
                "absolute_floor", thresholds.get("absoluteFloor", 2.0))),
        }
        result = gates.evaluate_gates(
            scores,
            query_tokens=q_tokens,
            doc_tokens_by_slug=doc_tokens_by_slug,
            thresholds=gate_thresholds,
        )
        # The receipt: the ranking and the numbers this decision was made on.
        # Everything past this point had rules scored, so a silence here can be
        # explained rather than merely named — see `core.reflex.receipts`.
        receipt = _receipt(scores)
        if not result.accepted_slugs:
            _log_silence(vault, sid, project, prompt_raw,
                         reason=result.silence_reason or "index_missing",
                         candidates=receipt, thresholds=gate_thresholds)
            return 0

        # Dedupe against injected_cache (day-lifetime)
        cache = session_state.read_injected_cache(vault)
        survivors = [s for s in result.accepted_slugs if s not in cache]
        if not survivors:
            _log_silence(vault, sid, project, prompt_raw, reason="deduped",
                         candidates=receipt, thresholds=gate_thresholds)
            return 0

        _emit_reflex_context(index, survivors)
        for slug in survivors:
            session_state.add_injection(vault, slug=slug, sid=sid, now_ts=now_ts)
            session_state.bump_emission(vault, sid=sid, kind="reflex", now_ts=now_ts)

        score_map = dict(scores)
        _log_emission(vault, sid, project, prompt_raw, survivors,
                      scores=[score_map.get(s, 0.0) for s in survivors],
                      candidates=receipt, thresholds=gate_thresholds)
    except Exception as exc:  # noqa: BLE001 — hook must never propagate
        try:
            from mnemo.core import config as _cfg, errors as _err, paths as _paths
            _err.log_error(_paths.vault_root(_cfg.load_config()), "user_prompt_submit.outer", exc)
        except Exception:
            pass
    return 0


def _candidates_for_project(index: dict, project: str) -> list[str]:
    docs = index.get("docs") or {}
    return [
        slug for slug, doc in docs.items()
        if project in (doc.get("projects") or []) or doc.get("universal")
    ]


def _doc_token_sets(index: dict, slugs: list[str]) -> dict[str, set[str]]:
    """Rebuild per-doc token UNION (across all 4 fields) for triple-gate overlap check."""
    out: dict[str, set[str]] = {s: set() for s in slugs}
    target = set(slugs)
    for term, entries in (index.get("postings") or {}).items():
        for entry in entries:
            if entry["slug"] in target:
                out[entry["slug"]].add(term)
    return out


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
                 thresholds: dict | None = None) -> None:
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
    _record_log(vault_root, entry)


def _log_emission(vault_root, sid: str, project: str, prompt: str,
                  emitted: list[str], *, scores: list[float],
                  candidates: list | None = None,
                  thresholds: dict | None = None) -> None:
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
