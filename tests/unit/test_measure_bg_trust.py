"""``tools/measure_bg_trust.py``'s verdict over synthetic reports (#826)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_bg_trust.py"

# What 2.1.321 printed for an untrusted cwd, the path left out.
REFUSED = ("Workspace not trusted. Run `claude` in {cwd} once and accept the trust "
           "prompt, then retry.")


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_bg_trust", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refused(cwd: Path) -> Dict[str, Any]:
    return {"cwd": str(cwd), "trusted_by": None, "refused": REFUSED.format(cwd=cwd),
            "exit": 1, "jobs": []}


def _started(cwd: Path, entry: Path) -> Dict[str, Any]:
    return {"cwd": str(cwd), "trusted_by": entry.as_posix(), "short_id": "a1b2c3d4",
            "recorded_cwd": str(cwd)}


def _measured(tmp: Path) -> Dict[str, Any]:
    """The shape measured on 2026-11-09, on stand-in paths."""
    home, checkout = tmp / "home", tmp / "proj"
    return {
        "fresh_repo": _refused(home / "tmp" / "repo"),
        "fresh_worktree": _refused(home / "tmp" / "repo-wt-1"),
        "nested_repo": _refused(checkout / ".scratch" / "repo"),
        "plain_dir": _started(home / "tmp" / "plain", home),
        "outside_dir": _refused(tmp / "public" / "plain"),
        "worktree_outside": _started(home / "tmp" / "tree", checkout),
        "worktree_inside": _started(checkout / ".scratch" / "tree", checkout),
        "print_fresh_repo": {"answered": "OK"},
    }


def test_the_measured_shape_passes(tool, tmp_path: Path) -> None:
    assert tool.verdict(_measured(tmp_path)) == []


def test_an_arm_left_out_is_not_a_departure(tool, tmp_path: Path) -> None:
    report = _measured(tmp_path)
    del report["outside_dir"]
    assert tool.verdict(report) == []


@pytest.mark.parametrize("arm, change, expect", [
    pytest.param("fresh_repo", {"refused": None, "short_id": "a1b2c3d4"},
                 "started, but no trust entry", id="untrusted-started"),
    pytest.param("worktree_outside", {"short_id": None, "refused": "Workspace not trusted. x"},
                 "refused, though", id="trusted-refused"),
    pytest.param("fresh_worktree", {"refused": "--bg with bypassPermissions requires"},
                 "another reason", id="other-refusal"),
    pytest.param("nested_repo", {"jobs": ["deadbeef"]},
                 "left job entries", id="refusal-left-a-job"),
    pytest.param("worktree_inside", {"recorded_cwd": "/somewhere/else"},
                 "state.json records cwd", id="cwd-not-verbatim"),
    pytest.param("plain_dir", {"short_id": None},
                 "neither started nor refused", id="nothing-seen"),
    pytest.param("fresh_repo", {"error": "git init failed"},
                 "could not be set up", id="setup-failed"),
])
def test_each_departure_is_named(tool, tmp_path: Path, arm, change, expect) -> None:
    report = _measured(tmp_path)
    report[arm] = {**report[arm], **change}
    found = tool.verdict(report)
    assert any(arm in line and expect in line for line in found), found


def test_a_print_refusal_is_a_departure(tool, tmp_path: Path) -> None:
    report = _measured(tmp_path)
    report["print_fresh_repo"] = {"refused": REFUSED.format(cwd="x"), "exit": 1}
    assert any("print_fresh_repo" in line for line in tool.verdict(report))


def test_the_printed_report_hides_the_home_directory(tool, monkeypatch, capsys) -> None:
    """Reports get pasted into PRs; the CLI's sentences and the keys carry paths."""
    home = Path.home()
    plain = home / "tmp" / "plain"
    report = {"checkout": str(home / "proj"), "checkout_trusted_by": (home / "proj").as_posix(),
              "plain_dir": {"cwd": str(plain), "trusted_by": home.as_posix(),
                            "short_id": "a1b2c3d4", "recorded_cwd": str(plain)},
              "outside_dir": _refused(home / "elsewhere")}
    monkeypatch.setattr(tool, "measure", lambda *a, **k: json.loads(json.dumps(report)))
    monkeypatch.setattr(tool, "_vault", lambda: None)

    for argv in ([], ["--json"]):
        assert tool.main(argv) == 0
        out = capsys.readouterr().out
        for spelling in (str(home), home.as_posix(), json.dumps(str(home))[1:-1]):
            assert spelling not in out, out
        assert "~" in out
