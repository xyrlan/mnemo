"""``mnemo inbox --json`` and ``--stats --json`` carry what the text prints (#673).

The desktop's Vault → Inbox pane read the text listing with regular
expressions because the JSON left out the reason, the age, the judge's
verdict, the header and the other-projects count, and ``--stats`` had no JSON
at all. Each test here names one fact the text prints and finds it in the JSON.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from tests.unit.test_inbox_review import BACKFILL, _page, _run, cfg  # noqa: F401 — fixture


def _rows(doc: dict) -> dict:
    return {p["key"]: p for p in doc["pages"]}


def _text_rows(out: str) -> dict:
    """``{key: (reason, age)}`` as the text listing prints them."""
    rows = {}
    for line in out.splitlines():
        m = re.match(r"^  (\S+/\S+)\s+(\S+)\s+(\d+)d  ", line)
        if m:
            rows[m.group(1)] = (m.group(2), int(m.group(3)))
    return rows


def test_each_page_carries_the_reason_the_text_prints(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/feedback/demo__a.md", extra="demoted_from: feedback\n")
    _page(tmp_vault, "shared/_inbox/project/demo__b.md", extra=BACKFILL)
    _page(tmp_vault, "shared/_inbox/reference/demo__c.md")

    _, text = _run(tmp_vault, monkeypatch)
    _, out = _run(tmp_vault, monkeypatch, json=True)

    rows = _rows(json.loads(out))
    assert {k: r["reason"] for k, r in rows.items()} == {
        "feedback/demo__a": "demotion", "project/demo__b": "backfill",
        "reference/demo__c": "other",
    }
    assert {k: v[0] for k, v in _text_rows(text).items()} == {
        k: r["reason"] for k, r in rows.items()}


def test_each_page_carries_the_judges_verdict_as_a_value(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/reference/demo__kept.md", extra="reference_gate: system\n")
    _page(tmp_vault, "shared/_inbox/reference/demo__plain.md")

    _, text = _run(tmp_vault, monkeypatch)
    _, out = _run(tmp_vault, monkeypatch, json=True)

    assert "[judge: system knowledge]" in text
    rows = _rows(json.loads(out))
    assert rows["reference/demo__kept"]["judge"] == "system"
    assert rows["reference/demo__plain"]["judge"] is None
    # The verdict is its own field now, not folded into the description.
    assert rows["reference/demo__kept"]["description"] == "what it says"


def test_the_age_is_the_texts_and_says_what_it_counts_from(tmp_vault: Path, monkeypatch, cfg):
    """A rewritten page: first staged 12 days ago, last written 1 day ago."""
    first = datetime.fromtimestamp(datetime.now().timestamp() - 12 * 86400)
    path = _page(tmp_vault, "shared/_inbox/project/demo__rewritten.md", age_days=1,
                 extra=BACKFILL + f"staged_at: {first.isoformat(timespec='seconds')}\n")

    _, text = _run(tmp_vault, monkeypatch)
    _, out = _run(tmp_vault, monkeypatch, json=True)

    doc = json.loads(out)
    row = _rows(doc)["project/demo__rewritten"]
    assert _text_rows(text)["project/demo__rewritten"][1] == 1
    assert row["age_days"] == 1
    assert datetime.fromisoformat(row["modified_at"]) == datetime.fromtimestamp(
        int(path.stat().st_mtime))
    # ``age_days`` is ``modified_at`` counted against the document's own clock,
    # and ``staged_at`` stays the first-staged time the expiry counts from.
    assert (datetime.fromisoformat(doc["generated_at"])
            - datetime.fromisoformat(row["modified_at"])).days == row["age_days"]
    assert (datetime.fromisoformat(doc["generated_at"])
            - datetime.fromisoformat(row["staged_at"])).days == 12


def test_the_header_median_and_oldest_age_are_in_the_document(
    tmp_vault: Path, monkeypatch, cfg,
):
    for i, age in enumerate((2, 5, 10)):
        _page(tmp_vault, f"shared/_inbox/project/demo__{i}.md", age_days=age)

    _, text = _run(tmp_vault, monkeypatch)
    _, out = _run(tmp_vault, monkeypatch, json=True)

    assert "(median 5d, oldest 10d)" in text
    doc = json.loads(out)
    assert doc["median_age_days"] == 5
    assert doc["oldest_age_days"] == 10


def test_other_projects_count_with_project(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/demo__mine.md")
    _page(tmp_vault, "shared/_inbox/project/x__theirs.md", project="other")
    _page(tmp_vault, "shared/_inbox/project/y__theirs.md", project="third")

    _, text = _run(tmp_vault, monkeypatch, project="demo")
    _, out = _run(tmp_vault, monkeypatch, project="demo", json=True)

    assert "(2 more staged for other projects" in text
    doc = json.loads(out)
    assert [p["key"] for p in doc["pages"]] == ["project/demo__mine"]
    assert doc["other_projects"] == 2


def test_other_projects_is_zero_with_all(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/demo__mine.md")
    _page(tmp_vault, "shared/_inbox/project/x__theirs.md", project="other")

    _, text = _run(tmp_vault, monkeypatch, all=True)
    _, out = _run(tmp_vault, monkeypatch, all=True, json=True)

    assert "more staged for other projects" not in text
    doc = json.loads(out)
    assert len(doc["pages"]) == 2
    assert doc["other_projects"] == 0


def test_other_projects_when_nothing_is_staged_here(tmp_vault: Path, monkeypatch, cfg):
    _page(tmp_vault, "shared/_inbox/project/x__theirs.md", project="other")

    _, text = _run(tmp_vault, monkeypatch, project="demo")
    _, out = _run(tmp_vault, monkeypatch, project="demo", json=True)

    assert "1 page(s) wait for other projects" in text
    doc = json.loads(out)
    assert doc["pages"] == []
    assert doc["other_projects"] == 1
    assert doc["median_age_days"] is None and doc["oldest_age_days"] is None


def test_the_listing_document_has_a_schema_version(tmp_vault: Path, monkeypatch, cfg):
    _, out = _run(tmp_vault, monkeypatch, json=True)
    assert json.loads(out)["schema_version"] == 1


def _ledger_rows(vault: Path, rows: list) -> None:
    path = vault / ".mnemo" / "inbox-offers.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_stats_json_holds_every_number_the_text_prints(tmp_vault: Path, monkeypatch, cfg):
    for i, age in enumerate((2, 6)):
        _page(tmp_vault, f"shared/_inbox/project/demo__{i}.md", age_days=age)
    now = datetime.now()
    ts = lambda days: datetime.fromtimestamp(now.timestamp() - days * 86400).isoformat(  # noqa: E731
        timespec="seconds")
    _ledger_rows(tmp_vault, [
        {"ts": ts(5), "event": "offered", "key": "project/demo__x"},
        {"ts": ts(4), "event": "offered", "key": "project/demo__y"},
        {"ts": ts(2), "event": "promoted", "key": "project/demo__x"},
        {"ts": ts(1), "event": "dropped", "key": "project/demo__y"},
        {"ts": ts(1), "event": "expired", "key": "reference/demo__z"},
        {"ts": ts(1), "event": "restored", "key": "reference/demo__z"},
    ])

    _, text = _run(tmp_vault, monkeypatch, stats=True)
    rc, out = _run(tmp_vault, monkeypatch, stats=True, json=True)

    assert rc == 0
    doc = json.loads(out)
    assert set(doc) == {
        "schema_version", "generated_at", "window_days", "staged", "median_age_days",
        "oldest_age_days", "offered", "promoted", "dropped", "resolved", "expired",
        "restored", "median_decision_days",
    }
    assert doc["schema_version"] == 1
    # Every number the text prints is a value in the document.
    printed = sorted(float(n) for n in re.findall(r"\d+(?:\.\d+)?", text))
    carried = sorted(float(v) for k, v in doc.items()
                     if isinstance(v, (int, float)) and k != "schema_version")
    assert printed == carried
    assert doc["staged"] == 2 and doc["median_age_days"] == 4 and doc["oldest_age_days"] == 6
    assert (doc["offered"], doc["promoted"], doc["dropped"], doc["resolved"]) == (2, 1, 1, 2)
    assert (doc["expired"], doc["restored"]) == (1, 1)
    assert doc["median_decision_days"] == 3


def test_stats_json_with_nothing_decided_says_null(tmp_vault: Path, monkeypatch, cfg):
    rc, out = _run(tmp_vault, monkeypatch, stats=True, json=True)

    assert rc == 0
    doc = json.loads(out)
    assert doc["staged"] == 0
    assert doc["median_age_days"] is None and doc["oldest_age_days"] is None
    assert doc["median_decision_days"] is None
