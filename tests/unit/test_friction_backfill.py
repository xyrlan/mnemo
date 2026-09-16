"""The retroactive friction sweep and the ``mnemo friction`` command.

No test spawns a model: the briefing, the ranking and the contradiction pass
are all injected. The ``link`` piece (``rank`` / ``resolve``) is written in
parallel, so its shapes are stood in for here by duck-typed fakes.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mnemo.core.friction import backfill as BF
from mnemo.core.friction import ledger
from mnemo.core.mcp import access_log

S_FOUND = "aaaaaaaa-1111-0000-0000-000000000001"
S_NONE = "bbbbbbbb-2222-0000-0000-000000000002"
S_GONE = "cccccccc-3333-0000-0000-000000000003"

QUOTE = "merge needs the admin flag, run it yourself without asking"
RULE = "Run `gh pr merge --admin` yourself; do not ask first."


@dataclass
class Cand:
    slug: str
    name: str = ""
    body: str = ""
    score: float = 1.0
    rank: int = 0


@dataclass
class Link:
    contradicts: list = field(default_factory=list)
    link_basis: str = "none"
    raw: dict = field(default_factory=dict)


@pytest.fixture
def telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


def _transcript(root: Path, sid: str, turns: list, *, cwd: str = "/nowhere/proj",
                mtime: float | None = None, day: str = "2026-09-10") -> Path:
    d = root / "-nowhere-proj"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{sid}.jsonl"
    rows = []
    for i, text in enumerate(turns):
        rows.append({"type": "user", "cwd": cwd, "timestamp": f"{day}T12:0{i}:00Z",
                     "message": {"role": "user", "content": text}})
        rows.append({"type": "assistant", "timestamp": f"{day}T12:0{i}:30Z",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _briefing(vault: Path, sid: str, project: str = "proj", *, day: str = "2026-08-01") -> Path:
    md = vault / "bots" / project / "briefings" / "sessions" / f"{sid}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(f"---\ntype: briefing\nagent: {project}\nsession_id: {sid}\ndate: {day}\n---\n\nbody\n",
                  encoding="utf-8")
    return md


def _rule(vault: Path, slug: str, *, kind: str = "feedback") -> None:
    md = vault / "shared" / kind / f"{slug}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(f"---\nname: {slug}\nslug: {slug}\ntype: {kind}\n---\n\nbody\n", encoding="utf-8")


class Briefer:
    """Writes a briefing whose Corrections are whatever the test says."""

    def __init__(self, items: dict):
        self.items = items
        self.calls: list = []

    def __call__(self, jsonl, project, sid, out_root):
        self.calls.append(sid)
        if self.items.get(sid) == "boom":
            raise RuntimeError("llm down")
        lines = "\n".join(f'- "{q}" → {r}' for q, r in self.items.get(sid, []))
        body = "## Summary\n\nstuff\n\n## Corrections\n\n" + lines + "\n"
        out = Path(out_root) / "bots" / project / "briefings" / "sessions" / f"{sid}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"---\ntype: briefing\n---\n\n{body}", encoding="utf-8")
        return out


def _ranker(slugs):
    calls = []

    def rank(vault_root, correction, *, project):
        calls.append((correction.quote, project))
        return [Cand(slug=s, rank=i) for i, s in enumerate(slugs, start=1)]

    rank.calls = calls
    return rank


def _resolver(contradicts):
    calls = []

    def resolve(correction, candidates, *, runner=None):
        calls.append(correction.quote)
        return Link(contradicts=list(contradicts), link_basis="extractor" if contradicts else "none")

    resolve.calls = calls
    return resolve


@pytest.fixture
def world(tmp_vault: Path, tmp_path: Path):
    """Three sessions: one with a correction, one without, one whose transcript is gone."""
    projects = tmp_path / "projects"
    _transcript(projects, S_FOUND, ["fix the merge step", QUOTE], mtime=2_000_000_000)
    _transcript(projects, S_NONE, ["add a readme section please"], mtime=1_900_000_000)
    _briefing(tmp_vault, S_GONE)
    for slug in ("merge-requires-admin", "unrelated-rule"):
        _rule(tmp_vault, slug)
    briefer = Briefer({S_FOUND: [(QUOTE, RULE)], S_NONE: []})
    return tmp_vault, projects, briefer


def _plan(vault, projects, briefer, *, contradicts=("merge-requires-admin",), **kw):
    return BF.plan(
        vault, projects_root=projects, briefer=briefer,
        ranker=kw.pop("ranker", _ranker(["unrelated-rule", "merge-requires-admin"])),
        resolver=kw.pop("resolver", _resolver(contradicts)),
        **kw,
    )


# --- the dry run ---------------------------------------------------------------

def test_three_sessions_produce_the_three_outcomes(world):
    vault, projects, briefer = world
    p = _plan(vault, projects, briefer)

    by = {s.session_id: s for s in p.sessions}
    assert by[S_FOUND].status == BF.FOUND and by[S_FOUND].found == 1
    assert by[S_NONE].status == BF.NONE_FOUND
    assert by[S_GONE].status == BF.GONE
    assert p.complete
    # Newest first; the gone session is never briefed.
    assert [s.session_id for s in p.sessions][:2] == [S_FOUND, S_NONE]
    assert S_GONE not in briefer.calls

    c = by[S_FOUND].corrections[0]
    assert c.quote == QUOTE and c.rule == RULE
    assert c.contradicts == ["merge-requires-admin"]
    assert c.ranks == {"merge-requires-admin": 2}
    assert c.link_basis == ledger.LINK_EXTRACTOR
    assert by[S_FOUND].ts == "2026-09-10T12:00:00Z"
    assert by[S_FOUND].briefing.startswith(".mnemo/friction-backfill/briefings/bots/")

    text = BF.format_plan(p)
    for label in ("corrections found (1)", "none found", "transcript gone"):
        assert label in text


def test_the_plan_is_saved_and_round_trips(world):
    vault, projects, briefer = world
    p = _plan(vault, projects, briefer)
    saved = BF.load_plan(BF.plan_path(vault))
    assert saved.to_dict() == p.to_dict()


def test_every_session_on_disk_is_swept_not_only_briefed_ones(world):
    """No briefing exists for either on-disk session, and both are swept."""
    vault, projects, briefer = world
    _plan(vault, projects, briefer)
    assert set(briefer.calls) == {S_FOUND, S_NONE}


def test_a_fabricated_quote_never_reaches_the_plan(world):
    vault, projects, _ = world
    briefer = Briefer({S_FOUND: [("the user never typed these particular words at all", RULE)]})
    resolve = _resolver(["merge-requires-admin"])
    p = _plan(vault, projects, briefer, resolver=resolve)
    s = {s.session_id: s for s in p.sessions}[S_FOUND]
    assert s.status == BF.NONE_FOUND and s.rejected == 1 and not s.corrections
    assert resolve.calls == []


def test_a_briefing_failure_is_reported_and_the_sweep_goes_on(world):
    vault, projects, _ = world
    briefer = Briefer({S_FOUND: "boom", S_NONE: []})
    p = _plan(vault, projects, briefer)
    by = {s.session_id: s for s in p.sessions}
    assert by[S_FOUND].status == BF.FAILED and "llm down" in by[S_FOUND].detail
    assert by[S_NONE].status == BF.NONE_FOUND


def test_a_session_with_nothing_typed_costs_no_briefing(world):
    vault, projects, _ = world
    brief = "You are building one piece of the feature x: do the thing."
    _transcript(projects, "dddddddd-4444", [brief], mtime=2_100_000_000)
    briefer = Briefer({})
    p = _plan(vault, projects, briefer)
    s = {s.session_id: s for s in p.sessions}["dddddddd-4444"]
    assert s.status == BF.NONE_FOUND and s.detail == "no typed turns"
    assert "dddddddd-4444" not in briefer.calls


def test_a_failed_link_keeps_the_correction(world):
    vault, projects, briefer = world

    def resolve(correction, candidates, *, runner=None):
        raise TimeoutError("pass timed out")

    p = _plan(vault, projects, briefer, resolver=resolve)
    c = {s.session_id: s for s in p.sessions}[S_FOUND].corrections[0]
    assert c.contradicts == [] and c.link_basis == ledger.LINK_NONE
    assert "TimeoutError" in c.detail


def test_a_missing_link_piece_degrades_to_unlinked(world, monkeypatch):
    vault, projects, briefer = world

    def missing():
        raise ImportError("no link piece")

    monkeypatch.setattr(BF, "_default_ranker", missing)
    p = BF.plan(vault, projects_root=projects, briefer=briefer)
    s = {s.session_id: s for s in p.sessions}[S_FOUND]
    assert s.status == BF.FOUND and s.corrections[0].link_basis == ledger.LINK_NONE


def test_a_slug_outside_the_candidates_is_dropped(world):
    vault, projects, briefer = world
    p = _plan(vault, projects, briefer, contradicts=["invented-slug", "merge-requires-admin"])
    c = {s.session_id: s for s in p.sessions}[S_FOUND].corrections[0]
    assert c.contradicts == ["merge-requires-admin"]


def test_an_injected_contradicted_rule_is_corroborated(world):
    vault, projects, briefer = world
    log = vault / ".mnemo" / "reflex-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"session_id": S_FOUND, "emitted": ["merge-requires-admin"]}) + "\n"
        + json.dumps({"session_id": S_NONE, "emitted": ["unrelated-rule"]}) + "\n",
        encoding="utf-8",
    )
    p = _plan(vault, projects, briefer)
    s = {s.session_id: s for s in p.sessions}[S_FOUND]
    assert s.injected == ["merge-requires-admin"]
    assert s.corrections[0].link_basis == ledger.LINK_EXTRACTOR_INJECTED


def test_a_rerun_reuses_what_was_planned(world):
    vault, projects, briefer = world
    _plan(vault, projects, briefer)
    resolve = _resolver(["merge-requires-admin"])
    again = Briefer({})
    p = _plan(vault, projects, again, resolver=resolve)
    assert again.calls == [] and resolve.calls == []
    assert {s.session_id: s for s in p.sessions}[S_FOUND].found == 1

    fresh = Briefer({S_FOUND: [(QUOTE, RULE)]})
    _plan(vault, projects, fresh, fresh=True)
    assert set(fresh.calls) == {S_FOUND, S_NONE}


def test_a_changed_transcript_is_planned_again(world):
    vault, projects, briefer = world
    _plan(vault, projects, briefer)
    path = projects / "-nowhere-proj" / f"{S_NONE}.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    again = Briefer({})
    _plan(vault, projects, again)
    assert again.calls == [S_NONE]


def test_an_interrupted_sweep_keeps_what_it_planned(world):
    vault, projects, _ = world

    class Interrupting(Briefer):
        def __call__(self, jsonl, project, sid, out_root):
            if sid == S_NONE:
                raise KeyboardInterrupt
            return super().__call__(jsonl, project, sid, out_root)

    with pytest.raises(KeyboardInterrupt):
        _plan(vault, projects, Interrupting({S_FOUND: [(QUOTE, RULE)]}))
    saved = BF.load_plan(BF.plan_path(vault))
    assert not saved.complete
    assert [s.session_id for s in saved.sessions] == [S_FOUND]

    resume = Briefer({S_NONE: []})
    p = _plan(vault, projects, resume)
    assert resume.calls == [S_NONE] and p.complete


def test_since_and_project_bound_the_sweep(world):
    vault, projects, briefer = world
    _transcript(projects, "eeeeeeee-5555", ["something else entirely here"], cwd="/nowhere/other",
                mtime=2_050_000_000)
    _briefing(vault, "eeeeeeee-5555", project="other")

    # 2_000_000_000 is 2033-05-18; 1_900_000_000 is 2030-03-17.
    p = _plan(vault, projects, briefer, since="2031-01-01")
    assert {s.session_id for s in p.sessions} == {S_FOUND, "eeeeeeee-5555"}

    p = _plan(vault, projects, briefer, project="other")
    assert [s.session_id for s in p.sessions] == ["eeeeeeee-5555"]
    assert p.project == "other"

    with pytest.raises(ValueError):
        _plan(vault, projects, briefer, since="last week")


# --- apply -----------------------------------------------------------------------

def test_apply_twice_writes_each_row_once(world, telemetry_on):
    vault, projects, briefer = world
    p = _plan(vault, projects, briefer)

    first = BF.apply(vault, p)
    second = BF.apply(vault, BF.load_plan(BF.plan_path(vault)))

    rows = list(ledger.iter_records(vault))
    assert len(rows) == 1 and len(first.written) == 1
    assert second.written == [] and second.duplicates == 1 and second.failed == 0
    r = rows[0]
    assert r.backfilled is True
    assert (r.session_id, r.quote, r.rule_text) == (S_FOUND, QUOTE, RULE)
    assert r.contradicts == ["merge-requires-admin"]
    assert r.link_basis == ledger.LINK_EXTRACTOR
    assert r.ts == "2026-09-10T12:00:00Z" and r.id.startswith("f-20260910-")
    assert r.briefing == p.sessions[0].briefing

    ranks = [json.loads(line) for line in
             BF.link_ranks_path(vault).read_text(encoding="utf-8").splitlines()]
    assert ranks == [{"id": r.id, "slug": "merge-requires-admin", "rank": 2, "candidates": 2}]


def test_apply_runs_no_llm(world, telemetry_on, monkeypatch):
    vault, projects, briefer = world
    p = _plan(vault, projects, briefer)

    def forbidden(*a, **k):
        raise AssertionError("apply must not run a pass")

    monkeypatch.setattr(BF, "_default_ranker", forbidden)
    monkeypatch.setattr(BF, "_default_resolver", forbidden)
    monkeypatch.setattr(BF, "default_briefer", forbidden)
    assert len(BF.apply(vault, p).written) == 1


def test_apply_with_telemetry_off_writes_nothing_and_says_so(world, monkeypatch):
    vault, projects, briefer = world
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (False, 1_048_576))
    report = BF.apply(vault, _plan(vault, projects, briefer))
    assert report.telemetry_off and report.written == []
    assert not ledger.ledger_path(vault).exists()


# --- the report ------------------------------------------------------------------

def test_the_report_counts_what_the_ledger_holds(world, telemetry_on):
    vault, projects, briefer = world
    BF.apply(vault, _plan(vault, projects, briefer))
    ledger.record(vault, ledger.FrictionRecord(
        session_id="live", project="other", quote="some later live correction text",
        contradicts=["gone-slug"], link_basis=ledger.LINK_EXTRACTOR_INJECTED, origin="ci",
    ))
    ledger.record(vault, ledger.FrictionRecord(
        session_id="live", project="proj", quote="an unlinked correction from the user",
    ))

    s = BF.summarize(vault)
    assert s["records"] == 3 and s["backfilled"] == 1
    assert s["by_project"] == {"proj": 2, "other": 1}
    assert s["by_origin"] == {"ci": 1, "user": 2}
    assert s["linked_records"] == 2 and s["links"] == 2
    assert s["corroborated"] == 1
    assert s["contradicted_live"] == ["merge-requires-admin"]
    assert s["contradicted_unknown"] == ["gone-slug"]
    assert s["live_rules"] == 2
    assert s["rank_distribution"]["1-5"] == 1
    assert s["rank_unknown"] == 1

    assert BF.summarize(vault, project="other")["records"] == 1
    text = BF.format_summary(s)
    assert "live rules standing contradicted: 1 of 2" in text
    assert "? gone-slug" in text


def test_the_report_on_an_empty_ledger(tmp_vault):
    assert BF.summarize(tmp_vault)["records"] == 0
    assert "the ledger is empty" in BF.format_summary(BF.summarize(tmp_vault))


# --- the command -----------------------------------------------------------------

def _ns(**kw):
    base = dict(command="friction", backfill=False, apply=False, fresh=False,
                since=None, project=None, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_friction_is_registered_with_its_flags():
    from mnemo.cli.parser import COMMANDS, _build_parser

    ns = _build_parser().parse_args(
        ["friction", "--backfill", "--fresh", "--since", "2026-09-01", "--project", "mnemo", "--json"])
    assert "friction" in COMMANDS
    assert (ns.backfill, ns.fresh, ns.since, ns.project, ns.json) == (True, True, "2026-09-01", "mnemo", True)
    assert _build_parser().parse_args(["friction", "--apply"]).apply is True


@pytest.fixture
def cli_vault(tmp_vault, monkeypatch):
    from mnemo import cli

    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    return tmp_vault


def test_backfill_and_apply_together_are_refused(cli_vault, capsys):
    from mnemo.cli.commands.friction import cmd_friction

    assert cmd_friction(_ns(backfill=True, apply=True)) == 1
    assert "pick one" in capsys.readouterr().out


def test_apply_without_a_plan_says_so(cli_vault, capsys):
    from mnemo.cli.commands.friction import cmd_friction

    assert cmd_friction(_ns(apply=True)) == 1
    assert "--backfill` first" in capsys.readouterr().out


def test_the_command_plans_then_applies(world, cli_vault, monkeypatch, capsys, telemetry_on):
    from mnemo.cli.commands.friction import cmd_friction

    vault, projects, briefer = world
    real_plan = BF.plan

    def planned(v, **kw):
        return real_plan(v, projects_root=projects, briefer=briefer,
                         ranker=_ranker(["merge-requires-admin"]),
                         resolver=_resolver(["merge-requires-admin"]), **kw)

    monkeypatch.setattr(BF, "plan", planned)
    assert cmd_friction(_ns(backfill=True, json=True)) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["complete"] is True
    assert "corrections found (1)" in out.err

    assert cmd_friction(_ns(apply=True)) == 0
    assert "wrote 1 correction(s) from 1 session(s)" in capsys.readouterr().out
    assert cmd_friction(_ns(apply=True)) == 0
    assert "wrote 0 correction(s)" in capsys.readouterr().out

    assert cmd_friction(_ns(json=True)) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 1


def test_an_interrupted_command_exits_130_and_points_at_the_plan(cli_vault, monkeypatch, capsys):
    from mnemo.cli.commands.friction import cmd_friction

    def interrupted(v, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(BF, "plan", interrupted)
    assert cmd_friction(_ns(backfill=True)) == 130
    assert "rerun `mnemo friction --backfill`" in capsys.readouterr().err
