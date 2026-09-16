"""``mnemo replay`` — the user-facing measurement (#237).

The property under test is honesty: a rule that would fire on a prompt is
credited to the vault only when the vault learned it in a *different, earlier*
session. The same-session case and the not-yet-learned case are the two ways a
naive replay flatters itself, and both must be counted out, visibly.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mnemo.core.reflex import replay as R
from mnemo.core.reflex.index import build_index

_CFG = {"enabled": True, "maxEmissionsPerSession": 10}
PROMPT = "How do I mock prisma in a jest test with typescript"
SID_A = "aaaaaaaa-0000-0000-0000-000000000001"
SID_B = "bbbbbbbb-0000-0000-0000-000000000002"
SID_C = "cccccccc-0000-0000-0000-000000000003"

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rule(vault: Path, slug: str, *, sources: list[str], extracted_at: str,
          desc: str = "Always use jest-mock-extended to mock Prisma in tests",
          body: str = "Mock the Prisma client in tests using jest-mock-extended.",
          tags=("prisma", "testing"), extra: str = "") -> None:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    src = "".join(f"  - {s}\n" for s in sources)
    tag = "".join(f"  - {t}\n" for t in tags)
    (d / f"{slug}.md").write_text(
        f"---\nname: {slug}\ndescription: {desc}\ntype: feedback\n"
        f"extracted_at: {extracted_at}\nstability: stable\n"
        f"tags:\n{tag}sources:\n{src}{extra}---\n{body}\n",
        encoding="utf-8",
    )


def _noise(vault: Path) -> None:
    for i, (name, desc, tag) in enumerate([
        ("use-yarn", "Prefer yarn over npm for installs", "yarn"),
        ("commit-strategy", "Small atomic commits with clear messages", "git"),
        ("review-etiquette", "Be kind and specific in code reviews", "review"),
        ("python-style", "Follow PEP8 and black formatting", "python"),
        ("docs-style", "Write clear, concise documentation", "docs"),
    ]):
        _rule(vault, name, sources=[f"bots/noise{i}/memory/x.md"],
              extracted_at="2026-01-01T00:00:00", desc=desc,
              body=f"Body for {name}.", tags=(tag,))


def _briefing(vault: Path, project: str, sid: str) -> str:
    p = vault / "bots" / project / "briefings" / "sessions" / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# briefing\n", encoding="utf-8")
    return f"bots/{project}/briefings/sessions/{sid}.md"


def _transcript(projects: Path, sid: str, turns: list[dict]) -> Path:
    path = projects / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t) + "\n")
    return path


def _user(text, at: datetime, *, cwd="/nowhere/alpha", **extra) -> dict:
    return {"type": "user", "timestamp": _iso(at), "cwd": cwd,
            "message": {"role": "user", "content": text}, **extra}


@pytest.fixture
def env(tmp_path: Path):
    """Rule learned from session A on Sep 1; A's briefing filed under ``alpha``."""
    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)
    src = _briefing(vault, "alpha", SID_A)
    _rule(vault, "use-prisma-mock", sources=[src],
          extracted_at=(T0 + timedelta(hours=1)).astimezone().strftime("%Y-%m-%dT%H:%M:%S"))
    _noise(vault)
    projects = tmp_path / "projects" / "-nowhere-alpha"
    projects.mkdir(parents=True)
    return vault, projects


def _project_for(_cwd: str, _sid: str) -> str:
    return "alpha"


# --- reading transcripts -------------------------------------------------------

def test_parse_ts_accepts_zulu_and_naive_local():
    z = R._parse_ts("2026-09-01T12:00:00.000Z")
    assert z == T0.timestamp()
    naive = R._parse_ts("2026-09-01T12:00:00")
    assert naive == datetime(2026, 9, 1, 12, 0).astimezone().timestamp()
    assert R._parse_ts("") is None and R._parse_ts(None) is None and R._parse_ts("junk") is None


def test_read_prompts_keeps_only_what_a_person_typed(env):
    vault, projects = env
    path = _transcript(projects, SID_B, [
        _user("<command-name>/clear</command-name>", T0),
        _user([{"type": "tool_result", "content": "x"}], T0 + timedelta(seconds=1)),
        _user("subagent turn about prisma mocks in jest", T0 + timedelta(seconds=2), isSidechain=True),
        _user("meta turn about prisma mocks in jest", T0 + timedelta(seconds=3), isMeta=True),
        {"type": "assistant", "timestamp": _iso(T0), "message": {"content": "hi"}},
        _user(PROMPT, T0 + timedelta(seconds=4)),
        _user([{"type": "text", "text": "a second real prompt about yarn installs"}],
              T0 + timedelta(seconds=5)),
        {"type": "user", "message": {"content": "no timestamp at all here"}},
    ])

    prompts = R.read_prompts(path, SID_B, _project_for)

    assert [p.text for p in prompts] == [PROMPT, "a second real prompt about yarn installs"]
    assert all(p.session_id == SID_B and p.project == "alpha" for p in prompts)


def test_collect_prompts_is_oldest_first_across_sessions(env):
    vault, projects = env
    _transcript(projects, SID_B, [_user("later prompt about prisma", T0 + timedelta(days=2))])
    _transcript(projects, SID_C, [_user("earlier prompt about prisma", T0 + timedelta(days=1))])

    prompts = R.collect_prompts(vault, projects_root=projects.parent, project_for=_project_for)

    assert [p.session_id for p in prompts] == [SID_C, SID_B]


def test_default_project_falls_back_to_the_briefing_when_the_tree_is_gone(env):
    vault, projects = env
    resolve = R.default_project_for(vault)
    assert resolve("/definitely/not/here", SID_A) == "alpha"
    assert resolve("/definitely/not/here", "unknown-session") == "here"


def test_default_project_folds_a_gone_dispatch_tree_into_its_repo(env, tmp_path):
    """#334: a delivered child replays against its repo's rules, as the hook
    saw them while the tree existed — not against its basename, which no rule
    is filed under, and not against a pre-#301 briefing filed under it."""
    vault, _projects = env
    repo = tmp_path / "src" / "clubinho"
    (repo / ".git").mkdir(parents=True)
    _briefing(vault, "clubinho-wt-192", SID_B)
    resolve = R.default_project_for(vault)

    assert resolve(str(repo.parent / "clubinho-wt-192"), SID_B) == "clubinho"
    assert resolve(str(repo.parent / "clubinho-wt-c-parser"), "s") == "clubinho"


def test_default_project_keeps_a_gone_tree_it_cannot_fold(env, tmp_path):
    """Only dispatch's own shapes fold, and only onto a repo that is there:
    a hand-made tree, or a child of a hand-made tree that is itself gone,
    keeps its briefing's project or its own name (#301)."""
    vault, _projects = env
    (tmp_path / "desk" / ".git").mkdir(parents=True)
    resolve = R.default_project_for(vault)

    assert resolve(str(tmp_path / "desk-old"), SID_A) == "alpha"
    assert resolve(str(tmp_path / "desk-old"), "s") == "desk-old"
    assert resolve(str(tmp_path / "desk-wt-round6-wt-40"), "s") == "desk-wt-round6-wt-40"


# --- reading the vault ---------------------------------------------------------

def test_rule_facts_reads_sessions_dates_and_evidence(env):
    vault, _projects = env
    src_b = _briefing(vault, "alpha", SID_B)
    _rule(vault, "verified-one", sources=[src_b], extracted_at="2026-09-02T10:00:00",
          desc="verified rule", body="verified body", tags=("x",),
          extra=("confidence: verified\nevidence:\n  quote: 'never sum the rows, use the global total'\n"
                 f"  source: 'briefing: {src_b} — user turns, turn 3'\n"))
    _rule(vault, "unverified-one", sources=[src_b], extracted_at="2026-09-02T10:00:00",
          desc="unverified rule", body="unverified body", tags=("y",),
          extra="confidence: inferred\nevidence:\n  quote: 'some words here that are long'\n  source: 'x'\n")

    facts = R.rule_facts(vault)

    assert facts["use-prisma-mock"].taught_by == frozenset({SID_A})
    assert facts["use-prisma-mock"].learned_at == pytest.approx((T0 + timedelta(hours=1)).timestamp(), abs=1)
    assert facts["use-prisma-mock"].correction_backed is False
    assert facts["verified-one"].correction_backed is True
    assert facts["verified-one"].taught_by == frozenset({SID_B})
    assert facts["unverified-one"].correction_backed is False
    assert facts["use-yarn"].taught_by == frozenset()


def test_rule_facts_reads_the_session_out_of_a_prose_evidence_source(env):
    """Reclassified pages cite ``briefing: <path> — user turns, turn N``; the
    session in there counts as a teacher even when ``sources:`` names another."""
    vault, _projects = env
    src_c = _briefing(vault, "alpha", SID_C)
    _rule(vault, "prose-cited", sources=[src_c], extracted_at="2026-09-02T10:00:00",
          desc="prose cited rule", body="prose body", tags=("z",),
          extra=("confidence: verified\nevidence:\n  quote: 'never sum the rows, use the global total'\n"
                 f"  source: 'briefing: bots/alpha/briefings/sessions/{SID_B}.md — user turns, turn 3'\n"))

    facts = R.rule_facts(vault)
    assert facts["prose-cited"].taught_by == frozenset({SID_B, SID_C})


def test_rule_facts_dates_project_pages_by_promoted_at(tmp_path):
    vault = tmp_path / "vault"
    d = vault / "shared" / "project"
    d.mkdir(parents=True)
    (d / "p.md").write_text(
        "---\nname: p\ntype: project\npromoted_at: 2026-06-15T09:39:02\n"
        "sources:\n  - bots/alpha/memory/p.md\n---\nbody\n", encoding="utf-8")

    facts = R.rule_facts(vault)
    assert facts["p"].learned_at == datetime(2026, 6, 15, 9, 39, 2).astimezone().timestamp()


def test_rule_facts_skips_drafts_like_the_index_does(tmp_path):
    vault = tmp_path / "vault"
    _rule(vault, "live", sources=["bots/a/memory/x.md"], extracted_at="2026-01-01T00:00:00")
    _rule(vault, "evolving", sources=["bots/a/memory/x.md"], extracted_at="2026-01-01T00:00:00")
    text = (vault / "shared" / "feedback" / "evolving.md").read_text(encoding="utf-8")
    (vault / "shared" / "feedback" / "evolving.md").write_text(
        text.replace("stability: stable", "stability: evolving"), encoding="utf-8")

    facts = R.rule_facts(vault)
    assert set(facts) == {"live"}


# --- classification -------------------------------------------------------------

def _p(sid: str, at: datetime) -> R.Prompt:
    return R.Prompt(session_id=sid, project="alpha", ts=at.timestamp(), text=PROMPT)


def test_classify_the_three_buckets():
    facts = R.RuleFacts(taught_by=frozenset({SID_A}), learned_at=T0.timestamp(), correction_backed=False)
    assert R.classify(facts, _p(SID_A, T0 + timedelta(days=5))) == R.HINDSIGHT
    assert R.classify(facts, _p(SID_B, T0 + timedelta(days=5))) == R.CARRIED
    assert R.classify(facts, _p(SID_B, T0 - timedelta(days=5))) == R.NOT_YET_LEARNED
    assert R.classify(R.RuleFacts(frozenset(), None, False), _p(SID_B, T0)) == R.UNDATED
    assert R.classify(None, _p(SID_B, T0)) == R.UNDATED


def test_hindsight_beats_dates():
    """A rule fired in the session that taught it is hindsight even if the
    page's date says it was learned earlier — the session is the stronger fact."""
    facts = R.RuleFacts(taught_by=frozenset({SID_A}), learned_at=(T0 - timedelta(days=9)).timestamp(),
                        correction_backed=False)
    assert R.classify(facts, _p(SID_A, T0)) == R.HINDSIGHT


# --- the replay ------------------------------------------------------------------

def _run(vault, prompts):
    return R.run(prompts, build_index(vault), R.rule_facts(vault), reflex_cfg=_CFG)


def test_same_prompt_lands_in_a_different_bucket_per_session(env):
    vault, _projects = env
    prompts = [
        _p(SID_C, T0 - timedelta(days=1)),   # before the rule existed
        _p(SID_A, T0 + timedelta(days=1)),   # the session that taught it
        _p(SID_B, T0 + timedelta(days=2)),   # a later, unrelated session
    ]

    out = _run(vault, prompts)

    assert [(i.session_id, i.bucket) for i in out.injections] == [
        (SID_C, R.NOT_YET_LEARNED), (SID_A, R.HINDSIGHT), (SID_B, R.CARRIED),
    ]
    assert out.fired_prompts == 3


def test_day_level_dedupe_and_session_cap_are_simulated(env):
    vault, _projects = env
    same_day = [_p(SID_B, T0 + timedelta(days=2, minutes=m)) for m in range(3)]
    next_day = [_p(SID_B, T0 + timedelta(days=3))]

    out = _run(vault, same_day + next_day)

    assert len(out.injections) == 2, "once per day, vault-wide, like injected_cache"
    assert out.silence.get("deduped") == 2

    capped = R.run(same_day + next_day, build_index(vault), R.rule_facts(vault),
                   reflex_cfg={"maxEmissionsPerSession": 1})
    assert len(capped.injections) == 1
    assert capped.silence.get("session_cap_reached") == 3


def test_silence_reasons_are_the_hooks(env):
    vault, _projects = env
    out = _run(vault, [
        R.Prompt(SID_B, "alpha", (T0 + timedelta(days=2)).timestamp(), "ok"),
        R.Prompt(SID_B, "nobody", (T0 + timedelta(days=2)).timestamp(), PROMPT),
    ])
    assert out.injections == []
    assert out.silence == {"below_min_tokens": 1, "index_missing": 1}


def test_project_overrides_are_asked_once_per_project(env):
    vault, _projects = env
    asked = []

    def overrides_for(project):
        asked.append(project)
        return {"absolute_floor": 99.0}

    out = R.run([_p(SID_B, T0 + timedelta(days=2))] * 3, build_index(vault), R.rule_facts(vault),
                reflex_cfg=_CFG, overrides_for=overrides_for)
    assert asked == ["alpha"]
    assert out.silence == {"absolute_floor_fail": 3}


# --- the report --------------------------------------------------------------------

def test_wilson_interval_behaves_near_zero_and_at_the_edges():
    lo, hi = R.wilson_interval(0, 100)
    assert lo == 0.0 and 0.0 < hi < 0.05
    lo, hi = R.wilson_interval(50, 100)
    assert 0.40 < lo < 0.5 < hi < 0.60
    assert R.wilson_interval(0, 0) == (0.0, 0.0)
    assert R.wilson_interval(100, 100)[1] == pytest.approx(1.0)


def _fake(prompts, injections, silence=None, fired=None):
    return R.Replay(prompts=prompts, injections=injections, silence=silence or {},
                    fired_prompts=len({(i.session_id, i.ts) for i in injections}) if fired is None else fired)


def _inj(sid, at, slug, bucket, backed=False):
    """``backed`` here is the strong kind: verified by the gate, not just labelled."""
    return R.Injection(session_id=sid, project="alpha", ts=at.timestamp(), slug=slug,
                       bucket=bucket, correction_backed=backed, gate_verified=backed)


def test_aggregate_credits_a_prompt_by_its_best_bucket():
    at = T0 + timedelta(days=2)
    prompts = [_p(SID_B, at), _p(SID_B, at + timedelta(minutes=1)), _p(SID_B, at + timedelta(minutes=2))]
    injections = [
        _inj(SID_B, at, "r1", R.CARRIED, backed=True),
        _inj(SID_B, at, "r2", R.HINDSIGHT),
        _inj(SID_B, at + timedelta(minutes=1), "r3", R.NOT_YET_LEARNED),
    ]

    report = R.aggregate(_fake(prompts, injections), vault_rules=10, correction_backed_rules=1,
                         gate_verified_rules=1)

    assert report["prompts"] == {
        "total": 3, "fired": 2, "carried": 1, "carried_correction_backed": 1,
        "carried_gate_verified": 1, "carried_label_only": 0,
        "carried_correction_backed_by_origin": {"user": 1, "ci": 0},
        "hindsight": 0, "not_yet_learned": 1, "undated": 0,
    }
    assert report["injections"]["carried"] == 1 and report["injections"]["hindsight"] == 1
    assert report["rules"] == {"carried_distinct": 1, "carried_correction_backed_distinct": 1,
                               "carried_gate_verified_distinct": 1, "carried_label_only_distinct": 0,
                               "carried_correction_backed_distinct_by_origin": {"user": 1, "ci": 0}}
    assert report["top_carried"] == [{"slug": "r1", "prompts": 1, "correction_backed": True,
                                      "gate_verified": True, "origin": "user"}]
    assert report["rate"] is None, "three prompts is not a rate"
    assert report["transcripts"] == {"sessions": 1, "prompts": 3, "first": "2026-09-03", "last": "2026-09-03"}


def test_aggregate_prints_a_rate_only_past_the_floor():
    at = T0
    prompts = [_p(SID_B, at + timedelta(minutes=i)) for i in range(R.MIN_PROMPTS_FOR_RATE)]
    injections = [_inj(SID_B, at + timedelta(minutes=i), f"r{i}", R.CARRIED) for i in range(3)]

    report = R.aggregate(_fake(prompts, injections), vault_rules=10, correction_backed_rules=0)

    assert report["rate"]["carried"] == 0.1
    lo, hi = report["rate"]["carried_ci95"]
    assert lo < 0.1 < hi
    assert report["rate"]["carried_correction_backed"] == 0.0

    short = R.aggregate(_fake(prompts[:-1], injections), vault_rules=10, correction_backed_rules=0)
    assert short["rate"] is None


def test_aggregate_on_nothing():
    report = R.aggregate(_fake([], []), vault_rules=0, correction_backed_rules=0)
    assert report["prompts"]["total"] == 0
    assert report["transcripts"]["first"] is None
    assert report["rate"] is None
    assert "nothing" not in R.format_report(report)  # renders without dividing by zero


def test_format_report_says_what_it_did_not_count():
    at = T0
    prompts = [_p(SID_B, at + timedelta(minutes=i)) for i in range(40)]
    injections = [
        _inj(SID_B, at, "carried-rule", R.CARRIED, backed=True),
        _inj(SID_B, at + timedelta(minutes=1), "same-session-rule", R.HINDSIGHT),
        _inj(SID_B, at + timedelta(minutes=2), "future-rule", R.NOT_YET_LEARNED),
    ]
    report = R.aggregate(_fake(prompts, injections, silence={"relative_gap_fail": 37}),
                         vault_rules=10, correction_backed_rules=1)

    text = R.format_report(report)

    def has(pattern: str) -> bool:
        return re.search(pattern, text) is not None

    assert has(r"prompts replayed\s+40\s+\(1 sessions")
    assert has(r"EARLIER session\s+1\s+prompts\s+2\.5%\s+\(95% CI")
    assert has(r"SAME session\s+1\s+prompts\s+hindsight")
    assert has(r"not learned yet\s+1\s+prompts")
    assert "carried-rule  ✓ your words" in text
    assert has(r"37\s+relative_gap_fail")
    assert "not simulated: export suppression" in text
    assert "not measured: whether an injected rule changed the answer" in text

    few = R.aggregate(_fake(prompts[:5], injections), vault_rules=10, correction_backed_rules=1)
    few_text = R.format_report(few)
    assert "95% CI" not in few_text
    assert "no rate is printed" in few_text


def test_end_to_end_same_inputs_same_report(env):
    """Reproducibility is the promise on the tin: run twice, diff nothing but the clock."""
    vault, projects = env
    _transcript(projects, SID_B, [_user(PROMPT, T0 + timedelta(days=2))])
    _transcript(projects, SID_A, [_user(PROMPT, T0 + timedelta(days=1))])

    def once():
        prompts = R.collect_prompts(vault, projects_root=projects.parent, project_for=_project_for)
        out = _run(vault, prompts)
        rep = R.aggregate(out, vault_rules=6, correction_backed_rules=0)
        rep.pop("generated_at")
        return rep

    first, second = once(), once()
    assert first == second
    assert first["prompts"]["carried"] == 1 and first["prompts"]["hindsight"] == 1


# --- provenance: gate-verified vs label-only (#257) ---------------------------

def _corrections_briefing(vault: Path, project: str, sid: str, quote: str) -> str:
    """A briefing whose ``## Corrections`` carries *quote* — what today's gate reads."""
    from mnemo.core import corrections

    rel = _briefing(vault, project, sid)
    item = corrections.Correction(quote=quote, rule="do the thing")
    (vault / rel).write_text("# briefing\n\n" + corrections.render_section([item]), encoding="utf-8")
    return rel


def test_rule_facts_tells_a_gate_verified_label_from_a_label_only_one(env):
    """A ``confidence: verified`` written by ``mnemo reclassify`` in 2026-09 cites a
    briefing that has no ``## Corrections`` at all; today's gate cannot re-check
    it. Both count as correction-backed; only one is verified by the gate."""
    vault, _projects = env
    quote = "never sum the rows, use the global total from the api"
    src_b = _corrections_briefing(vault, "alpha", SID_B, quote)
    _rule(vault, "gate-verified", sources=[src_b], extracted_at="2026-09-02T10:00:00",
          desc="gate verified rule", body="gate body", tags=("g",),
          extra=f"confidence: verified\nevidence:\n  quote: '{quote}'\n  source: '{src_b}'\n")
    src_c = _briefing(vault, "alpha", SID_C)  # no Corrections section
    _rule(vault, "label-only", sources=[src_c], extracted_at="2026-09-02T10:00:00",
          desc="label only rule", body="label body", tags=("l",),
          extra=(f"confidence: verified\nevidence:\n  quote: '{quote}'\n"
                 f"  source: 'briefing: {src_c} — user turns, turn 3'\n"))

    facts = R.rule_facts(vault)

    assert facts["gate-verified"].correction_backed is True
    assert facts["gate-verified"].gate_verified is True
    assert facts["label-only"].correction_backed is True
    assert facts["label-only"].gate_verified is False
    assert facts["use-prisma-mock"].gate_verified is False


def test_rule_facts_gate_refuses_a_quote_from_a_briefing_the_rule_was_not_built_from(env):
    """Same bar as ``evidence.verify_page``: the cited briefing must be one of the
    page's own sources, or one project's correction launders another's rule."""
    vault, _projects = env
    quote = "never sum the rows, use the global total from the api"
    src_b = _corrections_briefing(vault, "alpha", SID_B, quote)
    src_c = _briefing(vault, "alpha", SID_C)
    _rule(vault, "laundered", sources=[src_c], extracted_at="2026-09-02T10:00:00",
          desc="laundered rule", body="laundered body", tags=("w",),
          extra=f"confidence: verified\nevidence:\n  quote: '{quote}'\n  source: '{src_b}'\n")

    facts = R.rule_facts(vault)
    assert facts["laundered"].correction_backed is True
    assert facts["laundered"].gate_verified is False


def _ginj(sid, at, slug, bucket, *, gate):
    return R.Injection(session_id=sid, project="alpha", ts=at.timestamp(), slug=slug,
                       bucket=bucket, correction_backed=True, gate_verified=gate)


def test_aggregate_splits_correction_backed_by_provenance():
    at = T0 + timedelta(days=2)
    prompts = [_p(SID_B, at), _p(SID_B, at + timedelta(minutes=1)), _p(SID_B, at + timedelta(minutes=2))]
    injections = [
        _ginj(SID_B, at, "gate-rule", R.CARRIED, gate=True),
        _ginj(SID_B, at + timedelta(minutes=1), "label-rule", R.CARRIED, gate=False),
        _ginj(SID_B, at + timedelta(minutes=2), "same-session", R.HINDSIGHT, gate=True),
    ]

    report = R.aggregate(_fake(prompts, injections), vault_rules=10,
                         correction_backed_rules=3, gate_verified_rules=1)

    assert report["vault"] == {
        "rules": 10, "correction_backed": 3, "gate_verified": 1, "label_only": 2,
        "correction_backed_by_origin": {"user": 3, "ci": 0},
    }
    assert report["prompts"]["carried_correction_backed"] == 2
    assert report["prompts"]["carried_gate_verified"] == 1
    assert report["prompts"]["carried_label_only"] == 1
    assert report["injections"]["carried_gate_verified"] == 1
    assert report["injections"]["carried_label_only"] == 1
    assert report["rules"]["carried_gate_verified_distinct"] == 1
    assert report["rules"]["carried_label_only_distinct"] == 1
    assert report["top_carried"] == [
        {"slug": "gate-rule", "prompts": 1, "correction_backed": True, "gate_verified": True,
         "origin": "user"},
        {"slug": "label-rule", "prompts": 1, "correction_backed": True, "gate_verified": False,
         "origin": "user"},
    ]


def test_aggregate_rate_carries_the_gate_verified_share():
    at = T0
    prompts = [_p(SID_B, at + timedelta(minutes=i)) for i in range(R.MIN_PROMPTS_FOR_RATE)]
    injections = [
        _ginj(SID_B, at, "gate-rule", R.CARRIED, gate=True),
        _ginj(SID_B, at + timedelta(minutes=1), "label-rule", R.CARRIED, gate=False),
        _ginj(SID_B, at + timedelta(minutes=2), "label-rule-2", R.CARRIED, gate=False),
    ]
    report = R.aggregate(_fake(prompts, injections), vault_rules=10,
                         correction_backed_rules=3, gate_verified_rules=1)

    assert report["rate"]["carried_correction_backed"] == 0.1
    assert report["rate"]["carried_gate_verified"] == pytest.approx(1 / 30, abs=1e-4)
    lo, hi = report["rate"]["carried_gate_verified_ci95"]
    assert lo < 1 / 30 < hi


def test_format_report_prints_both_provenances():
    at = T0
    prompts = [_p(SID_B, at + timedelta(minutes=i)) for i in range(40)]
    injections = [
        _ginj(SID_B, at, "gate-rule", R.CARRIED, gate=True),
        _ginj(SID_B, at + timedelta(minutes=1), "label-rule", R.CARRIED, gate=False),
    ]
    report = R.aggregate(_fake(prompts, injections), vault_rules=10,
                         correction_backed_rules=3, gate_verified_rules=1)

    text = R.format_report(report)

    def has(pattern: str) -> bool:
        return re.search(pattern, text) is not None

    assert has(r"rules in the vault\s+10\s+\(3 cite a correction you typed: 1 verified by the evidence gate today, 2 label only\)")
    assert has(r"citing your own words\s+1\s+prompts\s+2\.5%\s+\(95% CI [\d.]+–[\d.]+%\)\s+verified by the evidence gate today")
    assert has(r"label only, gate can't check\s+1\s+prompts")
    assert has(r"distinct rules carried across sessions\s+2\s+\(1 gate-verified, 1 label only\)")
    assert "gate-rule  ✓ your words" in text
    assert "label-rule  ~ label only" in text
