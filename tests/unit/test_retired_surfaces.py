"""Every surface that reads live rules agrees on what a retired rule is.

Retirement is resolved in two places only: the reflex index (for everything
that ranks, through ``decide.candidates_for_project``) and
``filters.is_retired`` (for everything that parses a page itself). The first
half of this file enumerates the ten modules the refutation design measured
and asserts each takes one of those paths — so a surface added later, or an
existing one rewritten, cannot quietly ignore retirement. The second half
checks what each kind of surface does with a retired rule.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mnemo.core import rule_activation
from mnemo.core.extract.prompts import existing_rules
from mnemo.core.filters import SUPERSEDED_BY, SUPERSEDED_BY_FRICTION
from mnemo.core.friction import retire as R
from mnemo.core.mcp import access_log
from mnemo.core.mcp.tools import list_rules_by_topic, read_mnemo_rule
from mnemo.core.reflex import replay
from mnemo.core.reflex.decide import candidates_for_project, decide
from mnemo.core.reflex.index import build_index
from tests.unit import _retire_fixtures as fx

SRC = Path(__file__).resolve().parents[2] / "src" / "mnemo"

# How each surface learns a rule was retired.
RANKS = "ranks through decide.candidates_for_project"
PREDICATE = "calls filters.is_retired"
DELEGATES = "reads rules only through list_rules_by_topic"
NO_PAGES = "reads no rule page"

#: The ten modules ``docs/superpowers/specs/2026-09-16-refutation-design.md``
#: lists, and the path each takes. Keep this in step with the spec: a module
#: that starts reading rule pages belongs here.
SURFACES = {
    "core/mcp/tools.py": PREDICATE,
    "core/mcp/recall.py": DELEGATES,
    "core/mcp/popularity.py": NO_PAGES,
    "core/mcp/recall_sessions.py": RANKS,
    "core/reflex/index.py": PREDICATE,
    "core/reflex/replay.py": RANKS,
    "core/extract/prompts/existing_rules.py": PREDICATE,
    "core/dashboard.py": PREDICATE,
    "cli/commands/recall.py": DELEGATES,
    "core/activity/summarize.py": NO_PAGES,
}

#: Surfaces that do not take their path yet. Both are outside the file
#: boundary of the friction-loop-wave2 ``retire`` piece that introduced the
#: predicate, so they are recorded here instead of being fixed there.
#: ``strict``: the day one is fixed this test fails, and the entry must go.
PENDING = {
    "core/dashboard.py": (
        "HOME lists live rules from its own frontmatter walk and never asks "
        "is_retired — a retired rule stays on the dashboard"
    ),
    "core/mcp/recall_sessions.py": (
        "run_case copies candidates_for_project's comprehension instead of "
        "calling it, so the recall harness still ranks retired rules"
    ),
}

_PAGE_READERS = {"parse_frontmatter", "split_frontmatter"}


def _names(module: str) -> set[str]:
    """Every identifier and attribute name the module's code mentions."""
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.add((node.asname or node.name).split(".")[-1])
            out.add(node.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _takes_its_path(module: str, path: str) -> bool:
    names = _names(module)
    if path == PREDICATE:
        return "is_retired" in names
    if path == RANKS:
        return "candidates_for_project" in names or (
            "decide" in names and "mnemo.core.reflex.decide" in names
        )
    if path == DELEGATES:
        return not (names & _PAGE_READERS) and (
            "list_rules_by_topic" in names or "mnemo.core.mcp.recall" in names
        )
    if path == NO_PAGES:
        return not (names & _PAGE_READERS) and "is_consumer_visible" not in names
    raise AssertionError(path)


def test_the_enumeration_is_the_ten_the_spec_measured():
    assert len(SURFACES) == 10
    for module in SURFACES:
        assert (SRC / module).is_file(), module
    assert set(PENDING) <= set(SURFACES)


@pytest.mark.parametrize("module", sorted(SURFACES))
def test_each_surface_takes_one_of_the_two_paths(module, request):
    if module in PENDING:
        request.applymarker(pytest.mark.xfail(reason=PENDING[module], strict=True))
    assert _takes_its_path(module, SURFACES[module]), (
        f"{module} should {SURFACES[module]}"
    )


def test_ranking_goes_through_one_filter():
    """``decide`` asks ``candidates_for_project``; nothing re-filters after it."""
    names = _names("core/reflex/decide.py")
    assert "candidates_for_project" in names
    assert "retired" in (SRC / "core/reflex/decide.py").read_text(encoding="utf-8")
    assert "retired" not in (SRC / "core/reflex/replay.py").read_text(encoding="utf-8")


# --- behaviour ---------------------------------------------------------------------

_CFG = {
    "enabled": True,
    "maxEmissionsPerSession": 10,
    "thresholds": {"minQueryTokens": 1, "termOverlapMin": 1, "relativeGap": 1.0,
                   "absoluteFloor": 0.0},
}
PROMPT = "merging a pull request on master with gh pr merge admin flag"


@pytest.fixture(autouse=True)
def _telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


@pytest.fixture(autouse=True)
def _fresh_hint_cache():
    existing_rules.clear_cache()
    yield
    existing_rules.clear_cache()


@pytest.fixture
def vault(tmp_vault: Path) -> Path:
    fx.seed(tmp_vault)
    return tmp_vault


@pytest.fixture
def retired(vault: Path) -> Path:
    """The vault after the contradicted rule was retired, indexes rebuilt."""
    rec = fx.record(vault)
    assert R.retire(vault, rec, replacement=fx.NEW).ok
    rule_activation.write_index(vault, rule_activation.build_index(vault))
    return vault


def test_a_retired_rule_is_not_a_candidate(retired):
    index = build_index(retired)
    assert index["docs"][fx.OLD]["retired"] is True
    assert index["docs"][fx.NEW]["retired"] is False
    # Still indexed, so corpus statistics do not shift under every other rule.
    assert fx.OLD in index["docs"]
    candidates = candidates_for_project(index, fx.PROJECT)
    assert fx.OLD not in candidates and fx.NEW in candidates


def test_decide_never_accepts_a_retired_rule(vault):
    before = decide(build_index(vault), project=fx.PROJECT, prompt=PROMPT, reflex_cfg=_CFG)
    assert fx.OLD in [s for s, _ in before.scores]

    rec = fx.record(vault)
    assert R.retire(vault, rec, replacement=fx.NEW).ok
    after = decide(build_index(vault), project=fx.PROJECT, prompt=PROMPT, reflex_cfg=_CFG)

    assert fx.OLD not in [s for s, _ in after.scores]
    assert fx.OLD not in after.accepted


def test_an_old_index_without_the_key_reads_as_live(vault):
    index = build_index(vault)
    for doc in index["docs"].values():
        doc.pop("retired")
    assert fx.OLD in candidates_for_project(index, fx.PROJECT)


def test_an_unbacked_superseded_by_leaves_the_rule_a_candidate(vault):
    fx.write_rule(vault, fx.OLD, body="Merging requires the admin flag.", extra=(
        f"{SUPERSEDED_BY}: {fx.NEW}\n{SUPERSEDED_BY_FRICTION}: f-20260916-000000000000\n"
    ))
    rule_activation.write_index(vault, rule_activation.build_index(vault))
    index = build_index(vault)

    assert index["docs"][fx.OLD]["retired"] is False
    assert fx.OLD in candidates_for_project(index, fx.PROJECT)
    assert fx.OLD in [r["slug"] for r in list_rules_by_topic(vault, "git", scope="vault")]
    assert "retired" not in read_mnemo_rule(vault, fx.OLD, scope="vault")
    assert "RETIRED" not in existing_rules.existing_rules_fragment(
        vault, "feedback", agents={fx.PROJECT})


def test_list_rules_by_topic_withholds_and_counts(retired):
    shown = list_rules_by_topic(retired, "git", scope="vault")

    assert fx.OLD not in [r["slug"] for r in shown]
    assert fx.NEW in [r["slug"] for r in shown]
    assert shown.retired_withheld == 1
    assert shown.note == "1 retired rule not shown (include_retired=True returns them)"


def test_list_rules_by_topic_include_retired_returns_them(retired):
    shown = list_rules_by_topic(retired, "git", scope="vault", include_retired=True)
    assert fx.OLD in [r["slug"] for r in shown]
    assert shown.retired_withheld == 0 and shown.note is None


def test_list_rules_by_topic_withholds_on_the_legacy_path_too(retired):
    (retired / ".mnemo" / "rule-activation-index.json").unlink()
    assert rule_activation.load_index(retired) is None

    shown = list_rules_by_topic(retired, "git", project=fx.PROJECT)

    assert fx.OLD not in [r["slug"] for r in shown]
    assert shown.retired_withheld == 1


def test_list_rules_by_topic_is_still_a_plain_json_list(retired):
    import json

    shown = list_rules_by_topic(retired, "git", scope="vault")
    assert json.loads(json.dumps(shown)) == list(shown)


def test_withheld_count_is_zero_without_retirements(vault):
    rule_activation.write_index(vault, rule_activation.build_index(vault))
    shown = list_rules_by_topic(vault, "git", scope="vault")
    assert fx.OLD in [r["slug"] for r in shown]
    assert shown.retired_withheld == 0


@pytest.mark.parametrize("indexed", [True, False])
def test_read_mnemo_rule_always_returns_a_retired_rule(retired, indexed):
    if not indexed:
        (retired / ".mnemo" / "rule-activation-index.json").unlink()

    rule = read_mnemo_rule(retired, fx.OLD, scope="vault")

    assert rule is not None and rule["slug"] == fx.OLD
    assert rule["retired"] is True and rule["superseded_by"] == fx.NEW
    head = rule["body"].split("\n\n")[0]
    assert "RETIRED" in head and fx.NEW in head and fx.QUOTE in head
    # The rule's own text is still there, after the header.
    assert "requires the admin flag" in rule["body"]


def test_read_mnemo_rule_of_a_live_rule_has_no_header(retired):
    rule = read_mnemo_rule(retired, fx.NEW, scope="vault")
    assert "retired" not in rule and not rule["body"].startswith(">")


def test_existing_rules_keeps_a_retired_rule_marked(retired):
    fragment = existing_rules.existing_rules_fragment(retired, "feedback", agents={fx.PROJECT})

    old_line = next(ln for ln in fragment.splitlines() if ln.startswith(f"- {fx.OLD} "))
    assert "RETIRED" in old_line and fx.NEW in old_line
    new_line = next(ln for ln in fragment.splitlines() if ln.startswith(f"- {fx.NEW} "))
    assert "RETIRED" not in new_line


def test_existing_rules_never_quotes_a_retired_body_for_editing(retired):
    from mnemo.core.extract.scanner import MemoryFile

    chunk = [MemoryFile(
        path=Path("/v/bots/mnemo/briefings/sessions/s.md"), agent=fx.PROJECT,
        type="feedback", slug="merge-requires-admin",
        frontmatter={"type": "briefing"},
        body="Merging a pull request on master requires the admin flag: run gh pr "
             "merge with --admin because code owner approval blocks it.",
        source_hash="h",
    )]

    fragment = existing_rules.existing_rules_fragment(
        retired, "feedback", agents={fx.PROJECT}, chunk=chunk)

    assert f"### {fx.OLD}\n" not in fragment
    assert f"- {fx.OLD} " in fragment


# --- replay ---------------------------------------------------------------------


def _replay(vault: Path) -> replay.Replay:
    prompts = [
        replay.Prompt(session_id=f"s{i}", project=fx.PROJECT, ts=1_788_000_000 + i * 90_000,
                      text=PROMPT)
        for i in range(3)
    ]
    return replay.run(prompts, build_index(vault), replay.rule_facts(vault), reflex_cfg=_CFG)


def _counts(result: replay.Replay) -> tuple:
    return (sorted((i.slug, i.bucket) for i in result.injections),
            sorted(result.silence.items()), result.fired_prompts)


def test_replay_counts_do_not_move_while_auto_retire_is_off(vault):
    before = _counts(_replay(vault))
    assert any(slug == fx.OLD for slug, _b in before[0])
    rec = fx.record(vault)

    run = R.auto_retire(vault, [rec], cfg={"friction": {}})

    assert run.results == []
    assert _counts(_replay(vault)) == before


def test_replay_loses_a_retired_rule_through_the_index_alone(vault):
    rec = fx.record(vault)
    run = R.auto_retire(vault, [rec], cfg={"friction": {"autoRetire": True}})
    assert run.retired

    after = _counts(_replay(vault))

    assert all(slug != fx.OLD for slug, _b in after[0])
