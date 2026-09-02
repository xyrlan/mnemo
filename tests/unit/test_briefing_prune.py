"""``briefing.prune`` — retention with source protection (#116)."""
from __future__ import annotations

import os
import time

from mnemo.core import briefing

DAY = 86400


def _briefing(vault, agent, sid, age_days):
    p = vault / "bots" / agent / "briefings" / "sessions" / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {sid}\n", encoding="utf-8")
    t = time.time() - age_days * DAY
    os.utime(p, (t, t))
    return p


def _rule(vault, slug, sources, where="feedback"):
    p = vault / "shared" / where / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    src = "\n".join(f"  - {s}" for s in sources)
    p.write_text(
        f"---\nname: {slug}\ntype: feedback\nsources:\n{src}\n---\nbody\n",
        encoding="utf-8",
    )


def _cfg(**over):
    return {"briefings": {"retentionDays": 180, "keepPerAgent": 2, **over}}


def test_prune_deletes_old_unprotected_beyond_keep(tmp_path):
    old1 = _briefing(tmp_path, "a", "s1", 400)
    old2 = _briefing(tmp_path, "a", "s2", 300)
    old3 = _briefing(tmp_path, "a", "s3", 200)
    new = _briefing(tmp_path, "a", "s4", 10)
    rep = briefing.prune(tmp_path, _cfg())
    # keepPerAgent=2 keeps s4 and s3 (newest); s1, s2 are older than 180d → deleted
    assert sorted(p.name for p in rep.deleted) == ["s1.md", "s2.md"]
    assert not old1.exists() and not old2.exists() and old3.exists() and new.exists()
    assert rep.scanned == 4 and rep.kept_min == 2


def test_prune_protects_sources_of_live_rules(tmp_path):
    old = _briefing(tmp_path, "a", "s1", 400)
    _briefing(tmp_path, "a", "s2", 10)
    _briefing(tmp_path, "a", "s3", 5)
    _rule(tmp_path, "r", ["bots/a/briefings/sessions/s1.md"])
    rep = briefing.prune(tmp_path, _cfg())
    assert rep.deleted == [] and old.exists() and rep.protected_by_sources == 1


def test_prune_ignores_archived_rule_sources(tmp_path):
    old = _briefing(tmp_path, "a", "s1", 400)
    _briefing(tmp_path, "a", "s2", 10)
    _briefing(tmp_path, "a", "s3", 5)
    _rule(
        tmp_path, "r", ["bots/a/briefings/sessions/s1.md"],
        where="_archive/reclassify-x/originals/feedback",
    )
    assert briefing.prune(tmp_path, _cfg()).deleted == [old]


def test_prune_retention_zero_never_deletes(tmp_path):
    _briefing(tmp_path, "a", "s1", 900)
    rep = briefing.prune(tmp_path, _cfg(retentionDays=0))
    assert rep.deleted == [] and rep.scanned == 1


def test_prune_dry_run_keeps_files(tmp_path):
    old = _briefing(tmp_path, "a", "s1", 400)
    _briefing(tmp_path, "a", "s2", 10)
    _briefing(tmp_path, "a", "s3", 5)
    rep = briefing.prune(tmp_path, _cfg(), dry_run=True)
    assert rep.deleted == [old] and old.exists()
