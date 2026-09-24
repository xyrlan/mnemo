"""``mnemo inbox --json``, batch decisions and ``--review`` (#495, install review M1).

The JSON shapes here are a contract: mnemo-desktop's install-review screen is
built against them (``docs/superpowers/specs/2026-09-24-install-review-design.md``),
so each test pins a key set, not just a value.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemo.cli.parser import COMMANDS, _build_parser

_SECRET = "ghp_" + "a1B2" * 9


def _run(vault: Path, monkeypatch, *, stdin: str = "", **flags) -> tuple[int, str]:
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    from mnemo.core import inbox as I

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr("mnemo.cli.commands.inbox._resolve_project", lambda _v: "demo")
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    defaults = {
        "all": False, "show": None, "promote": None, "drop": None, "stats": False,
        "restore": None, "keys_stdin": False, "review": False, "origin": "any",
        "project": None, "json": False,
    }
    ns = argparse.Namespace(**{**defaults, **flags})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = COMMANDS["inbox"](ns)
    return rc, buf.getvalue()


def _page(vault: Path, rel: str, *, age_days: float = 1.0, project: str = "demo",
          body: str = "body\n", extra: str = "", name: str | None = None) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name or path.stem}\nslug: {path.stem}\ndescription: what it says\n"
        f"type: {path.parent.name}\n{extra}sources:\n  - bots/{project}/x.md\n---\n\n{body}",
        encoding="utf-8",
    )
    ts = time.time() - age_days * 86400 - 60
    os.utime(path, (ts, ts))
    return path


def _ledger(vault: Path) -> list[dict]:
    path = vault / ".mnemo" / "inbox-offers.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def cfg(tmp_vault: Path, monkeypatch):
    """Point config at the vault's own file so ``heldExpiryDays`` is the default."""
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault / "mnemo.config.json"


BACKFILL = "origin: backfill\n"


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def test_the_parser_takes_several_keys_stdin_origin_project_json_and_review():
    ns = _build_parser().parse_args(
        ["inbox", "--promote", "project/a", "feedback/b", "--json"])
    assert ns.promote == ["project/a", "feedback/b"] and ns.json

    ns = _build_parser().parse_args(["inbox", "--drop", "--keys-stdin"])
    assert ns.drop == [] and ns.keys_stdin

    ns = _build_parser().parse_args(
        ["inbox", "--review", "--origin", "backfill", "--project", "clubinho"])
    assert ns.review and ns.origin == "backfill" and ns.project == "clubinho"

    assert _build_parser().parse_args(["inbox"]).origin == "any"
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["inbox", "--origin", "live"])


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_json_listing_has_the_contract_shape(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/demo__cron.md", extra=BACKFILL,
          name="Annual cron blocked until #183")
    _page(tmp_vault, "shared/_inbox/feedback/demo__rule.md", extra=BACKFILL)
    _page(tmp_vault, "shared/_inbox/project/demo__other.md", extra=BACKFILL)

    rc, out = _run(tmp_vault, monkeypatch, json=True)

    assert rc == 0
    doc = json.loads(out)
    assert set(doc) == {"project", "origin", "pages", "counts"}
    assert doc["project"] == "demo" and doc["origin"] == "any"
    assert doc["counts"] == {"project": 2, "feedback": 1}
    row = next(p for p in doc["pages"] if p["key"] == "project/demo__cron")
    assert set(row) == {"key", "type", "name", "description", "excerpt", "staged_at", "expires_at"}
    assert row["type"] == "project"
    assert row["name"] == "Annual cron blocked until #183"
    assert row["description"] == "what it says"
    assert row["excerpt"] == "body"


def test_staged_at_is_the_mtime_and_expires_at_is_fourteen_days_later(
    tmp_vault: Path, monkeypatch, cfg,
):
    from mnemo.core.inbox import HELD_EXPIRY_DAYS

    path = _page(tmp_vault, "shared/_inbox/project/demo__a.md", extra=BACKFILL, age_days=3)

    _, out = _run(tmp_vault, monkeypatch, json=True)

    row = json.loads(out)["pages"][0]
    staged = datetime.fromisoformat(row["staged_at"])
    assert staged == datetime.fromtimestamp(int(path.stat().st_mtime))
    assert datetime.fromisoformat(row["expires_at"]) - staged == timedelta(days=HELD_EXPIRY_DAYS)
    assert HELD_EXPIRY_DAYS == 14


def test_expires_at_follows_the_config_that_drives_the_expiry(
    tmp_vault: Path, monkeypatch, cfg,
):
    """One number: the config ``expire_held`` reads, not a second constant."""
    cfg.write_text(json.dumps({"vaultRoot": str(tmp_vault), "inbox": {"heldExpiryDays": 5}}),
                   encoding="utf-8")
    _page(tmp_vault, "shared/_inbox/project/demo__a.md", extra=BACKFILL)

    row = json.loads(_run(tmp_vault, monkeypatch, json=True)[1])["pages"][0]
    gap = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["staged_at"])
    assert gap == timedelta(days=5)

    cfg.write_text(json.dumps({"vaultRoot": str(tmp_vault), "inbox": {"heldExpiryDays": 0}}),
                   encoding="utf-8")
    row = json.loads(_run(tmp_vault, monkeypatch, json=True)[1])["pages"][0]
    assert row["expires_at"] is None


def test_a_page_the_queue_never_sheds_has_no_expiry(tmp_vault: Path, monkeypatch, cfg):
    """A multi-source page waits for a human, and so does one someone restored."""
    from mnemo.core import inbox as I

    _page(tmp_vault, "shared/_inbox/reference/demo__plain.md")
    _page(tmp_vault, "shared/_inbox/reference/demo__held.md", extra="reference_gate: generic\n")
    _page(tmp_vault, "shared/_inbox/project/demo__back.md", extra=BACKFILL)
    I.record(tmp_vault, event=I.RESTORED, key="project/demo__back")

    rows = {p["key"]: p for p in json.loads(_run(tmp_vault, monkeypatch, json=True)[1])["pages"]}

    assert rows["reference/demo__plain"]["expires_at"] is None
    assert rows["reference/demo__held"]["expires_at"] is not None
    assert rows["project/demo__back"]["expires_at"] is None


def test_the_excerpt_is_redacted_then_cut_to_300_characters(tmp_vault: Path, monkeypatch, cfg):
    body = f"Deploy with {_SECRET} then run it.\n" + "x" * 1000
    _page(tmp_vault, "shared/_inbox/project/demo__a.md", extra=BACKFILL, body=body)

    row = json.loads(_run(tmp_vault, monkeypatch, json=True)[1])["pages"][0]

    assert _SECRET not in row["excerpt"] and "[redacted]" in row["excerpt"]
    assert len(row["excerpt"]) == 300
    assert row["excerpt"].startswith("Deploy with [redacted] then run it.")


def test_a_secret_straddling_the_cut_leaves_no_half_behind(tmp_vault: Path, monkeypatch, cfg):
    """Cut first and the tail of the token would no longer look like one."""
    body = "y" * 290 + " " + _SECRET + " tail"
    _page(tmp_vault, "shared/_inbox/project/demo__a.md", extra=BACKFILL, body=body)

    row = json.loads(_run(tmp_vault, monkeypatch, json=True)[1])["pages"][0]

    assert "ghp_" not in row["excerpt"] and "a1B2" not in row["excerpt"]


def test_origin_backfill_keeps_only_stamped_pages_and_says_so(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/demo__b.md", extra=BACKFILL)
    _page(tmp_vault, "shared/_inbox/reference/demo__live.md")
    # The nested spelling a harvested file carries counts too: one predicate.
    _page(tmp_vault, "shared/_inbox/feedback/demo__n.md", extra="metadata:\n  origin: backfill\n")

    doc = json.loads(_run(tmp_vault, monkeypatch, json=True, origin="backfill")[1])

    assert doc["origin"] == "backfill"
    assert sorted(p["key"] for p in doc["pages"]) == ["feedback/demo__n", "project/demo__b"]
    assert doc["counts"] == {"project": 1, "feedback": 1}


def test_project_flag_and_all_scope_the_json_listing(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")
    _page(tmp_vault, "shared/_inbox/project/club__a.md", project="clubinho")

    doc = json.loads(_run(tmp_vault, monkeypatch, json=True, project="clubinho")[1])
    assert doc["project"] == "clubinho"
    assert [p["key"] for p in doc["pages"]] == ["project/club__a"]

    doc = json.loads(_run(tmp_vault, monkeypatch, json=True, all=True)[1])
    assert doc["project"] is None and len(doc["pages"]) == 2


def test_an_empty_queue_is_still_one_json_document(tmp_vault: Path, monkeypatch, cfg):
    rc, out = _run(tmp_vault, monkeypatch, json=True, origin="backfill")

    assert rc == 0
    assert json.loads(out) == {"project": "demo", "origin": "backfill", "pages": [], "counts": {}}


def test_json_is_utf8_whatever_the_page_is_named(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md", name="Cron anual bloqueado até #183")

    doc = json.loads(_run(tmp_vault, monkeypatch, json=True)[1])

    assert doc["pages"][0]["name"] == "Cron anual bloqueado até #183"


def test_text_listing_is_unchanged_without_json(tmp_vault: Path, monkeypatch):
    """Byte for byte what the listing printed before #495, for the same queue."""
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md", age_days=9)
    _page(tmp_vault, "shared/_inbox/reference/other__y.md", age_days=2, project="other")

    rc, out = _run(tmp_vault, monkeypatch)

    assert rc == 0
    assert out == (
        "1 staged page for demo in shared/_inbox/ (median 9d, oldest 9d)\n"
        "\n"
        "  reference/demo__x  other          9d  what it says\n"
        "\n"
        "nothing was written — this is a listing only.\n"
        "  `mnemo inbox --promote KEY` moves one into shared/<type>/, where recall sees it\n"
        "  `mnemo inbox --drop KEY` archives it and takes it out of the queue\n"
        "  `mnemo inbox --show KEY` prints one page\n"
        "  (1 more staged for other projects — `mnemo inbox --all`)\n"
    )


# ---------------------------------------------------------------------------
# Deciding in batch
# ---------------------------------------------------------------------------


def test_batch_promote_json_reports_every_key(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")
    _page(tmp_vault, "shared/_inbox/feedback/demo__b.md")

    rc, out = _run(tmp_vault, monkeypatch, json=True,
                   promote=["project/demo__a", "feedback/demo__b"])

    assert rc == 0
    assert json.loads(out) == {"promoted": ["project/demo__a", "feedback/demo__b"], "failed": []}
    assert (tmp_vault / "shared" / "project" / "demo__a.md").is_file()
    assert (tmp_vault / "shared" / "feedback" / "demo__b.md").is_file()


def test_a_failure_in_the_middle_of_a_batch_does_not_stop_the_rest(tmp_vault: Path, monkeypatch):
    """The middle key fails for real — a live page already holds its slot —
    and the keys either side of it are still decided."""
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")
    _page(tmp_vault, "shared/_inbox/reference/demo__c.md")
    _page(tmp_vault, "shared/_inbox/feedback/demo__d.md")
    live = tmp_vault / "shared" / "reference" / "demo__c.md"
    live.parent.mkdir(parents=True)
    live.write_text("---\nname: live\n---\n", encoding="utf-8")

    rc, out = _run(tmp_vault, monkeypatch, json=True,
                   promote=["project/demo__a", "reference/demo__c", "reference/ghost",
                            "feedback/demo__d"])

    doc = json.loads(out)
    assert rc == 1
    assert doc["promoted"] == ["project/demo__a", "feedback/demo__d"]
    assert [f["key"] for f in doc["failed"]] == ["reference/demo__c", "reference/ghost"]
    assert "already exists" in doc["failed"][0]["error"]
    assert "no staged page" in doc["failed"][1]["error"]
    assert (tmp_vault / "shared" / "feedback" / "demo__d.md").is_file()
    assert (tmp_vault / "shared" / "_inbox" / "reference" / "demo__c.md").is_file()


def test_an_exception_on_one_key_is_that_keys_failure(tmp_vault: Path, monkeypatch):
    from mnemo.core import inbox as I

    _page(tmp_vault, "shared/_inbox/project/demo__a.md")
    _page(tmp_vault, "shared/_inbox/project/demo__b.md")
    _page(tmp_vault, "shared/_inbox/project/demo__c.md")
    real = I._archive_out

    def flaky(vault_root, page, **kw):
        if page.key == "project/demo__b":
            raise RuntimeError("disk on fire")
        return real(vault_root, page, **kw)

    monkeypatch.setattr(I, "_archive_out", flaky)

    rc, out = _run(tmp_vault, monkeypatch, json=True,
                   drop=["project/demo__a", "project/demo__b", "project/demo__c"])

    doc = json.loads(out)
    assert rc == 1
    assert doc == {
        "dropped": ["project/demo__a", "project/demo__c"],
        "failed": [{"key": "project/demo__b",
                    "error": "could not decide project/demo__b: disk on fire"}],
    }


def test_every_batch_decision_lands_in_the_ledger_with_via_review(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")
    _page(tmp_vault, "shared/_inbox/project/demo__b.md")

    _run(tmp_vault, monkeypatch, json=True, promote=["project/demo__a"])
    _run(tmp_vault, monkeypatch, json=True, drop=["project/demo__b"])

    rows = _ledger(tmp_vault)
    assert [(r["event"], r["key"], r["via"]) for r in rows] == [
        ("promoted", "project/demo__a", "review"),
        ("dropped", "project/demo__b", "review"),
    ]
    assert all(r["project"] == "demo" for r in rows)


def test_a_single_text_decision_writes_the_row_it_always_did(tmp_vault: Path, monkeypatch):
    """No ``via``: the offer-driven drain stays distinguishable from the review's."""
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")

    rc, out = _run(tmp_vault, monkeypatch, promote=["project/demo__a"])

    assert rc == 0 and "recall can reach it now" in out
    assert "via" not in _ledger(tmp_vault)[0]


def test_keys_stdin_reads_one_key_per_line(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")
    _page(tmp_vault, "shared/_inbox/project/demo__b.md")

    rc, out = _run(tmp_vault, monkeypatch, json=True, drop=[], keys_stdin=True,
                   stdin="project/demo__a\n\n  project/demo__b  \n")

    assert rc == 0
    assert json.loads(out) == {"dropped": ["project/demo__a", "project/demo__b"], "failed": []}


def test_keys_stdin_without_an_action_is_refused(tmp_vault: Path, monkeypatch):
    rc, out = _run(tmp_vault, monkeypatch, keys_stdin=True, stdin="project/a\n")

    assert rc == 1 and "--promote or --drop" in out


def test_a_repeated_key_is_decided_once(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")

    rc, out = _run(tmp_vault, monkeypatch, json=True,
                   promote=["project/demo__a", "project/demo__a"])

    assert rc == 0 and json.loads(out) == {"promoted": ["project/demo__a"], "failed": []}


def test_a_bare_slug_in_a_batch_is_reported_by_its_full_key(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")

    _, out = _run(tmp_vault, monkeypatch, json=True, promote=["demo__a"])

    assert json.loads(out)["promoted"] == ["project/demo__a"]


def test_a_batch_rebuilds_the_indexes_once(tmp_vault: Path, monkeypatch):
    from mnemo.core import inbox as I

    for slug in ("a", "b", "c"):
        _page(tmp_vault, f"shared/_inbox/project/demo__{slug}.md")
    calls = []
    monkeypatch.setattr(I, "_rebuild_indexes", lambda v: calls.append(v))

    I.decide_many(tmp_vault, ["project/demo__a", "project/demo__b", "project/demo__c"],
                  action=I.PROMOTED)

    assert calls == [tmp_vault]


def test_multi_key_text_output_names_each_decision(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__a.md")

    rc, out = _run(tmp_vault, monkeypatch, drop=["project/demo__a", "project/ghost"])

    assert rc == 1
    assert "dropped project/demo__a" in out and "no staged page for project/ghost" in out


# ---------------------------------------------------------------------------
# Terminal review
# ---------------------------------------------------------------------------


class _TtyIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def _run_tty(vault: Path, monkeypatch, answers: list[str], **flags) -> tuple[int, str]:
    import mnemo.cli.commands  # noqa: F401
    from mnemo.core import inbox as I

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr("mnemo.cli.commands.inbox._resolve_project", lambda _v: "demo")
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    monkeypatch.setattr("sys.stdin", _TtyIO(""))
    feed = iter(answers)

    def fake_input(prompt=""):
        print(prompt, end="")
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)
    defaults = {
        "all": False, "show": None, "promote": None, "drop": None, "stats": False,
        "restore": None, "keys_stdin": False, "review": True, "origin": "any",
        "project": None, "json": False,
    }
    buf = _TtyIO()
    with contextlib.redirect_stdout(buf):
        rc = COMMANDS["inbox"](argparse.Namespace(**{**defaults, **flags}))
    return rc, buf.getvalue()


def _three(vault: Path) -> None:
    _page(vault, "shared/_inbox/reference/demo__r.md", extra=BACKFILL, age_days=3)
    _page(vault, "shared/_inbox/project/demo__p.md", extra=BACKFILL, age_days=2)
    _page(vault, "shared/_inbox/feedback/demo__f.md", extra=BACKFILL, age_days=1)


def test_review_off_a_tty_prints_the_checklist_and_decides_nothing(tmp_vault: Path, monkeypatch):
    _three(tmp_vault)

    rc, out = _run(tmp_vault, monkeypatch, review=True)

    assert rc == 0
    assert "not a terminal — nothing was decided" in out
    assert "[x]" in out and "[ ]" not in out
    assert _ledger(tmp_vault) == []
    assert len(list((tmp_vault / "shared" / "_inbox").rglob("*.md"))) == 3


def test_review_groups_by_type_with_project_feedback_reference_first(
    tmp_vault: Path, monkeypatch,
):
    _three(tmp_vault)
    _page(tmp_vault, "shared/_inbox/user/demo__u.md", extra=BACKFILL)

    _, out = _run(tmp_vault, monkeypatch, review=True)

    heads = [ln for ln in out.splitlines() if ln and not ln.startswith(" ") and "(" in ln
             and ln.endswith(")")]
    assert heads == ["project (1)", "feedback (1)", "reference (1)", "user (1)"]


def test_review_keep_promotes_the_checked_and_drops_the_rest(tmp_vault: Path, monkeypatch):
    _three(tmp_vault)

    # 1 = project, 2 = feedback, 3 = reference: uncheck the reference, keep.
    rc, out = _run_tty(tmp_vault, monkeypatch, ["3", "k", "y"])

    assert rc == 0
    assert "promoted 2, dropped 1." in out
    assert (tmp_vault / "shared" / "project" / "demo__p.md").is_file()
    assert (tmp_vault / "shared" / "feedback" / "demo__f.md").is_file()
    assert not (tmp_vault / "shared" / "reference" / "demo__r.md").exists()
    assert not list((tmp_vault / "shared" / "_inbox").rglob("*.md"))
    rows = _ledger(tmp_vault)
    assert {(r["event"], r["key"]) for r in rows} == {
        ("promoted", "project/demo__p"), ("promoted", "feedback/demo__f"),
        ("dropped", "reference/demo__r"),
    }
    assert all(r["via"] == "review" for r in rows)


def test_review_toggles_ranges_none_and_all(tmp_vault: Path, monkeypatch):
    _three(tmp_vault)

    rc, out = _run_tty(tmp_vault, monkeypatch, ["none", "1-2", "0", "k", "y"])

    assert rc == 0
    assert "not understood: '0'" in out
    assert "promoted 2, dropped 1." in out


def test_review_quit_decides_nothing(tmp_vault: Path, monkeypatch):
    _three(tmp_vault)

    rc, out = _run_tty(tmp_vault, monkeypatch, ["1", "q"])

    assert rc == 0 and "nothing was decided" in out
    assert _ledger(tmp_vault) == []


def test_review_keep_without_confirmation_decides_nothing(tmp_vault: Path, monkeypatch):
    """``k`` then anything but yes goes back to the list; EOF then quits."""
    _three(tmp_vault)

    rc, out = _run_tty(tmp_vault, monkeypatch, ["k", "n"])

    assert rc == 0 and "nothing was decided" in out
    assert _ledger(tmp_vault) == []


def test_review_reports_a_failure_and_still_decides_the_rest(tmp_vault: Path, monkeypatch):
    _three(tmp_vault)
    live = tmp_vault / "shared" / "project" / "demo__p.md"
    live.parent.mkdir(parents=True)
    live.write_text("---\nname: live\n---\n", encoding="utf-8")

    rc, out = _run_tty(tmp_vault, monkeypatch, ["k", "y"])

    assert rc == 1
    assert "promoted 2, dropped 0, 1 failed:" in out
    assert "project/demo__p: shared/project/demo__p.md already exists" in out


def test_review_honours_origin_backfill(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/project/demo__b.md", extra=BACKFILL)
    _page(tmp_vault, "shared/_inbox/reference/demo__live.md")

    _, out = _run(tmp_vault, monkeypatch, review=True, origin="backfill")

    assert "demo__b" in out and "demo__live" not in out


def test_review_with_nothing_staged_says_so(tmp_vault: Path, monkeypatch):
    rc, out = _run(tmp_vault, monkeypatch, review=True)

    assert rc == 0 and "nothing staged to review for demo" in out


def test_review_is_one_action(tmp_vault: Path, monkeypatch):
    rc, out = _run(tmp_vault, monkeypatch, review=True, promote=["project/a"])

    assert rc == 1 and "pick one action" in out
