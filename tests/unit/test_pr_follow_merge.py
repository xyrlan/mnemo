"""The pr-follow watcher merging a child's PR once it is ready (#638).

Everything that talks to the world is injected, as in ``test_pr_follow.py``:
``git`` and ``gh`` through :class:`World`, which answers each call by what it
asks for and records every argv, so a test can say what reached ``gh pr
merge`` and that nothing did. Nothing here starts a process or reads a real
config.
"""
from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from mnemo.core import config as config_mod
from mnemo.core import dispatch, landing
from mnemo.core.sessions import grants, pr_follow, report_card

FULL = "594436f2-a282-4a1c-b04a-08441296047e"
SHORT = FULL[:8]
PARENT = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
HEAD = "abc123def4567890"
REPO = "o/r"
URL = f"https://github.com/{REPO}/pull/12"
T0 = 1_790_000_000.0
ON = {"dispatch": {"autoMerge": {"repos": [REPO]}}}
S = pr_follow.settings({})


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _closing(*issues: int) -> List[Dict[str, Any]]:
    return [{"number": n, "repository": {"name": "r", "owner": {"login": "o"}}}
            for n in issues]


class World:
    """``git`` and ``gh`` for one child's PR, answered by what the call asks."""

    def __init__(self, *, state: str = "OPEN", draft: bool = False,
                 mergeable: str = "MERGEABLE", buckets: Sequence[str] = ("pass", "pass"),
                 checks_answer: Optional[Tuple[int, str, str]] = None,
                 comments: Sequence[dict] = (), reviews: Sequence[dict] = (),
                 review_decision: str = "", local_head: str = HEAD,
                 pr_head: str = HEAD, dirty: str = "",
                 others: Sequence[dict] = (), open_answer: Optional[Tuple[int, str, str]] = None,
                 merge: Tuple[int, str, str] = (0, "", ""), after: str = "MERGED",
                 reread_head: Optional[str] = None) -> None:
        self.state, self.draft, self.mergeable = state, draft, mergeable
        self.buckets, self.checks_answer = list(buckets), checks_answer
        self.comments, self.reviews = list(comments), list(reviews)
        self.review_decision = review_decision
        self.local_head, self.pr_head, self.dirty = local_head, pr_head, dirty
        self.others, self.open_answer = list(others), open_answer
        self.merge, self.after, self.reread_head = merge, after, reread_head
        self.calls: List[Tuple[str, ...]] = []

    def _row(self, head: Optional[str] = None) -> Dict[str, Any]:
        return {"number": 12, "url": URL, "state": self.state, "isDraft": self.draft,
                "additions": 1, "deletions": 1, "changedFiles": 1,
                "headRefOid": head or self.pr_head, "mergeable": self.mergeable}

    def merges(self) -> List[Tuple[str, ...]]:
        return [c for c in self.calls if c[:3] == ("gh", "pr", "merge")]

    def __call__(self, argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        call = tuple(argv)
        self.calls.append(call)
        if call[:4] == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return 0, "fix/issue-7\n", ""
        if call[:3] == ("git", "status", "--porcelain"):
            return 0, self.dirty, ""
        if call[:3] == ("git", "rev-parse", "HEAD"):
            return 0, self.local_head + "\n", ""
        if call[:2] == ("git", "rev-list"):
            return 0, "0\n", ""
        if call[:3] == ("gh", "pr", "list"):
            if "--head" in call:
                return 0, json.dumps([self._row()]), ""
            if self.open_answer is not None:
                return self.open_answer
            mine = {"number": 12, "headRefName": "fix/issue-7",
                    "closingIssuesReferences": _closing(7)}
            rows = ([mine] if self.state == "OPEN" else []) + self.others
            return 0, json.dumps(rows), ""
        if call[:3] == ("gh", "pr", "checks"):
            if self.checks_answer is not None:
                return self.checks_answer
            return 0, json.dumps([{"name": f"job{i}", "bucket": b}
                                  for i, b in enumerate(self.buckets)]), ""
        if call[:3] == ("gh", "pr", "view"):
            fields = call[call.index("--json") + 1]
            if "comments" in fields:
                return 0, json.dumps({
                    "mergeable": self.mergeable, "comments": self.comments,
                    "reviews": self.reviews, "reviewDecision": self.review_decision,
                }), ""
            return 0, json.dumps(self._row(self.reread_head)), ""
        if call[:3] == ("gh", "pr", "merge"):
            if self.merge[0] == 0:
                self.state = self.after
            return self.merge
        return 1, "", "unexpected call"


@dataclass
class Row:
    short_id: str = SHORT
    state: str = "done"
    tempo: Optional[str] = None

    @property
    def is_done(self) -> bool:
        return self.state in ("done", "stopped")

    @property
    def is_blocked(self) -> bool:
        return self.tempo == "blocked"


class Told:
    def __init__(self) -> None:
        self.sent: List[Tuple[Optional[str], str]] = []

    def __call__(self, vault_root, parent, text) -> bool:
        self.sent.append((parent, text))
        return True


class Waker:
    def __init__(self) -> None:
        self.calls: List[dict] = []

    def __call__(self, session_id: str, **kw) -> Optional[str]:
        self.calls.append({"session_id": session_id, **kw})
        return None


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    path = tmp_path / "app-wt-7"
    path.mkdir()
    return path


def _follow(vault: Path, tree: Path, *, at: float = T0) -> None:
    assert pr_follow.register(vault, short_id=SHORT, session_id=FULL, cwd=str(tree),
                              parent=PARENT, now=at)


def _entry(vault: Path) -> dict:
    return pr_follow.load_ledger(vault)["children"][SHORT]


def _sweep(vault: Path, run: World, *, now: float = T0 + 600, roster=(Row(),),
           waker=None, told=None, cfg=ON, **kw):
    return pr_follow.sweep(
        cfg, vault_root=vault, now=now, run=run, wake_fn=waker or Waker(),
        reader=lambda: list(roster), notify=told or Told(), announce=False, **kw)


# ---------------------------------------------------------------------------
# the pure decision
# ---------------------------------------------------------------------------


def _card(*, buckets: Optional[Sequence[str]] = ("pass", "pass"), state: str = "OPEN",
          draft: bool = False, tree_seen: bool = True, clean: bool = True,
          unpushed: bool = False, target: object = 7) -> report_card.Card:
    card = report_card.Card(short_id=SHORT, target=target)
    card.pr = report_card.PR(number=12, url=URL, state=state, draft=draft, head=HEAD)
    card.tree_seen, card.clean, card.unpushed = tree_seen, clean, unpushed
    if buckets is None:
        card.checks = None
    else:
        card.checks = {}
        for b in buckets:
            card.checks[b] = card.checks.get(b, 0) + 1
        card.failing = [f"job{i}" for i, b in enumerate(buckets) if b == "fail"]
    return card


def _data(mergeable: str = "MERGEABLE", **more: Any) -> Dict[str, Any]:
    return {"mergeable": mergeable, "comments": [], "reviews": [], **more}


def _facts(**kw: Any) -> "pr_follow.MergeFacts":
    return pr_follow.MergeFacts(**{"on": True, "siblings": [], **kw})


def _decide(card=None, data=None, *, session: Any = Row(), facts=None,
            entry: Optional[dict] = None, now: float = T0 + 600) -> "pr_follow.Decision":
    entry = {"since": T0, "ended_at": T0, "attempts": [], **(entry or {})}
    return pr_follow.decide(
        entry, card or _card(), _data() if data is None else data, session,
        now=now, s=S, merge=_facts() if facts is None else facts)


def test_a_ready_pr_is_merged_when_the_switch_is_on() -> None:
    assert _decide().action == "merge"


def test_the_switch_off_decides_what_it_decided_before() -> None:
    off = pr_follow.MergeFacts(on=False, siblings=[])
    assert _decide(facts=off).action == "wait"
    entry = {"since": T0, "ended_at": T0, "attempts": []}
    # No facts at all is every caller that existed before #638.
    assert pr_follow.decide(entry, _card(), _data(), Row(), now=T0 + 600, s=S).action == "wait"


# -- item 2: what "ready" asks, clause by clause ----------------------------


def test_a_merged_or_closed_pr_is_not_merged() -> None:
    assert _decide(_card(state="MERGED")).action == "close"
    assert _decide(_card(state="CLOSED")).action == "close"


def test_a_draft_is_not_merged() -> None:
    assert _decide(_card(draft=True)).action == "wait"


def test_mergeable_unknown_is_not_mergeable() -> None:
    assert _decide(data=_data("UNKNOWN")).action == "wait"
    assert _decide(data=_data("")).action == "wait"


def test_a_conflict_wakes_the_child_and_merges_nothing() -> None:
    decision = _decide(data=_data("CONFLICTING"))
    assert (decision.action, decision.events) == ("wake", ["conflict"])


def test_a_pr_whose_activity_could_not_be_read_is_not_merged() -> None:
    entry = {"since": T0, "ended_at": T0, "attempts": []}
    decision = pr_follow.decide(entry, _card(), None, Row(), now=T0 + 600, s=S,
                                merge=_facts())
    assert decision.action == "wait"


def test_no_checks_at_all_is_never_merged() -> None:
    assert _decide(_card(buckets=())).action == "wait"


def test_checks_that_could_not_be_read_are_not_green() -> None:
    assert _decide(_card(buckets=None)).action == "wait"


@pytest.mark.parametrize("bucket", ["pending", "skipping", "cancel", "something-new"])
def test_a_check_that_did_not_pass_is_not_green(bucket: str) -> None:
    assert _decide(_card(buckets=("pass", bucket))).action == "wait"


def test_a_failing_check_wakes_the_child_and_merges_nothing() -> None:
    decision = _decide(_card(buckets=("pass", "fail")))
    assert (decision.action, decision.events) == ("wake", ["ci-red"])


def test_a_check_that_never_ran_is_not_green() -> None:
    """#575's case: red only by a job GitHub never started wakes nobody, and
    it is not a merge either."""
    card = _card(buckets=("pass", "fail"))
    entry = {"since": T0, "ended_at": T0, "attempts": []}
    decision = pr_follow.decide(entry, card, _data(), Row(), now=T0 + 600, s=S,
                                kinds={"job1": pr_follow.NOT_RUN}, merge=_facts())
    assert decision.action == "wait"


# -- item 4: what it never merges, line by line -----------------------------


def test_a_twins_pr_is_never_merged() -> None:
    decision = _decide(facts=_facts(twin=True))
    assert (decision.action, decision.reason) == ("wait", "twin")


def test_another_open_pr_for_the_same_issue_holds_the_merge() -> None:
    decision = _decide(facts=_facts(siblings=[31]))
    assert (decision.action, decision.reason) == ("wait", "siblings")


def test_siblings_that_could_not_be_read_hold_the_merge() -> None:
    decision = _decide(facts=_facts(siblings=None))
    assert (decision.action, decision.reason) == ("wait", "siblings-unread")


def test_a_contract_piece_is_never_merged() -> None:
    decision = _decide(_card(target="parser"))
    assert (decision.action, decision.reason) == ("wait", "piece")
    # A tree dispatch did not name targets nothing mnemo can vouch for.
    assert _decide(_card(target=None)).action == "wait"


def test_a_head_that_is_not_the_trees_head_is_never_merged() -> None:
    decision = _decide(_card(unpushed=True))
    assert (decision.action, decision.reason) == ("wait", "branch-moved")


def test_a_dirty_tree_is_never_merged() -> None:
    decision = _decide(_card(clean=False))
    assert (decision.action, decision.reason) == ("wait", "tree-dirty")


def test_a_gone_tree_is_never_merged() -> None:
    decision = _decide(_card(tree_seen=False))
    assert (decision.action, decision.reason) == ("wait", "tree-gone")


def test_an_unanswered_trusted_comment_wakes_the_child_and_merges_nothing() -> None:
    comment = {"authorAssociation": "OWNER", "createdAt": _iso(T0 + 600)}
    decision = _decide(data=_data(comments=[comment]))
    assert (decision.action, decision.events) == ("wake", ["review"])


def test_an_unanswered_trusted_review_holds_the_merge_on_its_own() -> None:
    """The hold does not lean on the wake coming first."""
    review = {"authorAssociation": "MEMBER", "state": "CHANGES_REQUESTED",
              "submittedAt": _iso(T0 + 600)}
    entry = {"since": T0, "ended_at": T0, "attempts": []}
    assert pr_follow.merge_hold(entry, _card(), _data(reviews=[review]), Row(),
                                _facts()) == "review"


def test_a_strangers_comment_does_not_hold_the_merge() -> None:
    comment = {"authorAssociation": "NONE", "createdAt": _iso(T0 + 600)}
    assert _decide(data=_data(comments=[comment])).action == "merge"


def test_changes_requested_still_standing_holds_the_merge() -> None:
    decision = _decide(data=_data(reviewDecision="CHANGES_REQUESTED"))
    assert (decision.action, decision.reason) == ("wait", "changes-requested")


@pytest.mark.parametrize("session", [Row(state="working"), Row(tempo="blocked")])
def test_a_child_still_running_or_blocked_holds_the_merge(session: Row) -> None:
    decision = _decide(session=session)
    assert (decision.action, decision.reason) == ("wait", "child-working")


def test_a_head_gh_refused_is_not_tried_again() -> None:
    refused = {"merge": {"head": HEAD, "outcome": "refused"}}
    assert _decide(entry=refused).reason == "tried"
    other = {"merge": {"head": "an-older-head", "outcome": "refused"}}
    assert _decide(entry=other).action == "merge"


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------


def test_the_switch_is_off_by_default() -> None:
    cfg = config_mod.load_config(Path("no-such-dir") / "mnemo.config.json")
    assert cfg["dispatch"]["autoMerge"] == {"repos": []}
    assert not pr_follow.merge_on(cfg, REPO)


def test_the_switch_is_per_repo() -> None:
    assert pr_follow.merge_on(ON, REPO)
    assert pr_follow.merge_on(ON, "O/R")
    assert not pr_follow.merge_on(ON, "o/other")
    assert not pr_follow.merge_on(ON, None)


@pytest.mark.parametrize("raw", [True, "o/r", {"o/r": True}, [True], None, 1])
def test_a_switch_nobody_meant_is_off(raw: Any) -> None:
    assert not pr_follow.merge_on({"dispatch": {"autoMerge": {"repos": raw}}}, REPO)
    assert not pr_follow.merge_on({"dispatch": {"autoMerge": raw}}, REPO)


def test_a_config_inside_the_childs_tree_cannot_turn_it_on(tree: Path, tmp_path: Path) -> None:
    """The branch being merged could carry that file."""
    inside = tree / ".mnemo" / "mnemo.config.json"
    assert not pr_follow.merge_on(ON, REPO, config_path=inside, tree=str(tree))
    # Nor one inside any other tree dispatch made: that is a branch too.
    other = tmp_path / "app-wt-9" / ".mnemo" / "mnemo.config.json"
    assert not pr_follow.merge_on(ON, REPO, config_path=other, tree=str(tree))
    outside = tmp_path / "vault" / "mnemo.config.json"
    assert pr_follow.merge_on(ON, REPO, config_path=outside, tree=str(tree))


def test_the_watcher_reads_the_switch_from_the_file_each_pass(vault, tree) -> None:
    """A watcher lives for a day; turning the switch off must not wait for it."""
    path = vault / "mnemo.config.json"
    path.write_text(json.dumps({"dispatch": {"autoMerge": {"repos": []}}}), encoding="utf-8")
    _follow(vault, tree)
    world = World()
    _sweep(vault, world, cfg=ON, config_path=path)
    assert world.merges() == []


def test_a_config_file_in_the_tree_merges_nothing(vault, tree) -> None:
    path = tree / ".mnemo" / "mnemo.config.json"
    path.parent.mkdir()
    path.write_text(json.dumps(ON), encoding="utf-8")
    _follow(vault, tree)
    world = World()
    _sweep(vault, world, cfg=ON, config_path=path)
    assert world.merges() == []


def test_nothing_on_the_ledger_can_turn_it_on(vault, tree) -> None:
    """The ledger is written by a hook any session fires."""
    _follow(vault, tree)

    def forge(d: Dict[str, Any]) -> None:
        d["children"][SHORT].update({"autoMerge": True, "merge_on": True})

    pr_follow._update(vault, forge)
    world = World()
    _sweep(vault, world, cfg={})
    assert world.merges() == []


def test_merge_is_still_refused_as_a_grant_and_the_prompt_still_forbids_it() -> None:
    with pytest.raises(grants.GrantError):
        grants.parse("merge")
    assert "merge" not in grants.VALUES
    prompt = dispatch.build_prompt(7, title="t", body="b", may=("push", "pr"))
    assert "Do not merge" in prompt


# ---------------------------------------------------------------------------
# the pass, with the switch off
# ---------------------------------------------------------------------------


def test_with_the_switch_off_a_ready_pr_is_left_exactly_as_before(vault, tree) -> None:
    _follow(vault, tree)
    off, told = World(), Told()
    report = _sweep(vault, off, cfg={}, told=told)
    assert off.merges() == []
    assert not [c for c in off.calls if c[:3] == ("gh", "pr", "list") and "--head" not in c]
    assert told.sent == [] and report.closed == [] and report.woken == []
    assert _entry(vault)["closed"] is None
    # And every call it made is a call it made before #638.
    view, = [c for c in off.calls if c[:3] == ("gh", "pr", "view")]
    assert view == ("gh", "pr", "view", URL, "--json", "mergeable,comments,reviews")


# ---------------------------------------------------------------------------
# the merge step, against a stubbed runner
# ---------------------------------------------------------------------------


def _pr() -> report_card.PR:
    return report_card.PR(number=12, url=URL, state="OPEN", head=HEAD, mergeable="MERGEABLE")


def test_the_method_is_the_one_mnemo_land_defaults_to() -> None:
    default = inspect.signature(landing.merge_prs).parameters["method"].default
    assert pr_follow.MERGE_METHOD == default == "squash"


def test_the_merge_pins_the_head_it_judged_and_never_passes_admin() -> None:
    world = World()
    result = pr_follow.merge_pr(_pr(), run=world)
    merge, = world.merges()
    assert merge == ("gh", "pr", "merge", URL, "--squash", "--match-head-commit", HEAD)
    assert "--admin" not in merge and "--auto" not in merge
    assert result.outcome == "merged"


def test_merged_is_said_only_when_github_says_it() -> None:
    world = World(after="OPEN")  # gh exited 0: a merge queue took it, say
    result = pr_follow.merge_pr(_pr(), run=world)
    assert len(world.merges()) == 1
    assert (result.outcome, result.state) == ("unconfirmed", "OPEN")
    # And the read that says so comes after the merge call.
    last = world.calls[-1]
    assert last[:3] == ("gh", "pr", "view") and world.calls[-2][:3] == ("gh", "pr", "merge")


def test_a_refusal_carries_ghs_own_words() -> None:
    words = "GraphQL: Pull request is not mergeable: the base branch policy prohibits the merge."
    result = pr_follow.merge_pr(_pr(), run=World(merge=(1, "", words + "\n")))
    assert (result.outcome, result.detail) == ("refused", words)


def test_a_head_that_moved_before_the_merge_is_not_merged() -> None:
    world = World(reread_head="someone-elses-push")
    result = pr_follow.merge_pr(_pr(), run=world)
    assert world.merges() == [] and result.outcome == "moved"


@pytest.mark.parametrize("change", [{"state": "CLOSED"}, {"draft": True},
                                    {"mergeable": "UNKNOWN"}, {"mergeable": "CONFLICTING"}])
def test_a_pr_that_changed_before_the_merge_is_not_merged(change: dict) -> None:
    world = World(**change)
    assert pr_follow.merge_pr(_pr(), run=world).outcome == "moved"
    assert world.merges() == []


# ---------------------------------------------------------------------------
# the pass, with the switch on
# ---------------------------------------------------------------------------


def test_a_ready_pr_is_merged_closed_and_told(vault, tree) -> None:
    _follow(vault, tree)
    world, told = World(), Told()
    report = _sweep(vault, world, told=told)
    merge, = world.merges()
    assert merge[-2:] == ("--match-head-commit", HEAD) and "--admin" not in merge
    entry = _entry(vault)
    assert (entry["closed"], entry["merged_by"]) == ("merged", "mnemo")
    assert entry["merge"]["head"] == HEAD and entry["merge"]["outcome"] == "merged"
    assert report.merged == [SHORT] and report.closed == [(SHORT, "merged")]
    (parent, text), = told.sent
    assert parent == PARENT
    assert 'event="follow-merged"' in text and 'state="merged"' in text
    assert "mnemo merged" in text and "GitHub reports it MERGED" in text
    assert "not your user speaking" in text


def test_a_merge_a_person_made_is_told_apart(vault, tree) -> None:
    _follow(vault, tree)
    told = Told()
    _sweep(vault, World(state="MERGED"), told=told)
    entry = _entry(vault)
    assert (entry["closed"], entry["merged_by"]) == ("merged", "other")
    assert told.sent == []


def test_a_refusal_is_handed_back_with_ghs_words_and_not_retried(vault, tree) -> None:
    _follow(vault, tree)
    words = "GraphQL: At least 1 approving review is required by reviewers with write access."
    world, told = World(merge=(1, "", words)), Told()
    report = _sweep(vault, world, told=told)
    (parent, text), = told.sent
    assert 'event="follow-merge-refused"' in text and words in text
    entry = _entry(vault)
    assert entry["closed"] is None and entry["merge"]["outcome"] == "refused"
    assert report.merged == [] and report.refused == [SHORT]
    # The next poll does not ask gh the same question about the same head.
    _sweep(vault, world, told=told, now=T0 + 1200)
    assert len(world.merges()) == 1 and len(told.sent) == 1
    # A new head — the child pushed — is a new question.
    world.pr_head = world.local_head = "fedcba9876543210"
    world.merge = (0, "", "")
    _sweep(vault, world, told=told, now=T0 + 1800)
    assert world.merges()[-1][-1] == "fedcba9876543210"
    assert _entry(vault)["merged_by"] == "mnemo"


def test_an_unconfirmed_merge_says_so_and_is_credited_once_github_shows_it(vault, tree) -> None:
    _follow(vault, tree)
    world, told = World(after="OPEN"), Told()
    _sweep(vault, world, told=told)
    (_, text), = told.sent
    assert 'event="follow-merge-unconfirmed"' in text and "MERGED" not in text.split("\n")[0]
    assert _entry(vault)["closed"] is None
    world.state = "MERGED"
    _sweep(vault, world, told=told, now=T0 + 1200)
    assert len(world.merges()) == 1
    assert _entry(vault)["merged_by"] == "mnemo"
    assert 'event="follow-merged"' in told.sent[-1][1]


def test_a_twin_is_held_and_the_parent_hears_it_once(vault, tmp_path) -> None:
    twin = tmp_path / "app-wt-7-3fa9c1"
    twin.mkdir()
    _follow(vault, twin)
    world, told = World(), Told()
    _sweep(vault, world, told=told)
    _sweep(vault, world, told=told, now=T0 + 1200)
    assert world.merges() == []
    (_, text), = told.sent
    assert 'event="follow-merge-held"' in text and "twin" in text
    assert _entry(vault)["closed"] is None


def test_a_piece_is_left_to_mnemo_land(vault, tmp_path) -> None:
    piece = tmp_path / "app-wt-c-parser"
    piece.mkdir()
    _follow(vault, piece)
    world, told = World(), Told()
    _sweep(vault, world, told=told)
    assert world.merges() == []
    assert "mnemo land" in told.sent[0][1]


def test_another_open_pr_closing_the_same_issue_holds_the_merge(vault, tree) -> None:
    _follow(vault, tree)
    other = {"number": 31, "headRefName": "someone/else", "closingIssuesReferences": _closing(7)}
    world, told = World(others=[other]), Told()
    _sweep(vault, world, told=told)
    assert world.merges() == []
    assert "#31" in told.sent[0][1]


def test_another_open_pr_from_the_issues_branch_holds_the_merge(vault, tree) -> None:
    _follow(vault, tree)
    twin = {"number": 32, "headRefName": "fix/issue-7-3fa9c1", "closingIssuesReferences": []}
    world = World(others=[twin])
    _sweep(vault, world)
    assert world.merges() == []


def test_an_open_pr_for_another_issue_holds_nothing(vault, tree) -> None:
    _follow(vault, tree)
    other = {"number": 33, "headRefName": "fix/issue-70", "closingIssuesReferences": _closing(70)}
    world = World(others=[other])
    _sweep(vault, world)
    assert len(world.merges()) == 1


def test_open_prs_that_could_not_be_listed_hold_the_merge(vault, tree) -> None:
    _follow(vault, tree)
    world, told = World(open_answer=(1, "", "HTTP 502")), Told()
    _sweep(vault, world, told=told)
    assert world.merges() == [] and told.sent == []
    assert _entry(vault)["closed"] is None


@pytest.mark.parametrize("change, word", [
    ({"local_head": "local-only-commit"}, "HEAD"),
    ({"dirty": " M src/x.py\n"}, "uncommitted"),
])
def test_a_tree_that_is_not_the_pr_holds_the_merge(vault, tree, change, word) -> None:
    _follow(vault, tree)
    world, told = World(**change), Told()
    _sweep(vault, world, told=told)
    assert world.merges() == []
    assert word in told.sent[0][1]


def test_a_gone_tree_holds_the_merge(vault, tree) -> None:
    _follow(vault, tree)
    tree.rmdir()
    world = World()
    _sweep(vault, world)
    assert world.merges() == []


def test_no_checks_merges_nothing_and_says_nothing(vault, tree) -> None:
    _follow(vault, tree)
    world, told = World(checks_answer=(1, "", "no checks reported on the 'x' branch")), Told()
    _sweep(vault, world, told=told)
    assert world.merges() == [] and told.sent == []


def test_red_checks_still_wake_the_child_with_the_switch_on(vault, tree) -> None:
    _follow(vault, tree)
    world, waker = World(buckets=("pass", "fail")), Waker()
    report = _sweep(vault, world, waker=waker)
    assert report.woken == [SHORT] and world.merges() == []
    assert waker.calls[0]["events"] == ["ci-red"]


def test_a_conflict_still_wakes_the_child_with_the_switch_on(vault, tree) -> None:
    _follow(vault, tree)
    world, waker = World(mergeable="CONFLICTING"), Waker()
    report = _sweep(vault, world, waker=waker)
    assert report.woken == [SHORT] and world.merges() == []


def test_a_trusted_comment_still_wakes_the_child_with_the_switch_on(vault, tree) -> None:
    _follow(vault, tree)
    comment = {"authorAssociation": "OWNER", "createdAt": _iso(T0 + 600)}
    world, waker = World(comments=[comment]), Waker()
    report = _sweep(vault, world, waker=waker)
    assert report.woken == [SHORT] and world.merges() == []


def test_a_woken_child_is_not_merged_under(vault, tree) -> None:
    """In the child's hands: it is about to push, and its stop reports."""
    _follow(vault, tree)
    world = World(buckets=("pass", "fail"))
    _sweep(vault, world)
    world.buckets = ["pass", "pass"]
    _sweep(vault, world, now=T0 + 1200)
    assert world.merges() == []


# ---------------------------------------------------------------------------
# what mnemo says
# ---------------------------------------------------------------------------


def test_the_cards_last_line_says_who_merges() -> None:
    card = _card()
    assert report_card.render(card).splitlines()[-1].startswith(
        "Review and merge stay with the maintainer")
    last = report_card.render(card, auto_merge=True).splitlines()[-1]
    assert "merge stay with the maintainer" not in last
    assert "mnemo merges" in last and "dispatch.autoMerge" in last


def test_will_merge_is_true_only_for_a_followed_issue_child(vault, tree, tmp_path) -> None:
    grants.record(SHORT, ("push", "pr"), vault_root=vault)
    assert pr_follow.will_merge(ON, vault_root=vault, short_id=SHORT, cwd=str(tree), url=URL)
    assert not pr_follow.will_merge({}, vault_root=vault, short_id=SHORT, cwd=str(tree), url=URL)
    assert not pr_follow.will_merge(ON, vault_root=vault, short_id="deadbeef",
                                    cwd=str(tree), url=URL)
    twin = tmp_path / "app-wt-7-3fa9c1"
    assert not pr_follow.will_merge(ON, vault_root=vault, short_id=SHORT, cwd=str(twin), url=URL)
    piece = tmp_path / "app-wt-c-parser"
    assert not pr_follow.will_merge(ON, vault_root=vault, short_id=SHORT, cwd=str(piece), url=URL)
    off = {"dispatch": {"autoMerge": {"repos": [REPO]}, "followPR": {"enabled": False}}}
    assert not pr_follow.will_merge(off, vault_root=vault, short_id=SHORT, cwd=str(tree), url=URL)


def test_the_queue_says_which_prs_mnemo_will_merge_and_which_it_merged(vault, tree) -> None:
    grants.record(SHORT, ("push", "pr"), vault_root=vault)
    _follow(vault, tree)
    assert pr_follow.queue_lines(ON, vault_root=vault) == []  # no PR read yet
    held = World(review_decision="CHANGES_REQUESTED")
    _sweep(vault, held)
    will, = pr_follow.queue_lines(ON, vault_root=vault)
    assert will.startswith("  mnemo merges when ready") and f"{SHORT} (PR #12)" in will
    assert pr_follow.queue_lines({}, vault_root=vault) == []
    assert pr_follow.queue_lines(ON, vault_root=vault, short_ids=["deadbeef"]) == []
    _sweep(vault, World(), now=T0 + 1200)
    made, = pr_follow.queue_lines(ON, vault_root=vault, now=T0 + 1300)
    assert made.startswith("  merged by mnemo") and f"{SHORT} (PR #12)" in made
    assert pr_follow.queue_lines(ON, vault_root=vault, now=T0 + 3 * 86400) == []


def test_the_queue_does_not_credit_mnemo_with_a_persons_merge(vault, tree) -> None:
    _follow(vault, tree)
    _sweep(vault, World(state="MERGED"))
    assert pr_follow.queue_lines(ON, vault_root=vault, now=T0 + 700) == []
