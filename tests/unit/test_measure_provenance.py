"""tools/_provenance.py, and the rule that every measure tool stamps its report (#595)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import _provenance as prov  # noqa: E402

# A tool listed here may skip the helper; each needs its reason.
EXEMPT = {}  # type: dict
# A tool whose --json is a bare list keeps that shape and puts the line on stderr.
LIST_JSON = {"measure_doctor_rows": "--json prints the list of timed rows; wrapping it would break readers"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false"] + list(args),
        capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.txt").write_text("one\n", encoding="utf-8")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "first")
    return r


def test_git_state_reads_commit_and_dirty_flag(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    assert prov.git_state(repo) == (head, False)

    (repo / "scratch.json").write_text("{}", encoding="utf-8")  # untracked: still clean
    assert prov.git_state(repo) == (head, False)

    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    assert prov.git_state(repo) == (head, True)

    p = prov.provenance("tools/measure_x.py", ["--json"], root=repo)
    assert (p["commit"], p["dirty"]) == (head, True)


def test_outside_a_git_checkout_fields_are_null(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert prov.git_state(plain) == (None, None)
    assert prov.git_state(tmp_path / "missing") == (None, None)
    p = prov.provenance("measure_x", [], root=plain)
    assert p["commit"] is None and p["dirty"] is None


def test_vault_fingerprint_counts_pages_and_moves_with_them(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    (vault / ".mnemo").mkdir()
    a = vault / "shared" / "feedback" / "a.md"
    a.write_text("a", encoding="utf-8")
    (vault / "HOME.md").write_text("home", encoding="utf-8")
    (vault / ".mnemo" / "cache.md").write_text("not a page", encoding="utf-8")
    (vault / "notes.txt").write_text("not a page", encoding="utf-8")
    os.utime(a, (1_790_000_000, 1_790_000_000))
    os.utime(vault / "HOME.md", (1_780_000_000, 1_780_000_000))

    fp = prov.vault_fingerprint(vault)
    assert fp["exists"] is True and fp["pages"] == 2
    assert fp["newest"] == "2026-09-21T14:13:20Z"
    assert fp["digest"].startswith("sha256:")
    assert prov.vault_fingerprint(vault) == fp  # stable when nothing moved

    a.write_text("a, edited", encoding="utf-8")
    os.utime(a, (1_790_000_000, 1_790_000_000))
    assert prov.vault_fingerprint(vault)["digest"] != fp["digest"]  # same mtime, new size

    assert prov.vault_fingerprint(None) is None
    assert prov.vault_fingerprint(tmp_path / "nowhere") == {
        "exists": False, "pages": 0, "newest": None, "digest": None}


def test_provenance_fields_redact_home_and_private_names(tmp_path: Path, repo: Path) -> None:
    home = Path(os.path.expanduser("~"))
    vault = home / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / "private-names.tsv").write_text("secretproj\tproj-a\n", encoding="utf-8")
    (vault / "p.md").write_text("p", encoding="utf-8")
    at = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

    p = prov.provenance(str(ROOT / "tools" / "measure_x.py"),
                        ["--projects", str(home / "secretproj"), "--json"],
                        vault=vault, blind_spots=["skipped 3", None], root=repo, now=at)

    assert p["tool"] == "measure_x"
    assert p["argv"] == ["--projects", "~" + os.sep + "proj-a", "--json"]
    assert p["at"] == "2026-10-07T12:00:00Z"
    assert p["python"].count(".") == 2
    assert p["vault"]["path"] == "~" + os.sep + "vault" and p["vault"]["pages"] == 1
    assert p["blind_spots"] == ["skipped 3"]
    assert p["mnemo"] is None or "mnemo" in p["mnemo"]
    assert str(home) not in json.dumps(p)


def test_provenance_argv_none_reads_sys_argv(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["tools/measure_x.py", "--list"])
    assert prov.provenance("measure_x", root=repo)["argv"] == ["--list"]


def test_stamp_adds_one_key_and_changes_nothing_else() -> None:
    report = {"verdict": "null", "n": 3}
    stamped = prov.stamp(report, {"commit": "abc"})
    assert stamped == {"verdict": "null", "n": 3, "provenance": {"commit": "abc"}}
    assert report == {"verdict": "null", "n": 3}
    with pytest.raises(ValueError):
        prov.stamp(stamped, {})


def test_line_is_one_parseable_line() -> None:
    text = prov.line({"commit": None, "blind_spots": ["a\nb"]})
    assert "\n" not in text and text.startswith("provenance: ")
    assert json.loads(text[len("provenance: "):]) == {"commit": None, "blind_spots": ["a\nb"]}


def test_transcripts_blind_spot_names_where_history_begins(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    (projects / "-p").mkdir(parents=True)
    t = projects / "-p" / "s.jsonl"
    t.write_text("{}\n", encoding="utf-8")
    os.utime(t, (1_756_166_400, 1_756_166_400))  # 2025-08-26
    assert "2025-08-26" in prov.transcripts_blind_spot(projects)
    assert prov.transcripts_blind_spot(tmp_path / "none").startswith("no transcripts")
    assert prov.transcripts_blind_spot(None) is None


def _helper_calls(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "_provenance"}


def test_every_measure_tool_stamps_its_report() -> None:
    """A new tools/measure_*.py cannot skip provenance without an exemption and a reason."""
    tools = sorted((ROOT / "tools").glob("measure_*.py"))
    assert len(tools) >= 57
    missing = []
    for path in tools:
        if path.stem in EXEMPT:
            continue
        calls = _helper_calls(path)
        text = path.read_text(encoding="utf-8")
        need = {"provenance"}
        if path.stem in LIST_JSON:
            need.add("line")
        elif '"--json"' in text or "report.json" in text:
            need.add("stamp")
        if not need <= calls or not calls & {"stamp", "line"}:
            missing.append("%s: calls %s, needs %s" % (path.name, sorted(calls), sorted(need)))
    assert not missing, "\n".join(missing)
    assert all(EXEMPT.values()) and all(LIST_JSON.values()), "every exemption needs its reason"
    assert set(EXEMPT) | set(LIST_JSON) <= {p.stem for p in tools}, "an exemption names a tool that is gone"
