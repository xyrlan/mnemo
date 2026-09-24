"""``mnemo backfill`` run for the install review, and backfill pages that expire (#496).

The install review (``docs/superpowers/specs/2026-09-24-install-review-design.md``)
drives the backfill from the desktop, not from a person at a terminal:

- ``--dry-run --json`` is the consent screen's estimate: one JSON document,
  nothing sent;
- ``--yes --extract --progress-json`` harvests, then runs the first extraction
  for that project, and stdout carries nothing but ``harvest`` / ``extract`` /
  ``done`` lines; exit 0 when it finished, even with failures, 2 when it could
  not run;
- a staged backfill page nobody decided expires 14 days after staging, through
  #429's sweep and ledger, and ``--restore`` undoes it.

The model is stubbed at ``llm._subprocess_run`` — the seam under ``llm.call`` —
so the real call path runs, the env every helper is given is visible, and
nothing reaches the network.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from pathlib import Path

import pytest

from mnemo.core import inbox as I
from mnemo.core import llm
from mnemo.core.backfill import discover, ledger
from mnemo.core.extract import prompts, run_extraction
from mnemo.core.extract import reference_gate as rg
from mnemo.cli.commands import backfill as cmd


# --- fixtures ---------------------------------------------------------------


def _transcript(path: Path, *, edits: int) -> Path:
    """A transcript with *edits* file mutations — what decides a harvest call."""
    rows = [{"type": "user", "message": {"role": "user", "content": "fix the cron job"}}]
    for i in range(edits):
        rows.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": f"f{i}.py"}},
        ]}})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / "shared").mkdir()
    (vault / ".mnemo").mkdir()
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "chunkSize": 10, "subprocessTimeout": 60,
                       "referenceGate": {"enabled": True, "model": "claude-haiku-4-5"}},
        "backfill": {"enabled": True, "installCap": 20, "minFileMutations": 1},
    }
    monkeypatch.setattr(cmd.cfg_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cmd, "_current_project", lambda: "alpha")

    made = [
        discover.Transcript(path=_transcript(tmp_path / "s1.jsonl", edits=2),
                            agent="alpha", cwd="/tmp/alpha", mtime=3.0),
        discover.Transcript(path=_transcript(tmp_path / "s2.jsonl", edits=1),
                            agent="alpha", cwd="/tmp/alpha", mtime=2.0),
        # Below minFileMutations: harvest returns before the model for it.
        discover.Transcript(path=_transcript(tmp_path / "s3.jsonl", edits=0),
                            agent="alpha", cwd="/tmp/alpha", mtime=1.0),
    ]

    def fake_find(**kw):
        out = [t for t in made if kw.get("project") in (None, t.agent)]
        return out[: kw["limit"]] if kw.get("limit") is not None else out

    monkeypatch.setattr(cmd.discover, "find_transcripts", fake_find)
    return cfg, vault, made


class _Model:
    """Answers each helper by its system prompt and records every call."""

    def __init__(self, *, fail_harvest: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail_harvest = fail_harvest

    def __call__(self, argv, **kwargs):
        system = argv[argv.index("--system-prompt") + 1] if "--system-prompt" in argv else ""
        self.calls.append({"system": system, "env": kwargs.get("env") or {}})
        if system == prompts.HARVEST_SYSTEM_PROMPT:
            n = sum(1 for c in self.calls if c["system"] == system)
            if self.fail_harvest and n == 1:
                # Content-level garbage: attributable to this transcript, so
                # the sweep counts it and goes on.
                text = "no json here"
            else:
                text = json.dumps({"pages": [
                    {"slug": f"cron-blocked-{n}", "type": "project", "name": "Cron blocked",
                     "description": "annual cron waits on #183", "body": "The annual cron is blocked."},
                    {"slug": f"prefer-small-prs-{n}", "type": "feedback", "name": "Small PRs",
                     "description": "keep PRs small", "body": "Keep PRs small."},
                ]})
        elif system == rg.SYSTEM_PROMPT:
            text = json.dumps({"verdicts": []})
        else:
            text = json.dumps({"pages": [
                {"slug": "prefer-small-prs", "type": "feedback", "name": "Small PRs",
                 "description": "keep PRs small", "body": "Keep PRs small.",
                 "source_files": ["bots/alpha/memory/prefer-small-prs-1.md"]},
            ]})
        envelope = [{"type": "result", "result": text, "apiKeySource": "none",
                     "total_cost_usd": 0.0, "usage": {"input_tokens": 1, "output_tokens": 1}}]
        return argparse.Namespace(returncode=0, stdout=json.dumps(envelope), stderr="")


def _args(**kw) -> argparse.Namespace:
    base = dict(all=False, dry_run=False, project="alpha", limit=None, install_run=False,
                yes=True, retry_failed=False, extract=False, json=False, progress_json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines()]


# --- the estimate -----------------------------------------------------------


def test_dry_run_json_is_one_document_and_sends_nothing(env, monkeypatch, capsys):
    cfg, vault, made = env
    model = _Model()
    monkeypatch.setattr(llm, "_subprocess_run", model)

    assert cmd.cmd_backfill(_args(dry_run=True, json=True)) == 0

    doc = json.loads(capsys.readouterr().out)
    assert doc["project"] == "alpha"
    assert doc["sessions"] == 3
    # Two sessions clear the mutation gate; the third never reaches the model.
    assert doc["harvest_calls"] == 2
    assert doc["extract_calls_estimate"] == 2  # one cluster + one gate call per chunk
    assert doc["calls_estimate"] == 4
    assert isinstance(doc["api_price_estimate_usd"], float)
    assert model.calls == []
    assert not ledger.state_path(vault).exists()


def test_the_prompt_quotes_the_same_estimate(env, monkeypatch, capsys):
    """One estimate: the terminal line counts the calls the JSON counts."""
    monkeypatch.setattr(llm, "_subprocess_run", _Model())

    assert cmd.cmd_backfill(_args(dry_run=True)) == 0

    assert "2 LLM call(s)" in capsys.readouterr().out


def test_an_unpriced_model_says_null_not_zero(env, monkeypatch, capsys):
    cfg, _, _ = env
    cfg["extraction"]["model"] = "some-model-the-table-lacks"

    assert cmd.cmd_backfill(_args(dry_run=True, json=True)) == 0

    assert json.loads(capsys.readouterr().out)["api_price_estimate_usd"] is None


# --- the run ----------------------------------------------------------------


def test_progress_json_harvests_then_extracts_and_stdout_is_only_events(env, monkeypatch, capsys):
    cfg, vault, made = env
    model = _Model()
    monkeypatch.setattr(llm, "_subprocess_run", model)

    code = cmd.cmd_backfill(_args(extract=True, progress_json=True))

    out = capsys.readouterr().out
    events = _lines(out)  # raises if anything but JSON reached stdout
    assert code == 0
    assert events[:3] == [
        {"event": "harvest", "done": 1, "of": 3},
        {"event": "harvest", "done": 2, "of": 3},
        {"event": "harvest", "done": 3, "of": 3},
    ]
    extract = [e for e in events if e["event"] == "extract"]
    assert extract and extract[-1]["done"] == extract[-1]["of"]
    done = events[-1]
    assert done["event"] == "done" and done["failed"] == 0 and done["failed_chunks"] == 0
    # Every backfill page stages. The feedback page cites no briefing, so the
    # evidence gate demotes it to a reference page — staged all the same.
    staged = {p.key for p in I.staged_pages(vault)}
    assert staged == {"project/alpha__cron-blocked-1", "project/alpha__cron-blocked-2",
                      "reference/prefer-small-prs"}
    assert all(p.backfill for p in I.staged_pages(vault))
    assert done["staged"] == 3
    assert done["live"] == 0
    # Every helper the run launched carried the hook guard.
    assert model.calls
    assert all(c["env"].get("MNEMO_HOOKS_OFF") == "1" for c in model.calls)


def test_the_extraction_is_scoped_to_the_project(env, monkeypatch, capsys):
    """Another agent's dirty memory is not this review's to spend on."""
    cfg, vault, _ = env
    other = vault / "bots" / "beta" / "memory"
    other.mkdir(parents=True)
    (other / "unrelated.md").write_text(
        "---\nname: unrelated\ntype: feedback\ndescription: d\n---\n\nbeta only\n", encoding="utf-8",
    )
    model = _Model()
    monkeypatch.setattr(llm, "_subprocess_run", model)

    assert cmd.cmd_backfill(_args(extract=True, progress_json=True)) == 0

    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    assert "feedback/unrelated" not in state["entries"]
    assert not any("beta only" in json.dumps(c) for c in model.calls)


def test_a_failed_session_is_counted_and_the_run_still_exits_0(env, monkeypatch, capsys):
    monkeypatch.setattr(llm, "_subprocess_run", _Model(fail_harvest=True))

    code = cmd.cmd_backfill(_args(extract=True, progress_json=True))

    events = _lines(capsys.readouterr().out)
    assert code == 0
    assert events[-1]["event"] == "done" and events[-1]["failed"] == 1


def test_a_run_that_cannot_start_exits_2_with_an_error_line(env, monkeypatch, capsys):
    """An environmental failure — no CLI — stops the sweep: exit 2, and the
    caller gets a reason on stdout rather than a half-finished stream."""
    def missing(argv, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(llm, "_subprocess_run", missing)

    code = cmd.cmd_backfill(_args(extract=True, progress_json=True))

    events = _lines(capsys.readouterr().out)
    assert code == 2
    assert events[-1]["event"] == "error"
    assert not any(e["event"] == "done" for e in events)


def test_disabled_backfill_is_exit_2_under_json(env, capsys):
    cfg, _, _ = env
    cfg["backfill"]["enabled"] = False

    assert cmd.cmd_backfill(_args(extract=True, progress_json=True)) == 2
    assert _lines(capsys.readouterr().out)[-1]["event"] == "error"


def test_a_held_extraction_lock_is_exit_2(env, monkeypatch, capsys):
    from mnemo.core.extract import ExtractionIOError

    monkeypatch.setattr(llm, "_subprocess_run", _Model())

    def locked(cfg, **kw):
        raise ExtractionIOError("another extraction is in progress (lock held)")

    monkeypatch.setattr("mnemo.core.extract.run_extraction", locked)

    assert cmd.cmd_backfill(_args(extract=True, progress_json=True)) == 2
    events = _lines(capsys.readouterr().out)
    assert events[-1]["event"] == "error" and "lock held" in events[-1]["message"]


def test_it_never_runs_a_hook_main(env, monkeypatch, capsys):
    from mnemo.hooks import session_end, session_start

    def boom(*a, **k):
        raise AssertionError("a hook main ran")

    monkeypatch.setattr(session_end, "main", boom)
    monkeypatch.setattr(session_start, "main", boom)
    monkeypatch.setattr(llm, "_subprocess_run", _Model())

    assert cmd.cmd_backfill(_args(extract=True, progress_json=True)) == 0


def test_without_yes_it_still_asks_and_a_no_sends_nothing(env, monkeypatch, capsys):
    model = _Model()
    monkeypatch.setattr(llm, "_subprocess_run", model)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert cmd.cmd_backfill(_args(yes=False, extract=True, progress_json=True)) == 0

    assert _lines(capsys.readouterr().out) == [{"event": "cancelled"}]
    assert model.calls == []


def test_json_without_dry_run_is_refused(env, capsys):
    assert cmd.cmd_backfill(_args(json=True)) == 2


def test_extract_without_progress_json_reports_in_text(env, monkeypatch, capsys):
    monkeypatch.setattr(llm, "_subprocess_run", _Model())

    assert cmd.cmd_backfill(_args(extract=True)) == 0

    assert "staged" in capsys.readouterr().out


def test_the_parser_knows_the_flags():
    from mnemo.cli.parser import _build_parser

    ns = _build_parser().parse_args(
        ["backfill", "--project", "p", "--yes", "--extract", "--progress-json"])
    assert ns.extract and ns.progress_json and ns.yes and ns.project == "p"
    assert _build_parser().parse_args(["backfill", "--dry-run", "--json"]).json


def test_run_extraction_reports_every_chunk(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    mem = root / "bots" / "alpha" / "memory"
    mem.mkdir(parents=True)
    for i in range(3):
        (mem / f"f{i}.md").write_text(
            f"---\nname: f{i}\ntype: feedback\ndescription: d\n---\n\nbody {i}\n", encoding="utf-8")
    monkeypatch.setattr(llm, "_subprocess_run", _Model())
    ticks: list[tuple[int, int]] = []

    cfg = {"vaultRoot": str(root),
           "extraction": {"model": "m", "chunkSize": 2, "subprocessTimeout": 60,
                          "referenceGate": {"enabled": False}}}
    run_extraction(cfg, project="alpha", on_chunk=lambda d, t: ticks.append((d, t)))

    assert ticks == [(1, 2), (2, 2)]


# --- expiry -----------------------------------------------------------------


def _staged(vault: Path, rel: str, *, age_days: float, backfill: bool = True) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    origin = "origin: backfill\n" if backfill else ""
    path.write_text(
        # `slug:` as every writer stamps it: without one, extraction's slug
        # migration rewrites the page and restarts its clock.
        f"---\nname: {path.stem}\nslug: {path.stem}\ndescription: d\n"
        f"type: {path.parent.name}\n{origin}"
        "sources:\n  - bots/alpha/memory/x.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    ts = time.time() - age_days * 86400 - 60
    os.utime(path, (ts, ts))
    return path


def _state(vault: Path, *keys: str) -> None:
    entries = {k: {"source_files": ["bots/alpha/memory/x.md"], "source_hash": "sh",
                   "written_hash": "wh", "written_at": "r", "last_sync": "r",
                   "status": "inbox"} for k in keys}
    (vault / ".mnemo").mkdir(parents=True, exist_ok=True)
    (vault / ".mnemo" / "extraction-state.json").write_text(
        json.dumps({"schema_version": 2, "last_run": "r", "entries": entries}), encoding="utf-8")


def _status(vault: Path, key: str) -> str:
    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    return state["entries"][key]["status"]


def test_an_undecided_backfill_page_expires_after_14_days_and_restores(tmp_vault: Path):
    _staged(tmp_vault, "shared/_inbox/project/alpha__cron.md", age_days=15)
    _state(tmp_vault, "project/alpha__cron")

    results = I.expire_held(tmp_vault, days=I.held_expiry_days({}))

    assert [(r.ok, r.why) for r in results] == [(True, I.WHY_BACKFILL)]
    assert not (tmp_vault / "shared" / "_inbox" / "project" / "alpha__cron.md").exists()
    assert _status(tmp_vault, "project/alpha__cron") == "dismissed"
    assert [(r["event"], r["key"]) for r in I.read_ledger(tmp_vault)] == [
        ("expired", "project/alpha__cron")]

    restored = I.restore(tmp_vault, "project/alpha__cron")

    assert restored.ok
    assert (tmp_vault / "shared" / "_inbox" / "project" / "alpha__cron.md").exists()
    assert _status(tmp_vault, "project/alpha__cron") == "inbox"
    # A restore is a decision to keep it waiting: the sweep never takes it again.
    assert I.expire_held(tmp_vault, days=14, now=None) == []
    page = next(p for p in I.staged_pages(tmp_vault) if p.key == "project/alpha__cron")
    assert I.expires_at(page, {}, restored=I.restored_keys(tmp_vault)) is None


def test_a_backfill_page_younger_than_14_days_stays(tmp_vault: Path):
    _staged(tmp_vault, "shared/_inbox/feedback/fresh.md", age_days=13)

    assert I.expire_held(tmp_vault, days=14) == []


def test_a_live_capture_page_still_waits_for_a_human(tmp_vault: Path):
    _staged(tmp_vault, "shared/_inbox/feedback/live.md", age_days=90, backfill=False)

    assert I.expire_held(tmp_vault, days=14) == []


def test_expires_at_is_staging_plus_the_one_window(tmp_vault: Path):
    from datetime import datetime, timedelta

    path = _staged(tmp_vault, "shared/_inbox/reference/r.md", age_days=2)
    _staged(tmp_vault, "shared/_inbox/feedback/live.md", age_days=2, backfill=False)
    pages = {p.key: p for p in I.staged_pages(tmp_vault)}

    got = I.expires_at(pages["reference/r"])
    assert got == datetime.fromtimestamp(path.stat().st_mtime) + timedelta(days=I.HELD_EXPIRY_DAYS)
    assert I.expires_at(pages["feedback/live"]) is None
    assert I.expires_at(pages["reference/r"], {"inbox": {"heldExpiryDays": 0}}) is None


def test_an_extraction_run_expires_backfill_pages_and_counts_them_apart(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "bots" / "alpha" / "memory").mkdir(parents=True)
    _staged(root, "shared/_inbox/project/alpha__old.md", age_days=15)
    _staged(root, "shared/_inbox/reference/judged.md", age_days=15, backfill=False)
    (root / "shared" / "_inbox" / "reference" / "judged.md").write_text(
        "---\nname: judged\nslug: judged\ndescription: d\ntype: reference\n"
        "reference_gate: generic\n---\n\nb\n",
        encoding="utf-8")
    old = time.time() - 16 * 86400
    os.utime(root / "shared" / "_inbox" / "reference" / "judged.md", (old, old))
    monkeypatch.setattr(llm, "_subprocess_run", _Model())

    summary = run_extraction({"vaultRoot": str(root),
                              "extraction": {"model": "m", "chunkSize": 10, "subprocessTimeout": 60}})

    assert summary.backfill_expired == 1
    assert summary.reference_expired == 1


def test_a_project_scoped_run_expires_nothing(tmp_path, monkeypatch):
    """Expiry is vault-wide housekeeping; the review's own run is not the
    place to archive another project's pages."""
    root = tmp_path / "vault"
    (root / "bots" / "alpha" / "memory").mkdir(parents=True)
    _staged(root, "shared/_inbox/project/beta__old.md", age_days=15)
    monkeypatch.setattr(llm, "_subprocess_run", _Model())

    summary = run_extraction({"vaultRoot": str(root),
                              "extraction": {"model": "m", "chunkSize": 10, "subprocessTimeout": 60}},
                             project="alpha")

    assert summary.backfill_expired == 0
    assert (root / "shared" / "_inbox" / "project" / "beta__old.md").exists()


def test_mnemo_extract_prints_backfill_expired(tmp_vault: Path, tmp_home, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.core.extract import ExtractionSummary

    summary = ExtractionSummary()
    summary.backfill_expired = 4
    monkeypatch.setattr("mnemo.core.extract.run_extraction", lambda cfg, **_kw: summary)
    cfg_path = tmp_vault / "mnemo.config.json"
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    assert cli.main(["extract"]) == 0
    assert "backfill expired: 4" in capsys.readouterr().out
