"""``tools/measure_hook_durations.py`` over synthetic logs and transcripts (#593).

The judge count is the number #593 was filed on (7 ``ok`` rows over the
2,500 ms wall), and the per-hook p99s are what the timeouts in ``hooks.json``
were chosen from, so the arithmetic behind both is pinned here. Everything is
built in ``tmp_path``: a vault with a reflex log and its rotation, and a
projects tree holding one transcript per case.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_hook_durations.py"
_spec = importlib.util.spec_from_file_location("measure_hook_durations", _TOOL)
mhd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mhd)


def _judge_row(ms, status="ok", ts="2026-10-01T00:00:00Z"):
    return {"ts": ts, "judge": {"status": status, "asked": 3, "injected": 0, "ms": ms,
                                "scores": []}}


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _attachment(command, ms, *, kind="hook_success", event="SessionStart",
                timed_out=None, timeout_ms=None, ts="2026-10-01T00:00:00.000Z"):
    att = {"type": kind, "hookEvent": event, "command": command, "durationMs": ms}
    if timed_out is not None:
        att["timedOut"] = timed_out
        att["timeoutMs"] = timeout_ms
    return {"type": "attachment", "timestamp": ts, "attachment": att}


# --- the judge count ----------------------------------------------------------

def test_only_ok_rows_over_the_wall_are_counted():
    rows = [_judge_row(ms) for ms in (400, 2500, 2501, 3517)]
    rows += [_judge_row(2600, status="timeout"), {"ts": "x", "no": "judge"}]
    report = mhd.judge_rows(rows, wall_ms=2500)
    # 2500 is at the wall, not over it; the timeout row is not an ``ok`` row.
    assert report["over_wall"] == 2
    assert report["over_wall_ms"] == [2501.0, 3517.0]
    assert report["ok"]["n"] == 4 and report["ok"]["max"] == 3517.0
    assert report["by_status"] == {"ok": 4, "timeout": 1}


def test_the_judge_count_reads_the_rotated_log_too(tmp_path):
    log = tmp_path / "vault" / ".mnemo" / "reflex-log.jsonl"
    _write_jsonl(log, [_judge_row(3000)])
    _write_jsonl(Path(str(log) + ".1"), [_judge_row(2800), _judge_row(100)])
    report = mhd.measure(str(tmp_path / "vault"), str(tmp_path / "projects"))
    assert report["judge"]["over_wall"] == 2
    assert report["judge"]["ok"]["n"] == 3


def test_days_drops_older_judge_rows():
    rows = [_judge_row(3000, ts="2020-01-01T00:00:00Z"), _judge_row(3000)]
    assert mhd.judge_rows(rows, since="2026-01-01T00:00:00")["over_wall"] == 1


def test_percentiles_are_nearest_rank_like_mnemo_rerank():
    report = mhd.judge_rows([_judge_row(ms) for ms in range(1, 101)])
    assert report["ok"]["p50"] == 51.0 and report["ok"]["p99"] == 99.0


# --- which hooks are mnemo's --------------------------------------------------

def test_both_install_forms_name_their_hook():
    assert mhd.mnemo_hook("/usr/bin/python3 -m mnemo.hooks.session_start") == "session_start"
    assert mhd.mnemo_hook('"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd" hook pre_tool_use') == "pre_tool_use"
    assert mhd.mnemo_hook("/opt/mnemo hook session_end") == "session_end"


def test_the_desktop_hook_and_strangers_are_not_mnemos():
    assert mhd.mnemo_hook("'/Users/you/.mnemo-desktop/bin/mnemo-desktop-hook' "
                          "SessionStart 2>/dev/null || true") is None
    assert mhd.mnemo_hook("Loading caveman mode...") is None
    assert mhd.mnemo_hook(None) is None


# --- hook durations -----------------------------------------------------------

def test_a_cancelled_run_is_kept_out_of_the_completed_spread(tmp_path):
    projects = tmp_path / "projects"
    cmd_start = "/usr/bin/python3 -m mnemo.hooks.session_start"
    cmd_prompt = "/usr/bin/python3 -m mnemo.hooks.user_prompt_submit"
    _write_jsonl(projects / "-p" / "a.jsonl", [
        _attachment(cmd_start, 1000),
        _attachment(cmd_start, 3000),
        _attachment(cmd_start, 600000, kind="hook_cancelled", timed_out=True,
                    timeout_ms=600000),
        _attachment(cmd_prompt, 31000, kind="hook_cancelled", event="UserPromptSubmit",
                    timed_out=True, timeout_ms=30000),
        _attachment(cmd_prompt, 900, kind="hook_cancelled", event="UserPromptSubmit"),
        _attachment("'/x/.mnemo-desktop/bin/mnemo-desktop-hook' SessionStart", 7000),
    ])
    _write_jsonl(projects / "-p" / "s" / "subagents" / "b.jsonl",
                 [_attachment(cmd_start, 2000)])
    report = mhd.measure(str(tmp_path / "vault"), str(projects))
    start = report["hooks"]["session_start"]
    assert start["completed"]["n"] == 3 and start["completed"]["max"] == 3000.0
    assert start["all"]["n"] == 4
    assert start["timed_out"] == 1 and start["timeouts_ms"] == {"600000": 1}
    prompt = report["hooks"]["user_prompt_submit"]
    # A cancellation with no ``timedOut`` (the user interrupting) is a
    # cancellation, not a timeout.
    assert prompt["completed"]["n"] == 0
    assert prompt["cancelled"] == 2 and prompt["timed_out"] == 1
    assert report["hooks"]["session_end"]["completed"]["n"] == 0
    assert "SessionEnd" in mhd.render(report)


def test_days_drops_older_hook_rows():
    cmd = "/usr/bin/python3 -m mnemo.hooks.pre_tool_use"
    rows = [_attachment(cmd, 50, ts="2020-01-01T00:00:00Z"), _attachment(cmd, 70)]
    out = mhd.hook_durations(rows, since="2026-01-01T00:00:00")
    assert out["pre_tool_use"]["completed"]["n"] == 1


# --- timed runs ---------------------------------------------------------------

def test_a_timed_run_measures_the_whole_process(tmp_path):
    ms = mhd.time_command([sys.executable, "-c", "import sys; sys.stdin.read()"],
                          stdin="{}", env={}, cwd=str(tmp_path))
    assert ms > 0


def test_the_scratch_clone_points_the_config_at_itself_and_turns_the_judge_off(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / "mnemo.config.json").write_text(json.dumps(
        {"vaultRoot": str(vault), "reflex": {"judge": {"provider": "typesafe"}}}),
        encoding="utf-8")
    env = mhd.prepare_scratch(str(vault), str(tmp_path / "scratch"))
    cfg = json.loads(Path(env["MNEMO_CONFIG_PATH"]).read_text(encoding="utf-8"))
    assert cfg["vaultRoot"] == str(tmp_path / "scratch" / "vault")
    assert cfg["reflex"]["judge"]["provider"] == "none"
    assert env["TMPDIR"].startswith(str(tmp_path / "scratch"))
    assert "MNEMO_HOOKS_OFF" not in env
    # The real vault's config is untouched.
    original = json.loads((vault / "mnemo.config.json").read_text(encoding="utf-8"))
    assert original["vaultRoot"] == str(vault)
    assert original["reflex"]["judge"]["provider"] == "typesafe"


def test_a_timed_hook_imports_the_mnemo_this_tool_imported(tmp_path):
    """#610: a relative PYTHONPATH=src resolved in --cwd, so a run from a
    worktree against the main checkout timed the main checkout's code."""
    import mnemo

    vault = tmp_path / "vault"
    vault.mkdir()
    env = mhd.prepare_scratch(str(vault), str(tmp_path / "scratch"))
    assert os.path.isabs(env["PYTHONPATH"])
    root = os.path.realpath(env["PYTHONPATH"])
    here = os.path.realpath(mnemo.__file__)
    assert os.path.commonpath([root, here]) == root
    # and the clone never writes the real settings.json
    cfg = json.loads(Path(env["MNEMO_CONFIG_PATH"]).read_text(encoding="utf-8"))
    assert cfg["install"]["autoRepairHooks"] is False
    assert env[mhd.PHASES_ENV]


def test_the_phase_breakdown_names_the_p50_and_p95_runs(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    rows = [{"session_id": "s%d" % i, "total_ms": float(t),
             "phases": {"config": 1.0, "reflex_index": float(t) - 1}}
            for i, t in enumerate([100, 300, 200, 900, 400])]
    rows.append({"session_id": "other", "total_ms": 5.0, "phases": {}})
    (vault / mhd.PHASES_LOG).write_text(
        "".join(json.dumps(r) + "\n" for r in rows) + "not json\n", encoding="utf-8")
    picked = mhd.phase_rows(str(vault), ["s0", "s1", "s2", "s3", "s4"])
    assert len(picked) == 5
    out = mhd.phase_breakdown(picked)
    assert out["p50_run"]["total_ms"] == 300.0
    assert out["p95_run"]["total_ms"] == 900.0
    assert out["per_phase"]["reflex_index"]["max"] == 899.0
    assert "reflex_index" in mhd.render_phases(out)
    assert mhd.phase_breakdown([]) == {}
