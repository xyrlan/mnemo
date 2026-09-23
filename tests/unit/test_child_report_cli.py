"""``mnemo child-report`` (#426): post the card, then watch only when there is
something to wait for and someone to tell."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.runtime import main as cli_main
from mnemo.core.sessions import inbox, report_card as rc


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, watch=None) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"vaultRoot": str(vault)}
    if watch is not None:
        cfg["dispatch"] = {"watchChecksMinutes": watch}
    path = tmp_path / "mnemo.config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(path))
    return vault


def _card(state_checks) -> rc.Card:
    return rc.Card(
        short_id="c0da0f55", target=7,
        pr=rc.PR(number=12, url="https://github.com/o/r/pull/12", state="OPEN"),
        checks=state_checks, tree_seen=True,
    )


def _run(monkeypatch, card, *, delivered=True):
    sent, watched = [], []
    monkeypatch.setattr(rc, "gather", lambda *a, **k: card)
    why = "" if delivered else "parent not live: none of its 1 recorded address(es) is (pid 9)"
    monkeypatch.setattr(inbox, "deliver", lambda v, p, text: sent.append((p, text)) or why)
    monkeypatch.setattr(
        rc, "watch_checks",
        lambda c, **kw: watched.append(kw["minutes"]) or "settled",
    )
    rc_code = cli_main([
        "child-report", "c0da0f55", "--parent", "P1",
        "--cwd", "/x/app-wt-7", "--transcript", "/t/c.jsonl",
    ])
    return rc_code, sent, watched


def _rows(vault: Path):
    path = vault / ".mnemo" / rc.LOG_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_ready_card_is_posted_and_nothing_is_watched(tmp_path, monkeypatch) -> None:
    vault = _setup(tmp_path, monkeypatch)

    code, sent, watched = _run(monkeypatch, _card({"pass": 3}))

    assert code == 0
    assert [p for p, _ in sent] == ["P1"]
    assert 'state="ready"' in sent[0][1]
    assert watched == []
    assert [(r["event"], r["state"], r["pr"], r["delivered"]) for r in _rows(vault)] == [
        ("finished", "ready", 12, True),
    ]


def test_running_checks_are_watched_for_the_configured_minutes(tmp_path, monkeypatch) -> None:
    vault = _setup(tmp_path, monkeypatch, watch=12)

    _, sent, watched = _run(monkeypatch, _card({"pending": 2}))

    assert "watching up to 12 min" in sent[0][1]
    assert watched == [12]
    assert [r["event"] for r in _rows(vault)] == ["finished", "settled"]


@pytest.mark.parametrize("delivered, watch", [(False, 30), (True, 0)])
def test_no_watch_without_a_reader_or_without_a_budget(
    tmp_path, monkeypatch, delivered, watch,
) -> None:
    _setup(tmp_path, monkeypatch, watch=watch)

    _, _, watched = _run(monkeypatch, _card({"pending": 2}), delivered=delivered)

    assert watched == []


def test_a_crash_is_logged_not_raised(tmp_path, monkeypatch) -> None:
    vault = _setup(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(rc, "gather", boom)

    assert cli_main(["child-report", "c0da0f55", "--parent", "P1"]) == 1
    assert "child_report.cli" in (vault / ".errors.log").read_text(encoding="utf-8")


def test_an_undelivered_card_leaves_a_row_with_the_reason_and_an_error(
    tmp_path, monkeypatch,
) -> None:
    """#454: a report that did not reach its parent is never only a missing row."""
    vault = _setup(tmp_path, monkeypatch)

    _run(monkeypatch, _card({"pass": 3}), delivered=False)

    [row] = _rows(vault)
    assert (row["event"], row["delivered"]) == ("finished", False)
    assert row["reason"].startswith("parent not live")
    [err] = [json.loads(l) for l in (vault / ".errors.log").read_text(encoding="utf-8").splitlines()]
    assert err["where"] == rc.UNDELIVERED_WHERE and "c0da0f55" in err["message"]


def test_a_watch_that_ends_without_a_post_is_recorded_as_undelivered(
    tmp_path, monkeypatch,
) -> None:
    vault = _setup(tmp_path, monkeypatch, watch=5)
    monkeypatch.setattr(rc, "gather", lambda *a, **k: _card({"pending": 1}))
    monkeypatch.setattr(inbox, "deliver", lambda *a: "")
    monkeypatch.setattr(rc, "watch_checks", lambda c, **kw: "parent-gone")

    cli_main(["child-report", "c0da0f55", "--parent", "P1"])

    assert [(r["event"], r["delivered"], r.get("reason")) for r in _rows(vault)] == [
        ("finished", True, None),
        ("parent-gone", False, "parent not live when checks were due"),
    ]


def test_a_crash_leaves_a_row_too(tmp_path, monkeypatch) -> None:
    vault = _setup(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(rc, "gather", boom)
    cli_main(["child-report", "c0da0f55", "--parent", "P1"])

    [row] = _rows(vault)
    assert row["delivered"] is False and row["reason"] == "child-report crashed: RuntimeError: boom"
