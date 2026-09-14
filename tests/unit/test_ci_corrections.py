"""The second source of corrections: a red CI run followed by a green one (#272).

The vault's only correction channel is the user typing one. A red job is a
correction too, with better evidence than most human ones: the assertion text
is exact, and the push that turned it green names the fix. These tests pin the
parsing of both halves and the evidence class that keeps a log quote from ever
being read back as something the user said.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core import ci_corrections as cc


# --- the red half: pulling the failure out of a log ----------------------------

def test_a_github_log_line_yields_the_test_and_its_assertion():
    """The real #176 failure, verbatim from run 34801761895's log-failed output.

    Three tab-separated columns, an ISO timestamp, then pytest's summary line.
    """
    log = (
        "windows-latest / py3.11\tUNKNOWN STEP\t2026-09-14T03:14:28.5485411Z "
        "FAILED tests/unit/test_sessions_detector.py::test_a_first_sighting_mid_write"
        " - assert 312 == 310\n"
    )
    failures = cc.parse_failures(log)

    assert len(failures) == 1
    assert failures[0].test == (
        "tests/unit/test_sessions_detector.py::test_a_first_sighting_mid_write"
    )
    assert failures[0].message == "assert 312 == 310"
    assert failures[0].file == "tests/unit/test_sessions_detector.py"


def test_the_runner_prefix_is_optional_so_a_local_pytest_run_parses_too():
    """`pytest` on the maintainer's machine prints the same line, unadorned.

    This is the path that needs no network at all.
    """
    log = (
        "=========================== short test summary info ====================\n"
        "FAILED tests/unit/test_land_command.py::test_a_red_suite_merges_nothing"
        " - assert 1 == 0\n"
        "======================= 1 failed, 2851 passed in 120.68s ===============\n"
    )
    failures = cc.parse_failures(log)

    assert [f.test for f in failures] == [
        "tests/unit/test_land_command.py::test_a_red_suite_merges_nothing"
    ]
    assert failures[0].message == "assert 1 == 0"


def test_an_error_line_counts_as_a_failure():
    """A collection ERROR is a red run the same way a FAILED is.

    Verbatim from run 34732645780 — the py3.8 `int | str` break.
    """
    log = (
        "ERROR tests/unit/test_dispatch_command.py - TypeError: unsupported "
        "operand type(s) for |: 'type' and 'type'\n"
    )
    failures = cc.parse_failures(log)

    assert len(failures) == 1
    assert failures[0].message.startswith("TypeError: unsupported operand type(s)")


def test_the_same_failure_on_several_runners_collapses_to_one():
    """A matrix of five runners reports one bug five times, not five bugs."""
    line = (
        "FAILED tests/unit/test_text_io_encoding.py::test_fixture_reproduces"
        " - Failed: DID NOT RAISE UnicodeDecodeError"
    )
    log = "\n".join([
        f"macos-latest / py3.9\tSTEP\t2026-09-14T03:04:30.8307840Z {line}",
        f"macos-latest / py3.10\tSTEP\t2026-09-14T03:04:31.8307840Z {line}",
        f"ubuntu-latest / py3.8\tSTEP\t2026-09-14T03:04:32.8307840Z {line}",
    ])

    assert len(cc.parse_failures(log)) == 1


def test_a_log_with_no_failure_line_yields_nothing():
    """Two of the thirteen red branches never reached pytest.

    A run that failed at install or lint has no assertion to quote, so it makes
    no rule. Guessing one is exactly the fabrication the evidence class exists
    to prevent.
    """
    log = (
        "ubuntu-latest / py3.8\tSTEP\t2026-09-14T03:10:50.9Z ##[error]Process "
        "completed with exit code 1.\n"
    )
    assert cc.parse_failures(log) == []


def test_a_traceback_line_is_not_mistaken_for_a_summary_line():
    """`FAILED` must start the line; prose mentioning it is not a result."""
    log = "    print('FAILED tests/unit/test_x.py::test_y - assert 1 == 0')\n"
    assert cc.parse_failures(log) == []


# --- pairing red with green ----------------------------------------------------

def test_a_red_run_followed_by_green_on_the_same_branch_is_a_pair():
    runs = [
        {"conclusion": "failure", "headSha": "aaa1111", "createdAt": "2026-09-14T03:11:00Z"},
        {"conclusion": "success", "headSha": "bbb2222", "createdAt": "2026-09-14T03:19:00Z"},
    ]
    pair = cc.pair_runs(runs)

    assert pair is not None
    assert pair.red_sha == "aaa1111"
    assert pair.green_sha == "bbb2222"


def test_a_red_run_with_no_green_successor_is_not_a_pair():
    """The issue is explicit: a red run alone teaches nothing.

    Until a push turns it green, nobody knows what the fix is — or whether the
    test itself was wrong.
    """
    runs = [
        {"conclusion": "success", "headSha": "aaa1111", "createdAt": "2026-09-14T01:00:00Z"},
        {"conclusion": "failure", "headSha": "bbb2222", "createdAt": "2026-09-14T02:00:00Z"},
    ]
    assert cc.pair_runs(runs) is None


def test_the_last_red_before_the_green_is_the_one_that_teaches():
    """fix/issue-255 went red three times before it went green.

    The fix that mattered is the diff against the final red, not the first.
    """
    runs = [
        {"conclusion": "failure", "headSha": "r1", "createdAt": "2026-09-14T03:02:00Z"},
        {"conclusion": "failure", "headSha": "r2", "createdAt": "2026-09-14T03:04:00Z"},
        {"conclusion": "failure", "headSha": "r3", "createdAt": "2026-09-14T03:10:00Z"},
        {"conclusion": "success", "headSha": "g1", "createdAt": "2026-09-14T03:15:00Z"},
    ]
    pair = cc.pair_runs(runs)

    assert pair.red_sha == "r3"
    assert pair.green_sha == "g1"


def test_runs_out_of_order_are_sorted_before_pairing():
    runs = [
        {"conclusion": "success", "headSha": "g1", "createdAt": "2026-09-14T03:15:00Z"},
        {"conclusion": "failure", "headSha": "r1", "createdAt": "2026-09-14T03:02:00Z"},
    ]
    pair = cc.pair_runs(runs)

    assert pair.red_sha == "r1" and pair.green_sha == "g1"


# --- the evidence class --------------------------------------------------------

def test_verified_ci_is_not_the_value_every_you_said_surface_looks_for():
    """The whole reason this is a separate literal.

    `share/format.VERIFIED_ELSEWHERE` established the pattern: every surface
    that speaks in the user's voice compares `== "verified"`, so a new value is
    excluded from all of them with no change at those call sites. This test is
    the executable form of that claim — if someone loosens one of those
    comparisons to `startswith("verified")`, this fails.
    """
    assert cc.VERIFIED_CI != "verified"
    assert cc.VERIFIED_CI.startswith("verified")


@pytest.mark.parametrize("surface", [
    "hooks/session_start.py",
    "cli/commands/learn.py",
    "cli/commands/status.py",
])
def test_no_you_said_surface_would_show_a_ci_quote(surface):
    """Read the source and assert the comparison is still an equality.

    A grep proves a string is absent, never a behaviour — so this asserts on
    the shape of the comparison these three surfaces use, which is what makes
    `verified-ci` invisible to them.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "mnemo" / surface
    text = src.read_text(encoding="utf-8")

    assert '"verified"' in text
    assert "startswith(\"verified\")" not in text


def test_a_ci_page_carries_the_log_quote_and_the_run_it_came_from():
    failure = cc.Failure(
        test="tests/unit/test_docs_accuracy.py::test_every_referenced_cli_command_exists",
        message="UnicodeDecodeError: 'charmap' codec can't decode byte 0x90",
        file="tests/unit/test_docs_accuracy.py",
    )
    page = cc.to_page(
        failure,
        fixed_files=["src/mnemo/core/paths.py"],
        branch="fix/issue-243",
        red_sha="c4e8250",
        green_sha="906d8d3",
        run_url="https://github.com/x/mnemo/actions/runs/34795191987",
    )

    assert page.confidence == cc.VERIFIED_CI
    assert page.evidence["quote"] == failure.message
    assert page.evidence["source"] == (
        "https://github.com/x/mnemo/actions/runs/34795191987"
    )
    assert "c4e8250" in page.evidence["red"]
    assert "906d8d3" in page.evidence["green"]


def test_a_ci_page_is_a_reference_not_feedback():
    """`shared/feedback/` is where "the user told me so" lives.

    A CI rule is real, checkable knowledge that nobody typed — the same slot
    `verify_page` puts an unverified feedback page into.
    """
    page = cc.to_page(
        cc.Failure(test="t.py::t", message="assert 1 == 0", file="t.py"),
        fixed_files=["src/mnemo/core/x.py"],
        branch="b", red_sha="r", green_sha="g", run_url="u",
    )
    assert page.type == "reference"


def test_the_extractor_gate_never_sees_a_ci_page():
    """The gate that would destroy it.

    `evidence.verify_page` demotes any `feedback` page whose quote is absent
    from a briefing's `## Corrections` and sets `evidence=None`. A CI quote
    lives in a log, so it can never pass that bar. Routing a CI page through
    the gate would strip exactly the evidence this feature exists to keep —
    hence `reference`, which `verify_page` only ever promotes, never strips.
    """
    from mnemo.core.extract.evidence import verify_page

    page = cc.to_page(
        cc.Failure(test="t.py::t", message="assert 312 == 310", file="t.py"),
        fixed_files=["src/mnemo/core/sessions/detector.py"],
        branch="b", red_sha="r", green_sha="g", run_url="u",
    )
    after = verify_page(page, Path("/nonexistent-vault"))

    assert after.evidence is not None
    assert after.evidence["quote"] == "assert 312 == 310"
    assert after.confidence == cc.VERIFIED_CI


# --- the origin axis in replay -------------------------------------------------

def test_origin_of_a_user_verified_page_is_user():
    assert cc.origin_of("verified") == cc.ORIGIN_USER


def test_origin_of_a_ci_page_is_ci():
    assert cc.origin_of(cc.VERIFIED_CI) == cc.ORIGIN_CI


def test_an_unbacked_page_has_no_origin():
    assert cc.origin_of("inferred") is None


# --- the origin split must never lose a rule -----------------------------------

def test_the_origin_halves_sum_to_the_whole():
    """The invariant that makes the split trustworthy.

    `user + ci` must equal `carried_correction_backed` at every level. An
    injection stamped before the origin axis existed carries `origin=None`;
    counting it as neither would quietly under-report the total, which is how a
    measurement lies without ever failing a test.
    """
    from datetime import datetime, timedelta, timezone

    from mnemo.core.reflex import replay as R

    at = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def prompt(ts):
        return R.Prompt(session_id="s1", project="alpha", ts=ts.timestamp(), text="x")

    def inj(ts, slug, origin):
        return R.Injection(
            session_id="s1", project="alpha", ts=ts.timestamp(), slug=slug,
            bucket=R.CARRIED, correction_backed=True, gate_verified=True, origin=origin,
        )

    prompts = [prompt(at), prompt(at + timedelta(minutes=1)), prompt(at + timedelta(minutes=2))]
    injections = [
        inj(at, "from-user", cc.ORIGIN_USER),
        inj(at + timedelta(minutes=1), "from-ci", cc.ORIGIN_CI),
        inj(at + timedelta(minutes=2), "legacy", None),  # predates the axis
    ]
    replay = R.Replay(
        prompts=prompts, injections=injections, silence={}, fired_prompts=3,
    )

    report = R.aggregate(replay, vault_rules=10, correction_backed_rules=3, gate_verified_rules=3)

    for level in ("prompts", "injections"):
        split = report[level]["carried_correction_backed_by_origin"]
        assert split[cc.ORIGIN_USER] + split[cc.ORIGIN_CI] == \
            report[level]["carried_correction_backed"], level

    assert report["injections"]["carried_correction_backed_by_origin"] == {"user": 2, "ci": 1}


# --- the network gate ----------------------------------------------------------

RED = "FAILED tests/unit/test_x.py::test_y - assert 1 == 0\n"


def test_the_github_path_does_nothing_while_the_network_is_off():
    """`gh run view` leaves the machine, so it sits behind the one switch.

    Off is the default (`core/config.py`: autopilot.network.enabled=False), and
    the fetchers must not even be called — a gate that fetches first and
    discards after is not a gate.
    """
    called = []

    def fetch_runs(branch):
        called.append(branch)
        return []

    pages = cc.rules_from_github(
        "fix/issue-272",
        fetch_runs=fetch_runs,
        cfg={"autopilot": {"network": {"enabled": False}}},
    )

    assert pages == []
    assert called == [], "the gate must refuse before anything reaches the network"


def test_the_github_path_extracts_when_the_network_is_on():
    runs = [
        {"conclusion": "failure", "headSha": "r1", "databaseId": "99",
         "createdAt": "2026-09-14T03:02:00Z", "headBranch": "fix/issue-272"},
        {"conclusion": "success", "headSha": "g1", "databaseId": "100",
         "createdAt": "2026-09-14T03:15:00Z", "headBranch": "fix/issue-272"},
    ]
    pages = cc.rules_from_github(
        "fix/issue-272",
        run_json=runs,
        fetch_log=lambda run_id: RED,
        changed_files=lambda a, b: ["src/mnemo/core/x.py"],
        cfg={"autopilot": {"network": {"enabled": True}}},
    )

    assert len(pages) == 1
    assert pages[0].confidence == cc.VERIFIED_CI
    assert pages[0].evidence["quote"] == "assert 1 == 0"


def test_the_local_path_needs_no_network_and_no_gate():
    """The maintainer's own loop: two pytest logs, nothing leaves the machine."""
    pages = cc.rules_from_local_run(
        red_log=RED, green_log="2851 passed\n", fixed_files=["src/mnemo/core/x.py"],
    )

    assert len(pages) == 1
    assert pages[0].evidence["quote"] == "assert 1 == 0"


def test_a_test_still_failing_in_the_green_log_mints_no_rule():
    """A flaky test that happens to pass elsewhere taught nothing.

    Only a failure that the later run actually fixed is a correction.
    """
    pages = cc.rules_from_local_run(
        red_log=RED, green_log=RED, fixed_files=["src/mnemo/core/x.py"],
    )

    assert pages == []


def test_the_most_carried_list_never_calls_a_ci_rule_your_words():
    """The false attribution this whole evidence class exists to prevent.

    A CI rule is gate-verified — its quote is checked by the run that printed
    it — so the mark keyed on `gate_verified` alone labelled it "✓ your words".
    Nobody typed `assert 312 == 310`.
    """
    from datetime import datetime, timedelta, timezone

    from mnemo.core.reflex import replay as R

    at = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def prompt(i):
        return R.Prompt(session_id="s1", project="alpha",
                        ts=(at + timedelta(minutes=i)).timestamp(), text="x")

    def inj(i, slug, origin):
        return R.Injection(
            session_id="s1", project="alpha", ts=(at + timedelta(minutes=i)).timestamp(),
            slug=slug, bucket=R.CARRIED, correction_backed=True, gate_verified=True,
            origin=origin,
        )

    replay = R.Replay(
        prompts=[prompt(0), prompt(1)],
        injections=[inj(0, "human-rule", cc.ORIGIN_USER), inj(1, "ci-rule", cc.ORIGIN_CI)],
        silence={}, fired_prompts=2,
    )
    report = R.aggregate(replay, vault_rules=10, correction_backed_rules=2, gate_verified_rules=2)
    text = R.format_report(report)

    assert "ci-rule  ✓ CI said" in text
    assert "ci-rule  ✓ your words" not in text
    assert "human-rule  ✓ your words" in text
