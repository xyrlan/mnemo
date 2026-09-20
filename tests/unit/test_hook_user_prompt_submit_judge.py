"""The judge as the reflex's gate, inside the hook (#412).

No test here reaches the network: the provider is a function patched into
:func:`mnemo.core.reflex.judge.ask`'s caller, and the one test that lets the
hook build its own client proves it stops at "no key".

What is pinned: the stage is off in the shipped defaults and a log row is
then byte-identical to the one written before it existed; with it on the
judge *replaces* the accept step rather than stacking on it, including on
prompts the gates silenced; every failure falls back to exactly what the
gates decided; and the session cap, the token pre-gate and a missing index
still cost no request.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from mnemo.core.reflex import judge
from mnemo.hooks import user_prompt_submit as hook

PROMPT = "How do I mock prisma in a jest test with typescript"


def _run_hook(payload: dict):
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        rc = hook.main()
    return rc, out.getvalue()


def _configure(vault, monkeypatch, *, judge_on=True, thresholds=None, **judge_cfg):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    reflex: dict = {"enabled": True}
    if thresholds:
        reflex["thresholds"] = thresholds
    if judge_on:
        reflex["judge"] = dict({"provider": "typesafe"}, **judge_cfg)
    (vault / "mnemo.config.json").write_text(
        json.dumps({"vaultRoot": str(vault), "reflex": reflex}), encoding="utf-8")


def _log(vault) -> list:
    path = vault / ".mnemo" / "reflex-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Recorder:
    """Stands in for :func:`judge.ask`, answering with a fixed verdict."""

    def __init__(self, picks, info=None):
        self.picks = picks
        self.info = info if info is not None else {
            "status": "ok", "asked": 0, "injected": 0, "ms": 3, "scores": []}
        self.calls = []

    def __call__(self, vault_root, *, prompt, slugs, chosen_settings, project=None,
                 client=None, read_text=None):
        self.calls.append({"prompt": prompt, "slugs": list(slugs),
                           "settings": dict(chosen_settings), "project": project})
        info = dict(self.info, asked=len(slugs),
                    injected=0 if self.picks is None else len(self.picks))
        return self.picks, info


@pytest.fixture
def stub(monkeypatch):
    def _install(picks, info=None):
        recorder = Recorder(picks, info)
        monkeypatch.setattr("mnemo.core.reflex.judge.ask", recorder)
        return recorder
    return _install


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(judge.DEFAULT_KEY_ENV, raising=False)


# --- off by default ----------------------------------------------------------

def test_with_the_stage_off_nothing_imports_it_and_the_row_is_unchanged(
        tmp_vault, monkeypatch, synthetic_index):
    """The default. A `judge` key on a row here would break every consumer
    that reads this log, and the import alone costs every prompt."""
    _configure(tmp_vault, monkeypatch, judge_on=False)
    synthetic_index(tmp_vault)

    def refuse(*args, **kwargs):
        raise AssertionError("the judge was consulted with the stage off")
    monkeypatch.setattr("mnemo.core.reflex.judge.ask", refuse)

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and "[[use-prisma-mock]]" in stdout
    entry = _log(tmp_vault)[-1]
    assert "judge" not in entry
    assert set(entry) == {"session_id", "project", "prompt_hash", "emitted",
                          "scores", "silence_reason", "candidates", "thresholds", "ts"}


def test_a_provider_this_version_does_not_know_is_off(
        tmp_vault, monkeypatch, synthetic_index):
    _configure(tmp_vault, monkeypatch, provider="typsafe")
    synthetic_index(tmp_vault)

    def refuse(*args, **kwargs):
        raise AssertionError("a typo turned the stage on")
    monkeypatch.setattr("mnemo.core.reflex.judge.ask", refuse)

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})
    assert rc == 0 and "[[use-prisma-mock]]" in stdout
    assert "judge" not in _log(tmp_vault)[-1]


# --- the judge decides -------------------------------------------------------

def test_the_judge_injects_what_it_picked(tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    asked = stub(["use-prisma-mock"])

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and "[[use-prisma-mock]]" in stdout
    assert asked.calls[0]["prompt"] == PROMPT
    entry = _log(tmp_vault)[-1]
    assert entry["emitted"] == ["use-prisma-mock"]
    assert entry["judge"]["status"] == "ok"


def test_the_pool_is_the_ranking_not_what_the_gates_accepted(
        tmp_vault, monkeypatch, synthetic_index, stub):
    """The measured design: the judge replaces the accept step. A prompt the
    floor silenced still gets asked about, which is where the on-point rules
    the shipped gate drops come back."""
    _configure(tmp_vault, monkeypatch, thresholds={"absoluteFloor": 999.0})
    synthetic_index(tmp_vault)
    asked = stub(["use-prisma-mock"])

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and "[[use-prisma-mock]]" in stdout
    assert asked.calls[0]["slugs"][0] == "use-prisma-mock"
    entry = _log(tmp_vault)[-1]
    # The gates would have silenced this one.
    assert entry["thresholds"]["absolute_floor"] == 999.0
    assert entry["emitted"] == ["use-prisma-mock"]


def test_the_pool_is_capped_at_candidates(tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch, candidates=2, thresholds={"absoluteFloor": 0.0})
    synthetic_index(tmp_vault)
    asked = stub([])

    _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert len(asked.calls[0]["slugs"]) <= 2


def test_nothing_over_the_bar_is_its_own_silence(
        tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    stub([])

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and stdout == ""
    entry = _log(tmp_vault)[-1]
    assert entry["silence_reason"] == "judge_none_relevant"
    assert entry["judge"]["status"] == "ok" and entry["judge"]["injected"] == 0


# --- every failure falls back ------------------------------------------------

@pytest.mark.parametrize("status", ["no_key", "error", "timeout"])
def test_a_failure_falls_back_to_what_the_gates_decided(
        tmp_vault, monkeypatch, synthetic_index, stub, status):
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    stub(None, {"status": status, "asked": 1, "injected": 0, "ms": 2, "scores": []})

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and "[[use-prisma-mock]]" in stdout
    entry = _log(tmp_vault)[-1]
    assert entry["emitted"] == ["use-prisma-mock"]
    assert entry["judge"]["status"] == status


def test_a_failure_on_a_prompt_the_gates_silenced_stays_silent(
        tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch, thresholds={"absoluteFloor": 999.0})
    synthetic_index(tmp_vault)
    stub(None, {"status": "error", "asked": 1, "injected": 0, "ms": 2, "scores": []})

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and stdout == ""
    entry = _log(tmp_vault)[-1]
    assert entry["silence_reason"] == "absolute_floor_fail"
    assert entry["judge"]["status"] == "error"


def test_the_hook_survives_a_judge_that_raises(
        tmp_vault, monkeypatch, synthetic_index):
    """`ask` promises never to raise. If it ever does, a prompt must still go
    through — the hook's fail-open is the last guarantee."""
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr("mnemo.core.reflex.judge.ask", explode)

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})
    assert rc == 0 and stdout == ""


def test_with_no_key_the_hook_never_reaches_the_network(
        tmp_vault, monkeypatch, synthetic_index):
    """The real `ask`, no stub: it must stop at the key and fall back."""
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    monkeypatch.setattr("mnemo.core.secrets.read", lambda *a, **k: None)

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and "[[use-prisma-mock]]" in stdout
    assert _log(tmp_vault)[-1]["judge"]["status"] == "no_key"


# --- what costs no request ---------------------------------------------------

def test_a_short_prompt_costs_no_request(tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    asked = stub(["use-prisma-mock"])

    _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": "ok"})

    assert asked.calls == []
    entry = _log(tmp_vault)[-1]
    assert entry["silence_reason"] == "below_min_tokens"
    assert "judge" not in entry


def test_a_missing_index_costs_no_request(tmp_vault, monkeypatch, stub):
    _configure(tmp_vault, monkeypatch)
    asked = stub(["anything"])

    _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert asked.calls == []
    assert _log(tmp_vault)[-1]["silence_reason"] == "index_missing"


def test_the_session_cap_costs_no_request(tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    asked = stub(["use-prisma-mock"])
    import time as _time

    from mnemo.core.mcp import session_state
    now = int(_time.time())
    for _ in range(10):
        session_state.bump_emission(tmp_vault, sid="s", kind="reflex", now_ts=now)

    _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert asked.calls == []
    assert _log(tmp_vault)[-1]["silence_reason"] == "session_cap_reached"


def test_a_rule_this_session_already_has_is_dropped_before_asking(
        tmp_vault, monkeypatch, synthetic_index, stub):
    """Paying to judge a rule that would be deduped away is money for nothing."""
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    asked = stub(["use-prisma-mock"])

    _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})
    first = len(asked.calls)
    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and stdout == ""
    assert "use-prisma-mock" not in asked.calls[-1]["slugs"] or len(asked.calls) == first
    entry = _log(tmp_vault)[-1]
    assert entry["silence_reason"] == "deduped"
    assert "judge" not in entry


def test_an_exported_rule_is_dropped_before_asking(
        tmp_vault, monkeypatch, synthetic_index, stub):
    _configure(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    asked = stub(["use-prisma-mock"])
    monkeypatch.setattr("mnemo.core.export.manifest.exported_slugs_for",
                        lambda *a, **k: {"use-prisma-mock"})

    rc, stdout = _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": PROMPT})

    assert rc == 0 and stdout == ""
    assert asked.calls == []
    entry = _log(tmp_vault)[-1]
    assert entry["silence_reason"] == "all_exported"
    assert "judge" not in entry
