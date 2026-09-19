"""The bounds and the cache behind the procedure offer (#397).

``mnemo procedures`` and its ``doctor`` row are both pulls, and #385 priced a
pull at 5 of 182 children. The push is a block on the session-start prompt,
built like #380's and bounded the same way:

* at most ``procedures.offerMax`` bullets per block (one, not two: accepting
  writes a permanent ``CLAUDE.md`` line every session in that repo then pays
  for);
* at most one block per repo per ``procedures.offerIntervalHours``;
* no candidate repeated inside ``procedures.offerCooldownDays``.

And one bound the inbox has no use for. A staged page is a file already on
disk; a candidate does not exist until every dispatch transcript has been read,
which costs ~1.0 s. So the scan runs detached and the offer reads its cache —
which makes *staleness* a thing this queue has to answer for, and these pin
where it is bought back: a decided candidate and a line the maintainer wrote by
hand are both read live, not from the cache.

What the block itself looks like is pinned in
``test_session_start_procedure_offer.py``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemo.core import procedures as P
from mnemo.core.config import DEFAULTS


def _cfg(**over) -> dict:
    return {**DEFAULTS, "procedures": {**DEFAULTS["procedures"], **over}}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _candidate(
    repo: str = "app",
    shape: str = "cargo test",
    *,
    carrier: str = "SDKROOT",
    value: str = "/sdk",
    repo_root: Path | None = None,
    stated: bool = False,
) -> P.Candidate:
    return P.Candidate(
        repo=repo,
        shape=shape,
        carriers=(P.Carrier(
            name=carrier,
            values=((value, 3),),
            rediscovered=(f"{repo}-wt-1", f"{repo}-wt-2"),
            kept=1,
            never=0,
            stated=stated,
        ),),
        shape_children=9,
        first_day="2026-09-01",
        last_day="2026-09-18",
        repo_root=str(repo_root) if repo_root else None,
    )


def _cache(vault: Path, *candidates: P.Candidate) -> None:
    assert P.write_cache(vault, candidates, projects="/p")


# --- the cache is the whole reason the hook can afford this ----------------


def test_a_candidate_survives_the_round_trip_to_disk(vault, tmp_path):
    """The block and the command read the same object, so they cannot disagree."""
    original = _candidate(repo_root=tmp_path / "app")

    _cache(vault, original)
    back, stamp = P.read_cache(vault)

    assert back == [original]
    assert isinstance(stamp, datetime)


def test_no_cache_at_all_is_an_empty_offer_not_a_crash(vault):
    """Every vault looks like this until the first refresh lands."""
    assert P.read_cache(vault) == ([], None)
    assert P.pick_offers(vault, "app", cfg=DEFAULTS) == ([], 0)


def test_a_cache_half_written_by_an_older_version_costs_the_session_nothing(vault):
    P.cache_path(vault).write_text(
        json.dumps({"candidates": [{"repo": "app"}, "junk"]}), encoding="utf-8"
    )

    assert P.read_cache(vault)[0] == []


def test_a_cache_that_is_not_json_costs_the_session_nothing(vault):
    P.cache_path(vault).write_text("{ truncated", encoding="utf-8")

    assert P.read_cache(vault)[0] == []
    assert P.pick_offers(vault, "app", cfg=DEFAULTS) == ([], 0)


# --- staleness, and where it is bought back --------------------------------


def test_a_candidate_the_maintainer_decided_is_never_offered_again(vault, tmp_path):
    """The ledger is read live, so a drop takes effect before the next scan."""
    _cache(vault, _candidate(repo_root=tmp_path / "app"))
    assert P.pick_offers(vault, "app", cfg=DEFAULTS)[0]

    P.record(vault, event=P.DROPPED, repo="app", key="cargo-test")

    assert P.pick_offers(vault, "app", cfg=_cfg(offerIntervalHours=0)) == ([], 0)


def test_a_line_written_into_claude_md_by_hand_silences_the_cached_candidate(
    vault, tmp_path
):
    """The one staleness that would read as the tool not looking at the repo."""
    repo = tmp_path / "app"
    repo.mkdir()
    _cache(vault, _candidate(repo_root=repo))
    assert P.pick_offers(vault, "app", cfg=DEFAULTS)[0]

    (repo / "CLAUDE.md").write_text("Run `SDKROOT=/sdk cargo test`.\n", encoding="utf-8")

    assert P.pick_offers(vault, "app", cfg=_cfg(offerIntervalHours=0)) == ([], 0)


def test_a_partly_stated_line_is_still_offered_for_what_is_missing(vault, tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    both = P.Candidate(
        repo="app", shape="pnpm test",
        carriers=(
            P.Carrier("DEVELOPER_DIR", (("/clt", 2),), ("a",), 0, 0, False),
            P.Carrier("PATH", (("/clt/bin", 2),), ("b",), 0, 0, False),
        ),
        shape_children=5, first_day="", last_day="", repo_root=str(repo),
    )
    _cache(vault, both)
    (repo / "CLAUDE.md").write_text("we already set PATH\n", encoding="utf-8")

    offers, waiting = P.pick_offers(vault, "app", cfg=DEFAULTS)

    assert waiting == 1
    assert [c.name for c in offers[0].unstated] == ["DEVELOPER_DIR"]


def test_only_the_repo_the_session_is_in(vault, tmp_path):
    _cache(vault, _candidate("app", repo_root=tmp_path / "app"),
           _candidate("other", repo_root=tmp_path / "other"))

    offers, waiting = P.pick_offers(vault, "other", cfg=DEFAULTS)

    assert waiting == 1 and [c.repo for c in offers] == ["other"]


# --- the three bounds ------------------------------------------------------


def test_the_block_holds_one_bullet_and_counts_the_rest(vault, tmp_path):
    _cache(vault, *[_candidate("app", f"cargo test{i}", repo_root=tmp_path / "app")
                    for i in range(4)])

    offers, waiting = P.pick_offers(vault, "app", cfg=DEFAULTS)

    assert len(offers) == 1 and waiting == 4


def test_a_repo_is_offered_at_most_once_a_day(vault, tmp_path):
    _cache(vault, _candidate("app", "cargo test", repo_root=tmp_path / "app"),
           _candidate("app", "cargo build", repo_root=tmp_path / "app"))
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")

    assert P.pick_offers(vault, "app", cfg=DEFAULTS) == ([], 0)
    later = datetime.now() + timedelta(hours=25)
    assert P.pick_offers(vault, "app", cfg=DEFAULTS, now=later)[0]


def test_another_repos_block_does_not_spend_this_repos_interval(vault, tmp_path):
    _cache(vault, _candidate("app", repo_root=tmp_path / "app"),
           _candidate("other", repo_root=tmp_path / "other"))
    P.record(vault, event=P.OFFERED, repo="other", key="cargo-test")

    assert P.pick_offers(vault, "app", cfg=DEFAULTS)[0]


def test_the_queue_rotates_rather_than_repeating_its_head(vault, tmp_path):
    """A candidate offered is not offered again inside the cooldown."""
    _cache(vault, _candidate("app", "cargo test", repo_root=tmp_path / "app"),
           _candidate("app", "cargo build", repo_root=tmp_path / "app"))
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")

    offers, waiting = P.pick_offers(vault, "app", cfg=_cfg(offerIntervalHours=0))

    assert waiting == 2 and [c.key for c in offers] == ["cargo-build"]


def test_a_candidate_nobody_decided_comes_back_after_the_cooldown(vault, tmp_path):
    """Deliberately not a once-ever marker: #229's went silent for three days."""
    _cache(vault, _candidate("app", repo_root=tmp_path / "app"))
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")

    later = datetime.now() + timedelta(days=8)
    assert P.pick_offers(vault, "app", cfg=DEFAULTS, now=later)[0]


def test_the_off_switch_leaves_the_command_working(vault, tmp_path):
    _cache(vault, _candidate("app", repo_root=tmp_path / "app"))

    assert P.pick_offers(vault, "app", cfg=_cfg(offerOnSessionStart=False)) == ([], 0)
    assert P.pick_offers(vault, "app", cfg=_cfg(offerMax=0)) == ([], 0)
    # …and the cache the command and `doctor` share is still readable.
    assert len(P.read_cache(vault)[0]) == 1


def test_a_silenced_call_reports_nothing_waiting(vault, tmp_path):
    """Counting would cost exactly what the interval gate saves.

    Same shortcut ``inbox.pick_offers`` takes and for the same reason: the
    total is only ever printed beside the bullets it belongs to.
    """
    _cache(vault, _candidate("app", repo_root=tmp_path / "app"))
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")

    assert P.pick_offers(vault, "app", cfg=DEFAULTS) == ([], 0)


def test_a_broken_ledger_costs_the_session_nothing(vault, tmp_path, monkeypatch):
    _cache(vault, _candidate("app", repo_root=tmp_path / "app"))
    monkeypatch.setattr(P, "ledger_rows", lambda *_a, **_k: 1 / 0)

    assert P.pick_offers(vault, "app", cfg=DEFAULTS) == ([], 0)


# --- when the scan behind it runs ------------------------------------------


def test_a_vault_that_has_never_scanned_is_due(vault):
    assert P.scan_is_due(vault, DEFAULTS) is True


def test_a_scan_that_died_is_retried_once_an_interval_not_once_a_session(vault):
    """The marker is stamped before the spawn, so no cache is not a spawn loop."""
    P.mark_scan(vault)

    assert P.cache_path(vault).exists() is False
    assert P.scan_is_due(vault, DEFAULTS) is False
    stale = datetime.now() + timedelta(hours=25)
    assert P.scan_is_due(vault, DEFAULTS, now=stale) is True


def test_the_off_switch_stops_the_scan_too(vault):
    assert P.scan_is_due(vault, _cfg(offerOnSessionStart=False)) is False
    assert P.scan_is_due(vault, _cfg(refreshIntervalHours=0)) is False


def test_the_marker_is_a_marker_not_the_cache(vault, tmp_path):
    """Written whole, and readable by a human wondering when this last ran."""
    P.mark_scan(vault)

    text = P.scan_marker_path(vault).read_text(encoding="utf-8")
    assert datetime.fromisoformat(text) <= datetime.now()
    assert time.time() - P.scan_marker_path(vault).stat().st_mtime < 60


# --- the instrument --------------------------------------------------------


def test_stats_answers_whether_the_offer_is_what_gets_things_decided(vault, tmp_path):
    """#390's ``inbox --stats`` is the precedent; this mirrors it in this
    queue's own ledger, because the two queues' keys mean different things."""
    candidates = [_candidate("app", repo_root=tmp_path / "app"),
                  _candidate("other", repo_root=tmp_path / "other")]
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")
    P.record(vault, event=P.ACCEPTED, repo="app", key="cargo-test")
    P.record(vault, event=P.DROPPED, repo="other", key="cargo-test")

    out = P.stats(vault, candidates)

    assert out["candidates"] == 2 and out["repos"] == 2
    assert (out["offered"], out["accepted"], out["dropped"]) == (1, 1, 1)
    assert out["resolved"] == 2
    # Only the one that was offered first has a latency to report.
    assert out["median_decision_days"] == 0


def test_a_decision_nobody_was_offered_has_no_latency(vault, tmp_path):
    P.record(vault, event=P.ACCEPTED, repo="app", key="cargo-test")

    assert P.stats(vault, [])["median_decision_days"] is None


def test_the_offer_row_carries_the_session_it_was_shown_to(vault):
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test", session_id="s1")

    assert P.ledger_rows(vault)[0]["session_id"] == "s1"


def test_an_offer_is_not_a_decision(vault, tmp_path):
    """Only ``accepted`` and ``dropped`` take a candidate out of a scan."""
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")

    assert P._decided(P.ledger_rows(vault)) == set()
