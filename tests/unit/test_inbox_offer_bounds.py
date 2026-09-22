"""The three bounds that keep the staged-page offer from becoming a nag (#380).

The block rides on the session-start prompt, beside a briefing that already
costs ~1783 tokens at 90.9% of starts. So the budget is not a preference:

* at most ``inbox.offerMax`` bullets per block;
* at most one block per project per ``inbox.offerIntervalHours``;
* no page repeated inside ``inbox.offerCooldownDays``.

Deliberately *not* a once-ever marker. #229's once-ever warning was spent the
first time its condition was checked rather than the first time it was true and
went silent for three days; a page nobody decided has to come back.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core import inbox as I
from mnemo.core.config import DEFAULTS


def _cfg(**over) -> dict:
    return {**DEFAULTS, "inbox": {**DEFAULTS["inbox"], **over}}


def _page(vault: Path, slug: str, *, age_days: float = 1.0, project: str = "demo",
          verdict: str = "") -> Path:
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = f"reference_gate: {verdict}\n" if verdict else ""
    path.write_text(
        f"---\nname: {slug}\nslug: {slug}\ndescription: d\ntype: reference\n{stamp}"
        f"sources:\n  - bots/{project}/x.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    # A minute past the day boundary: ages floor, and Windows' coarse clock can
    # read `now` a tick before the `time.time()` this was computed from.
    ts = time.time() - age_days * 86400 - 60
    os.utime(path, (ts, ts))
    return path


def test_defaults_are_two_bullets_a_day_a_week_apart():
    """The shipped numbers are the claim the PR makes; pin them."""
    assert DEFAULTS["inbox"] == {
        "offerOnSessionStart": True,
        "offerMax": 2,
        "offerCooldownDays": 7,
        "offerIntervalHours": 24,
        # Not an offer bound: how long a judge-held page may wait (#429).
        "heldExpiryDays": 14,
    }


def test_the_head_of_the_queue_is_offered_first(tmp_vault: Path):
    _page(tmp_vault, "young", age_days=1)
    _page(tmp_vault, "ancient", age_days=30)
    _page(tmp_vault, "middling", age_days=5)

    pages, waiting = I.pick_offers(tmp_vault, "demo", cfg=_cfg())

    assert [p.slug for p in pages] == ["ancient", "middling"]
    assert waiting == 3, "the total is the whole queue, not the offered slice"


def test_a_second_session_the_same_day_is_silent(tmp_vault: Path):
    _page(tmp_vault, "a")
    _page(tmp_vault, "b")
    _page(tmp_vault, "c")
    pages, _ = I.pick_offers(tmp_vault, "demo", cfg=_cfg())
    for page in pages:
        I.record(tmp_vault, event=I.OFFERED, key=page.key, project="demo")

    again, waiting = I.pick_offers(tmp_vault, "demo", cfg=_cfg())

    assert again == []
    # 0, not 3: the interval is checked before the queue is walked, so a
    # silenced session never pays to parse every staged page's frontmatter.
    assert waiting == 0


def test_tomorrow_moves_down_the_queue_instead_of_repeating_its_head(tmp_vault: Path):
    _page(tmp_vault, "a", age_days=30)
    _page(tmp_vault, "b", age_days=20)
    _page(tmp_vault, "c", age_days=10)
    first, _ = I.pick_offers(tmp_vault, "demo", cfg=_cfg())
    for page in first:
        I.record(tmp_vault, event=I.OFFERED, key=page.key, project="demo")

    later = datetime.now() + timedelta(hours=25)
    second, _ = I.pick_offers(tmp_vault, "demo", cfg=_cfg(), now=later)

    assert [p.slug for p in first] == ["a", "b"]
    assert [p.slug for p in second] == ["c"]


def test_a_page_nobody_decided_comes_back_after_the_cooldown(tmp_vault: Path):
    _page(tmp_vault, "only")
    I.record(tmp_vault, event=I.OFFERED, key="reference/only", project="demo")

    inside = datetime.now() + timedelta(days=6, hours=1)
    outside = datetime.now() + timedelta(days=7, hours=1)

    assert I.pick_offers(tmp_vault, "demo", cfg=_cfg(), now=inside)[0] == []
    assert [p.slug for p in I.pick_offers(tmp_vault, "demo", cfg=_cfg(), now=outside)[0]] == ["only"]


def test_another_project_is_not_offered_this_project_queue(tmp_vault: Path):
    _page(tmp_vault, "theirs", project="other")

    assert I.pick_offers(tmp_vault, "demo", cfg=_cfg()) == ([], 0)


def test_the_offer_can_be_switched_off_entirely(tmp_vault: Path):
    _page(tmp_vault, "a")

    assert I.pick_offers(tmp_vault, "demo", cfg=_cfg(offerOnSessionStart=False)) == ([], 0)
    # And `mnemo inbox` still lists it — switching off the offer is not
    # switching off the queue.
    assert len(I.staged_pages(tmp_vault, project="demo")) == 1


def test_zero_bounds_mean_no_bound_not_no_offer(tmp_vault: Path):
    """``offerIntervalHours: 0`` / ``offerCooldownDays: 0`` are the escape hatch
    for a maintainer who wants the queue in front of them every session."""
    _page(tmp_vault, "a", age_days=3)
    _page(tmp_vault, "b", age_days=2)
    cfg = _cfg(offerIntervalHours=0, offerCooldownDays=0, offerMax=1)

    first, _ = I.pick_offers(tmp_vault, "demo", cfg=cfg)
    I.record(tmp_vault, event=I.OFFERED, key=first[0].key, project="demo")
    second, _ = I.pick_offers(tmp_vault, "demo", cfg=cfg)

    assert [p.slug for p in first] == ["a"]
    assert [p.slug for p in second] == ["a"]


def test_a_garbled_config_value_falls_back_to_the_default(tmp_vault: Path):
    settings = I.offer_settings({"inbox": {"offerMax": "two", "offerCooldownDays": None}})

    assert settings["max"] == 2 and settings["cooldown_days"] == 7


# --- the order: pages worth a decision first (#433) -------------------------


def test_the_judges_keeps_come_first_and_its_let_gos_last(tmp_vault: Path):
    """A T page younger than an unstamped one and a G page older than both:
    by age the G page would take a slot, and it expires on its own anyway."""
    _page(tmp_vault, "kept", age_days=1, verdict="technique")
    _page(tmp_vault, "unjudged", age_days=5)
    _page(tmp_vault, "generic", age_days=30, verdict="generic")

    pages, waiting = I.pick_offers(tmp_vault, "demo", cfg=_cfg(offerMax=3))

    assert [p.slug for p in pages] == ["kept", "unjudged", "generic"]
    assert waiting == 3


def test_age_still_orders_inside_each_group(tmp_vault: Path):
    _page(tmp_vault, "sys-young", age_days=1, verdict="system")
    _page(tmp_vault, "tech-old", age_days=9, verdict="technique")
    _page(tmp_vault, "narr-young", age_days=2, verdict="narrative")
    _page(tmp_vault, "gen-old", age_days=20, verdict="generic")
    _page(tmp_vault, "plain-young", age_days=3)
    _page(tmp_vault, "plain-old", age_days=8)

    pages, _ = I.pick_offers(tmp_vault, "demo", cfg=_cfg(offerMax=6))

    assert [p.slug for p in pages] == [
        "tech-old", "sys-young", "plain-old", "plain-young", "gen-old", "narr-young",
    ]


def test_the_max_still_caps_the_reordered_offer(tmp_vault: Path):
    _page(tmp_vault, "g", age_days=30, verdict="generic")
    _page(tmp_vault, "t1", age_days=2, verdict="technique")
    _page(tmp_vault, "t2", age_days=1, verdict="system")
    _page(tmp_vault, "u", age_days=5)

    pages, waiting = I.pick_offers(tmp_vault, "demo", cfg=_cfg())

    assert [p.slug for p in pages] == ["t1", "t2"]
    assert waiting == 4


def test_a_kept_page_in_cooldown_yields_to_the_next_group(tmp_vault: Path):
    _page(tmp_vault, "t", age_days=2, verdict="technique")
    _page(tmp_vault, "u", age_days=5)
    _page(tmp_vault, "g", age_days=30, verdict="generic")
    I.record(tmp_vault, event=I.OFFERED, key="reference/t", project="elsewhere")

    pages, _ = I.pick_offers(tmp_vault, "demo", cfg=_cfg())

    assert [p.slug for p in pages] == ["u", "g"]


def test_the_interval_still_silences_a_queue_of_kept_pages(tmp_vault: Path):
    _page(tmp_vault, "t1", verdict="technique")
    _page(tmp_vault, "t2", verdict="system")
    _page(tmp_vault, "t3", verdict="system")
    pages, _ = I.pick_offers(tmp_vault, "demo", cfg=_cfg())
    for page in pages:
        I.record(tmp_vault, event=I.OFFERED, key=page.key, project="demo")

    assert I.pick_offers(tmp_vault, "demo", cfg=_cfg()) == ([], 0)


def test_an_unknown_stamp_reads_as_unjudged(tmp_vault: Path):
    _page(tmp_vault, "odd", verdict="maybe")
    _page(tmp_vault, "sys", verdict="system")

    by_slug = {p.slug: p for p in I.staged_pages(tmp_vault)}

    assert by_slug["odd"].gate_verdict == "" and by_slug["odd"].gate_label == ""
    assert by_slug["sys"].gate_verdict == "system"
    assert by_slug["sys"].gate_label == "judge: system knowledge"
    assert not by_slug["sys"].gate_held, "only a G/N verdict makes a page expirable"
