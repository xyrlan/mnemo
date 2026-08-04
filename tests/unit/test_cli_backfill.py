"""backfill CLI: dry-run spends nothing, caps are honoured, ledger advances.

The load-bearing test in here is
``test_environmental_failure_never_poisons_the_ledger``: an environment-level
failure (no ``claude`` on PATH, expired auth, rate limit) used to charge an
attempt against every transcript in the sweep, so three fruitless runs
permanently abandoned the user's entire history.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mnemo.core import llm
from mnemo.core.backfill import discover, ledger
from mnemo.cli.commands import backfill as cmd


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"enabled": True, "installCap": 2, "minFileMutations": 1,
                     "autoOnFirstSession": True},
    }
    monkeypatch.setattr(cmd.cfg_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cmd, "_current_project", lambda: "alpha")

    made: list[discover.Transcript] = []
    for i, name in enumerate(["s1", "s2", "s3"]):
        p = tmp_path / f"{name}.jsonl"
        p.write_text('{"type":"user"}\n', encoding="utf-8")
        made.append(discover.Transcript(path=p, agent="alpha", cwd="/tmp/alpha", mtime=float(i)))
    made.sort(key=lambda t: t.mtime, reverse=True)

    # Records the kwargs it was called with and actually honours them, so a
    # test can catch the CLI passing the wrong project or limit — a stub that
    # ignored `project` would let the whole scoping branch rot untested.
    seen_kwargs: list[dict] = []

    def fake_find(**kw):
        seen_kwargs.append(kw)
        out = made
        if kw.get("project") is not None:
            out = [t for t in out if t.agent == kw["project"]]
        if kw.get("limit") is not None:
            out = out[: kw["limit"]]
        return list(out)

    monkeypatch.setattr(cmd.discover, "find_transcripts", fake_find)
    return cfg, vault, made, seen_kwargs


def _args(**kw) -> argparse.Namespace:
    base = dict(all=False, dry_run=False, project=None, limit=None,
                install_run=False, yes=True, retry_failed=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _recorder(monkeypatch, result=None, raises=None):
    """Stub harvest_session; returns the list it records called paths into."""
    calls: list[Path] = []

    def fake(jsonl_path, agent, config):
        calls.append(jsonl_path)
        if raises is not None:
            raise raises
        return list(result or [])

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)
    return calls


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_calls_no_llm(env, monkeypatch, capsys):
    cfg, vault, made, _ = env

    def explode(*a, **k):
        raise AssertionError("dry run must not harvest")

    monkeypatch.setattr(cmd.harvest, "harvest_session", explode)

    assert cmd.cmd_backfill(_args(dry_run=True, all=True)) == 0
    assert not ledger.state_path(vault).exists()
    out = capsys.readouterr().out
    assert "3 session(s)" in out
    # Every candidate is named, so a user can see what they would be paying for.
    for t in made:
        assert t.path.name in out
    assert "nothing written" in out


# --------------------------------------------------------------------------
# the happy path + the ledger
# --------------------------------------------------------------------------

def test_harvests_and_records_in_ledger(env, monkeypatch):
    cfg, vault, made, _ = env
    calls = _recorder(monkeypatch, result=[Path("written.md")])

    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert len(calls) == 3

    led = ledger.load(vault)
    assert ledger.should_harvest(led, made[0].path) is False


def test_second_run_skips_already_done_sessions(env, monkeypatch):
    cfg, vault, _, _ = env
    calls = _recorder(monkeypatch)

    cmd.cmd_backfill(_args(all=True))
    first = len(calls)
    cmd.cmd_backfill(_args(all=True))
    assert len(calls) == first  # nothing re-harvested


def test_summary_separates_barren_sessions_from_productive_ones(env, monkeypatch, capsys):
    cfg, vault, made, _ = env

    def fake(jsonl_path, agent, config):
        return [Path("a.md")] if jsonl_path == made[0].path else []

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)

    assert cmd.cmd_backfill(_args(all=True)) == 0
    out = capsys.readouterr().out
    assert "processed 3 session(s)" in out
    assert "wrote 1 memory file(s) from 1 of them" in out
    assert "2 produced nothing" in out
    assert "mnemo extract" in out


# --------------------------------------------------------------------------
# scoping
# --------------------------------------------------------------------------

def test_install_run_respects_install_cap_and_scopes_to_this_repo(env, monkeypatch):
    cfg, vault, _, seen = env
    calls = _recorder(monkeypatch)

    assert cmd.cmd_backfill(_args(install_run=True)) == 0
    assert len(calls) == 2  # installCap
    assert seen[-1] == {"project": "alpha", "limit": 2}


def test_install_run_never_prompts(env, monkeypatch):
    cfg, vault, _, _ = env
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(
        "builtins.input",
        lambda *a: (_ for _ in ()).throw(AssertionError("install-run must not prompt")),
    )
    # yes=False: only the install_run branch can keep this from prompting.
    assert cmd.cmd_backfill(_args(install_run=True, yes=False)) == 0
    assert len(calls) == 2


def test_default_scope_is_the_current_repo(env, monkeypatch):
    cfg, vault, _, seen = env
    _recorder(monkeypatch)
    cmd.cmd_backfill(_args())
    assert seen[-1] == {"project": "alpha", "limit": None}


def test_all_flag_drops_the_project_filter(env, monkeypatch):
    cfg, vault, _, seen = env
    _recorder(monkeypatch)
    cmd.cmd_backfill(_args(all=True))
    assert seen[-1] == {"project": None, "limit": None}


def test_explicit_project_wins_over_the_cwd(env, monkeypatch):
    cfg, vault, _, seen = env
    calls = _recorder(monkeypatch)
    cmd.cmd_backfill(_args(project="beta"))
    assert seen[-1] == {"project": "beta", "limit": None}
    assert calls == []  # no transcripts belong to beta


def test_limit_zero_means_zero_not_unlimited(env, monkeypatch, capsys):
    cfg, vault, _, seen = env
    calls = _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True, limit=0)) == 0
    assert seen[-1] == {"project": None, "limit": 0}
    assert calls == []
    assert "no transcripts found" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the confirmation prompt
# --------------------------------------------------------------------------

@pytest.mark.parametrize("reply", ["y", "YES", " yes "])
def test_prompt_accepts_yes(env, monkeypatch, reply):
    cfg, vault, _, _ = env
    calls = _recorder(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: reply)
    assert cmd.cmd_backfill(_args(all=True, yes=False)) == 0
    assert len(calls) == 3


@pytest.mark.parametrize("reply", ["", "n", "no", "whatever"])
def test_prompt_declines_by_default(env, monkeypatch, reply, capsys):
    cfg, vault, _, _ = env
    calls = _recorder(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: reply)
    assert cmd.cmd_backfill(_args(all=True, yes=False)) == 0
    assert calls == []
    assert "cancelled" in capsys.readouterr().out


def test_prompt_treats_eof_as_no(env, monkeypatch):
    cfg, vault, _, _ = env
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(
        "builtins.input", lambda *a: (_ for _ in ()).throw(EOFError())
    )
    assert cmd.cmd_backfill(_args(all=True, yes=False)) == 0
    assert calls == []


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------

def test_one_failing_session_does_not_abort_the_run(env, monkeypatch):
    cfg, vault, made, _ = env
    seen: list[Path] = []

    def flaky(jsonl_path, agent, config):
        seen.append(jsonl_path)
        if jsonl_path == made[0].path:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(cmd.harvest, "harvest_session", flaky)

    assert cmd.cmd_backfill(_args(all=True)) == 1  # some work failed
    assert len(seen) == 3

    led = ledger.load(vault)
    assert led["sessions"][made[0].path.stem]["status"] == "failed"


def test_failures_are_reported_and_change_the_exit_code(env, monkeypatch, capsys):
    cfg, vault, made, _ = env

    def flaky(jsonl_path, agent, config):
        if jsonl_path == made[0].path:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(cmd.harvest, "harvest_session", flaky)

    assert cmd.cmd_backfill(_args(all=True)) == 1
    captured = capsys.readouterr()
    assert "failed: 1" in captured.err
    assert ".errors.log" in captured.err


def test_environmental_failure_never_poisons_the_ledger(env, monkeypatch):
    """Three runs with a broken `claude` must leave everything harvestable.

    Regression test for the bug where every transcript in the sweep was
    charged an attempt for a failure that had nothing to do with it. Archived
    transcripts never change on disk, so the ledger's changed-hash escape
    hatch cannot rescue them: three bad runs abandoned the whole history for
    good, and said nothing about it.
    """
    cfg, vault, made, _ = env
    _recorder(monkeypatch, raises=llm.LLMSubprocessError(
        "claude CLI not found; install Claude Code first"))

    for _ in range(3):
        assert cmd.cmd_backfill(_args(all=True)) == 2  # aborted, not "fine"

    led = ledger.load(vault)
    for t in made:
        assert ledger.should_harvest(led, t.path) is True
        assert ledger.attempts_exhausted(led, t.path) is False

    # And a working environment afterwards harvests all of them.
    calls = _recorder(monkeypatch, result=[Path("a.md")])
    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert len(calls) == 3


def test_environmental_failure_stops_the_sweep_immediately(env, monkeypatch, capsys):
    cfg, vault, made, _ = env
    calls: list[Path] = []

    def fake(jsonl_path, agent, config):
        calls.append(jsonl_path)
        raise llm.LLMSubprocessError("rate limit exceeded")

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)

    assert cmd.cmd_backfill(_args(all=True)) == 2
    assert len(calls) == 1  # did not burn through the other two
    assert "rate limit exceeded" in capsys.readouterr().err


def test_parse_errors_stay_attributable_to_their_transcript(env, monkeypatch):
    """A model that answers with garbage is this transcript's problem."""
    cfg, vault, made, _ = env
    calls = _recorder(monkeypatch, raises=llm.LLMParseError("no JSON object found"))

    assert cmd.cmd_backfill(_args(all=True)) == 1
    assert len(calls) == 3  # every one attempted; not an abort
    led = ledger.load(vault)
    assert led["sessions"][made[0].path.stem]["attempts"] == 1


def test_a_timing_out_transcript_does_not_wedge_the_sweep(env, monkeypatch):
    """A double timeout is the input's fault, so the sweep must step over it.

    Treating it as environmental wedges the sweep permanently: `find_transcripts`
    applies `limit` as a newest-first prefix, so no `--limit` can skip the
    hostile transcript to reach the ones behind it, and every rerun burns two
    full timeouts to make zero progress.
    """
    cfg, vault, made, _ = env
    hostile = made[1].path
    seen: list[Path] = []

    def fake(jsonl_path, agent, config):
        seen.append(jsonl_path)
        if jsonl_path == hostile:
            raise llm.LLMTimeoutError("subprocess timed out twice after 60s")
        return []

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)

    assert cmd.cmd_backfill(_args(all=True)) == 1
    assert seen == [t.path for t in made]  # reached everything behind the wedge

    led = ledger.load(vault)
    assert led["sessions"][hostile.stem]["status"] == "failed"
    for t in made:
        if t.path != hostile:
            assert ledger.should_harvest(led, t.path) is False  # done, not stuck


def test_a_malformed_cli_envelope_is_environmental(env, monkeypatch):
    """A `claude` that exits 0 printing a login nag must not poison the ledger.

    `llm.call` raises LLMParseError for three envelope-level conditions that
    are purely environmental — a login nag, an update notice, a PATH shim, an
    --output-format change. Keying on LLMParseError alone reinstated the
    original Critical through this door.
    """
    cfg, vault, made, _ = env
    _recorder(monkeypatch, raises=llm.LLMEnvelopeError(
        "envelope JSON invalid: Expecting value"))

    for _ in range(3):
        assert cmd.cmd_backfill(_args(all=True)) == 2

    led = ledger.load(vault)
    for t in made:
        assert ledger.should_harvest(led, t.path) is True
        assert ledger.attempts_exhausted(led, t.path) is False


def test_envelope_errors_are_still_parse_errors(env):
    """The new types subclass the old ones, so existing handlers keep working."""
    assert issubclass(llm.LLMEnvelopeError, llm.LLMParseError)
    assert issubclass(llm.LLMTimeoutError, llm.LLMSubprocessError)


# --------------------------------------------------------------------------
# --retry-failed
# --------------------------------------------------------------------------

def test_retry_failed_makes_exhausted_transcripts_eligible_again(env, monkeypatch):
    cfg, vault, made, _ = env
    led = ledger.load(vault)
    for t in made:
        for _ in range(ledger.MAX_ATTEMPTS):
            ledger.mark_failed(led, t.path, "boom")
    ledger.save(vault, led)

    calls = _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert calls == []  # terminal without the flag

    calls = _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True, retry_failed=True)) == 0
    assert len(calls) == 3


def test_retry_failed_does_not_re_harvest_finished_work(env, monkeypatch):
    cfg, vault, made, _ = env
    calls = _recorder(monkeypatch)
    cmd.cmd_backfill(_args(all=True))
    assert len(calls) == 3

    calls = _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True, retry_failed=True)) == 0
    assert calls == []  # done entries are left alone


def test_dry_run_with_retry_failed_leaves_the_ledger_on_disk_alone(env, monkeypatch, capsys):
    """`--dry-run` writes nothing, and that has to survive `--retry-failed`.

    The clear happens in memory so the preview is honest about which
    transcripts a real run would pick up — but a documented exception to a
    safety flag is worse than no flag at all, so nothing reaches disk.
    """
    cfg, vault, made, _ = env
    led = ledger.load(vault)
    for _ in range(ledger.MAX_ATTEMPTS):
        ledger.mark_failed(led, made[0].path, "boom")
    ledger.save(vault, led)
    before = ledger.state_path(vault).read_text(encoding="utf-8")

    calls = _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True, dry_run=True, retry_failed=True)) == 0

    assert calls == []
    assert ledger.state_path(vault).read_text(encoding="utf-8") == before
    assert ledger.attempts_exhausted(ledger.load(vault), made[0].path)

    out = capsys.readouterr().out
    assert "would clear 1 failed entry" in out
    # The retired transcript is previewed as work, which is the point of
    # clearing in memory at all.
    assert f"would harvest alpha/{made[0].path.name}" in out


def test_retry_failed_without_dry_run_still_persists_the_clear(env, monkeypatch, capsys):
    cfg, vault, made, _ = env
    led = ledger.load(vault)
    for _ in range(ledger.MAX_ATTEMPTS):
        ledger.mark_failed(led, made[0].path, "boom")
    ledger.save(vault, led)

    _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True, retry_failed=True)) == 0
    assert "cleared 1 failed entry" in capsys.readouterr().out
    assert not ledger.attempts_exhausted(ledger.load(vault), made[0].path)


def test_project_and_all_cannot_be_combined():
    """They answer the same question and only one can be honoured.

    `_select` resolves the pair by letting `--project` win, which silently
    discards a flag the user typed. argparse rejects it instead.
    """
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()
    assert parser.parse_args(["backfill", "--all"]).all is True
    assert parser.parse_args(["backfill", "--project", "alpha"]).project == "alpha"
    with pytest.raises(SystemExit):
        parser.parse_args(["backfill", "--all", "--project", "alpha"])


def test_gave_up_message_names_the_remedy(env, monkeypatch, capsys):
    cfg, vault, made, _ = env
    led = ledger.load(vault)
    for _ in range(ledger.MAX_ATTEMPTS):
        ledger.mark_failed(led, made[0].path, "boom")
    ledger.save(vault, led)
    _recorder(monkeypatch)

    cmd.cmd_backfill(_args(all=True))
    cmd.cmd_backfill(_args(all=True))  # second run: everything else is done
    out = capsys.readouterr().out
    assert "gave up after 3 failed attempts" in out
    assert "--retry-failed" in out


def test_keyboard_interrupt_exits_130_and_keeps_earlier_work(env, monkeypatch, capsys):
    cfg, vault, made, _ = env

    def fake(jsonl_path, agent, config):
        if jsonl_path == made[1].path:
            raise KeyboardInterrupt()
        return []

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)

    assert cmd.cmd_backfill(_args(all=True)) == 130
    assert "interrupted" in capsys.readouterr().err.lower()

    led = ledger.load(vault)
    assert ledger.should_harvest(led, made[0].path) is False  # flushed
    assert ledger.should_harvest(led, made[1].path) is True   # resumable


def test_exhausted_transcripts_are_not_reported_as_harvested(env, monkeypatch, capsys):
    cfg, vault, made, _ = env
    led = ledger.load(vault)
    for t in made:
        for _ in range(ledger.MAX_ATTEMPTS):
            ledger.mark_failed(led, t.path, "boom")
    ledger.save(vault, led)

    _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True)) == 0
    out = capsys.readouterr().out
    assert "3 gave up after 3 failed attempts" in out
    assert "every transcript is already harvested" not in out


# --------------------------------------------------------------------------
# config + estimate
# --------------------------------------------------------------------------

def test_disabled_config_is_a_no_op(env, monkeypatch):
    cfg, vault, _, _ = env
    cfg["backfill"]["enabled"] = False
    calls = _recorder(monkeypatch)
    assert cmd.cmd_backfill(_args(all=True)) == 0
    # Asserted positively rather than by a raising stub: the sweep's
    # per-session ``except Exception`` would swallow the raise and the test
    # would pass even with the enabled check gone.
    assert calls == []
    assert not ledger.state_path(vault).exists()


def test_failure_message_points_at_the_real_log_path(env, monkeypatch, capsys):
    """errors.log lives in the vault, not at ~/.errors.log."""
    cfg, vault, made, _ = env
    _recorder(monkeypatch, raises=RuntimeError("boom"))

    cmd.cmd_backfill(_args(all=True))
    err = capsys.readouterr().err
    assert str(vault / ".errors.log") in err
    assert "~/.errors.log" not in err
    # And the path named is the one log_error actually wrote to.
    assert (vault / ".errors.log").exists()


def test_abort_message_survives_a_multiline_stderr(env, monkeypatch, capsys):
    cfg, vault, _, _ = env
    noise = "Error: boom\n" + "\n".join(f"    at frame{i} (/x.js:{i})" for i in range(40))
    _recorder(monkeypatch, raises=llm.LLMSubprocessError(
        f"claude exited with code 1: {noise}"))

    assert cmd.cmd_backfill(_args(all=True)) == 2
    err = capsys.readouterr().err
    stopped = [ln for ln in err.splitlines() if "stopped —" in ln]
    assert len(stopped) == 1
    assert len(stopped[0]) < 250
    assert "frame39" not in err
    # The actionable sentence is not buried.
    assert "rerun to resume" in err


def test_estimate_measures_the_flattened_prompt_not_the_raw_bytes(tmp_path: Path):
    """A megabyte of tool_use input is a handful of tokens once flattened."""
    import json

    p = tmp_path / "big.jsonl"
    p.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Write", "input": {"content": "x" * 1_000_000}},
            ]},
        }) + "\n",
        encoding="utf-8",
    )
    t = discover.Transcript(path=p, agent="a", cwd="/tmp/a", mtime=0.0)

    raw_proxy = p.stat().st_size // 4
    est = cmd._estimate_input_tokens([t])
    assert est < 100            # "[assistant] [tool_use: Write]"
    assert raw_proxy > 200_000  # what the byte proxy would have claimed


def test_estimate_survives_an_unreadable_transcript(tmp_path: Path):
    missing = discover.Transcript(path=tmp_path / "gone.jsonl", agent="a",
                                  cwd="/tmp/a", mtime=0.0)
    assert cmd._estimate_input_tokens([missing]) == 0


@pytest.mark.parametrize("n,expected", [
    (0, "0"), (999, "999"), (1_000, "1K"), (37_528, "38K"),
    (999_999, "1000K"), (5_994_882, "6.0M"), (93_787_077, "93.8M"),
])
def test_fmt_tokens_rounds_hard(n, expected):
    assert cmd._fmt_tokens(n) == expected


# --------------------------------------------------------------------------
# the install-run one-shot: who is allowed to say it happened
# --------------------------------------------------------------------------
#
# `session_start` launches this with stdout and stderr on DEVNULL, so this
# process is the only one that ever knows whether a backfill actually ran.
# Letting the *hook* set `installRunDone` meant a `claude` CLI that was
# missing, unauthenticated or rate-limited spent the user's one automatic run
# doing zero work, recording nothing, and explaining itself to a discarded
# stderr.

def test_a_completed_install_run_marks_the_ledger(env, monkeypatch):
    cfg, vault, _, _ = env
    _recorder(monkeypatch, result=["a.md"])

    assert cmd.cmd_backfill(_args(install_run=True)) == 0
    assert ledger.load(vault)["installRunDone"] is True


def test_an_install_run_with_nothing_to_do_still_counts(env, monkeypatch):
    """A machine with no history has been fully backfilled, correctly, by doing
    nothing. Leaving the marker unset would respawn a sweep every session."""
    cfg, vault, made, _ = env
    _recorder(monkeypatch)
    monkeypatch.setattr(cmd.discover, "find_transcripts", lambda **kw: [])

    assert cmd.cmd_backfill(_args(install_run=True)) == 0
    assert ledger.load(vault)["installRunDone"] is True


def test_a_partial_failure_still_counts_as_a_run(env, monkeypatch):
    """One hostile transcript is recorded against itself and stepped over. The
    sweep ran; it does not owe the user another automatic one."""
    cfg, vault, _, _ = env
    _recorder(monkeypatch, raises=ValueError("bad transcript"))

    assert cmd.cmd_backfill(_args(install_run=True)) == 1
    assert ledger.load(vault)["installRunDone"] is True


def test_an_environmental_abort_leaves_the_one_shot_unspent(env, monkeypatch):
    """The fix. A broken environment must cost a retry, not the feature."""
    cfg, vault, _, _ = env
    _recorder(monkeypatch, raises=llm.LLMSubprocessError("claude: command not found"))

    assert cmd.cmd_backfill(_args(install_run=True)) == 2
    assert ledger.load(vault)["installRunDone"] is False
    assert not ledger.spawn_lock_path(vault).exists()


def test_an_interrupted_install_run_leaves_the_one_shot_unspent(env, monkeypatch):
    cfg, vault, _, _ = env
    _recorder(monkeypatch, raises=KeyboardInterrupt())

    assert cmd.cmd_backfill(_args(install_run=True)) == 130
    assert ledger.load(vault)["installRunDone"] is False


def test_an_install_run_releases_the_spawn_lock_it_was_launched_under(env, monkeypatch):
    cfg, vault, _, _ = env
    _recorder(monkeypatch, result=["a.md"])
    assert ledger.acquire_spawn_lock(vault) is True

    cmd.cmd_backfill(_args(install_run=True))
    assert not ledger.spawn_lock_path(vault).exists()


def test_the_marker_is_written_before_the_lock_is_dropped(env, monkeypatch):
    """Ordering, not just eventual state — the gap between them costs money.

    ``session_start`` spawns when it sees no marker *and* wins the lock. If the
    lock goes first, a session starting in that window reads
    ``installRunDone`` as False, finds the lock gone, and launches a second
    sweep: ``installCap`` extra LLM calls on the user's account, which is the
    exact thing the lock exists to prevent. Marking first closes the window —
    a crash between the two leaks a lock that is provably inert, and the hook
    reaps it (see test_hook_session_start_backfill.py).
    """
    cfg, vault, _, _ = env
    _recorder(monkeypatch, result=["a.md"])
    ledger.acquire_spawn_lock(vault)

    seen: list[bool] = []
    real_release = ledger.release_spawn_lock
    monkeypatch.setattr(
        cmd.ledger,
        "release_spawn_lock",
        lambda root: (seen.append(bool(ledger.load(root).get("installRunDone"))),
                      real_release(root))[1],
    )

    cmd.cmd_backfill(_args(install_run=True))

    assert seen == [True], "the lock was dropped while the marker still read False"


def test_an_install_run_that_explodes_still_releases_the_lock(env, monkeypatch):
    """A leaked lock blocks every future session until the TTL reaps it."""
    cfg, vault, _, _ = env
    ledger.acquire_spawn_lock(vault)
    monkeypatch.setattr(cmd, "_run_backfill",
                        lambda _a: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        cmd.cmd_backfill(_args(install_run=True))
    assert not ledger.spawn_lock_path(vault).exists()


def test_an_ordinary_run_never_touches_the_one_shot(env, monkeypatch):
    """`mnemo backfill` typed by a user is not the automatic install run."""
    cfg, vault, _, _ = env
    _recorder(monkeypatch, result=["a.md"])
    ledger.acquire_spawn_lock(vault)

    cmd.cmd_backfill(_args())
    assert ledger.load(vault)["installRunDone"] is False
    assert ledger.spawn_lock_path(vault).exists(), "not this run's lock to release"


def test_a_disabled_backfill_install_run_marks_nothing(env, monkeypatch):
    cfg, vault, _, _ = env
    cfg["backfill"]["enabled"] = False
    _recorder(monkeypatch)

    assert cmd.cmd_backfill(_args(install_run=True)) == 0
    assert ledger.load(vault)["installRunDone"] is False
