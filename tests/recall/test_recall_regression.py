"""Live retrieval regression — runs one parametrized test per case.

Marked ``recall`` so it can be opted in/out:
    pytest -m recall              # only recall regression
    pytest -m 'not recall'        # everything else (default CI)

Skips silently when cases.json is empty (fresh checkout) or when the configured
vault is absent (CI environment without a real vault).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mnemo.core.mcp.recall import aggregate, format_report, run_case
from mnemo.core.paths import vault_root as resolve_vault_root


_CASES_PATH = Path(__file__).parent / "cases.json"
_REPORT_PATH = Path(__file__).parent / "last-report.json"


def _load_cases() -> list[dict]:
    if not _CASES_PATH.is_file():
        return []
    try:
        return json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _resolve_vault() -> Path | None:
    cfg_path = Path.cwd() / "mnemo.config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    override = os.environ.get("MNEMO_RECALL_VAULT")
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    try:
        v = resolve_vault_root(cfg)
    except Exception:
        return None
    return v if v.is_dir() else None


_CASES = _load_cases()
_VAULT = _resolve_vault()


pytestmark = pytest.mark.recall


@pytest.mark.skipif(not _CASES, reason="cases.json is empty — run tests/recall/bootstrap.py first")
@pytest.mark.skipif(_VAULT is None, reason="no vault resolved (set MNEMO_RECALL_VAULT or run inside a vault)")
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["id"])
def test_case_hits_top_10(case: dict, record_property) -> None:
    """Each case must find its expected slug within top 10 of the live ranking."""
    assert _VAULT is not None  # for the type checker; skipif guards runtime
    result = run_case(_VAULT, case)
    record_property("result", result)
    assert result["rank"] is not None, (
        f"slug {case['expect_slug']!r} not returned for "
        f"topic={case['topic']!r} project={case['project']!r} "
        f"(got {result['result_count']} results)"
    )
    assert result["rank"] <= 10, (
        f"slug {case['expect_slug']!r} ranked #{result['rank']} "
        f"(expected top 10) — regression vs bootstrap rank "
        f"#{case.get('rank_at_bootstrap')}"
    )


@pytest.fixture(scope="session", autouse=True)
def _write_session_report(request):
    """At session end, re-run every case non-fatally and emit a combined report."""
    yield
    if not _CASES or _VAULT is None:
        return
    results = [run_case(_VAULT, c) for c in _CASES]
    report = aggregate(results)
    _REPORT_PATH.write_text(
        json.dumps({"report": report, "results": results}, indent=2) + "\n",
        encoding="utf-8",
    )
    terminalreporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if terminalreporter is not None:
        terminalreporter.write_sep("=", "recall report")
        terminalreporter.write_line(format_report(report))
        terminalreporter.write_line(f"(written to {_REPORT_PATH})")
