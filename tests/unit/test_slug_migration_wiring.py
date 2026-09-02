"""#114: the slug migration runs from session start and extract; doctor
reports the gap. Every index built after it keys by the kebab slug, not the
display name."""
from __future__ import annotations

import io
import json
from pathlib import Path

from mnemo.cli.commands.doctor_checks import rules as doctor_rules
from mnemo.core import llm as llm_mod
from mnemo.core import rule_activation
from mnemo.core.extract import run_extraction
from mnemo.core.migrations import slugs as slugs_mod
from mnemo.core.reflex import index as reflex_index
from mnemo.hooks import session_start

LEGACY = (
    "---\n"
    "name: Use Yarn\n"
    "description: d\n"
    "type: feedback\n"
    "sources:\n"
    "  - bots/p/briefings/sessions/s.md\n"
    "tags:\n"
    "  - auto-promoted\n"
    "---\n"
    "Always use yarn for package management in this repository, never npm.\n"
)


def _vault(tmp_path: Path, monkeypatch) -> Path:
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    (vault / "shared" / "feedback" / "use-yarn.md").write_text(LEGACY, encoding="utf-8")
    cfg = tmp_path / "mnemo.config.json"
    cfg.write_text(
        json.dumps({
            "vaultRoot": str(vault),
            "injection": {"enabled": True},
            "reflex": {"enabled": True},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg))
    return vault


def _run_session_start(tmp_path: Path, monkeypatch) -> int:
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "s", "cwd": str(tmp_path)})),
    )
    return session_start.main()


def test_session_start_migrates_then_indexes_by_slug(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)

    assert _run_session_start(tmp_path, monkeypatch) == 0

    page = (vault / "shared" / "feedback" / "use-yarn.md").read_text(encoding="utf-8")
    assert "slug: use-yarn" in page

    idx = rule_activation.load_index(vault)
    assert idx is not None
    assert "use-yarn" in idx["rules"]
    assert "Use Yarn" not in idx["rules"]

    ridx = reflex_index.load_index(vault)
    assert ridx is not None
    assert "use-yarn" in ridx["docs"]
    assert "Use Yarn" not in ridx["docs"]

    assert (vault / slugs_mod.MARKER_REL).exists()


def test_session_start_skips_the_scan_once_the_marker_exists(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    slugs_mod.write_marker(vault)

    assert _run_session_start(tmp_path, monkeypatch) == 0

    page = (vault / "shared" / "feedback" / "use-yarn.md").read_text(encoding="utf-8")
    assert "slug:" not in page, "marker present → migration must not run"


def test_session_start_withholds_the_marker_when_a_page_was_skipped(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    (vault / "shared" / "feedback" / "broken.md").write_text(
        "no frontmatter here\n", encoding="utf-8"
    )

    assert _run_session_start(tmp_path, monkeypatch) == 0

    page = (vault / "shared" / "feedback" / "use-yarn.md").read_text(encoding="utf-8")
    assert "slug: use-yarn" in page
    assert not (vault / slugs_mod.MARKER_REL).exists()


def test_doctor_reports_missing_slugs(tmp_path, capsys):
    (tmp_path / "shared" / "feedback").mkdir(parents=True)
    (tmp_path / "shared" / "feedback" / "a.md").write_text(LEGACY, encoding="utf-8")

    assert doctor_rules._doctor_check_missing_slugs(tmp_path) is True

    out = capsys.readouterr().out
    assert "1 page(s) missing slug:" in out
    # dry-run only: doctor never rewrites pages
    assert "slug:" not in (tmp_path / "shared" / "feedback" / "a.md").read_text(encoding="utf-8")


def test_doctor_is_quiet_when_every_page_has_a_slug(tmp_path, capsys):
    (tmp_path / "shared" / "feedback").mkdir(parents=True)
    (tmp_path / "shared" / "feedback" / "a.md").write_text(
        LEGACY.replace("name: Use Yarn\n", "name: Use Yarn\nslug: a\n"), encoding="utf-8"
    )

    assert doctor_rules._doctor_check_missing_slugs(tmp_path) is True
    assert "every rule page carries slug:" in capsys.readouterr().out


def test_doctor_registers_the_check_after_rule_integrity():
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    names = [name for name, _ in DOCTOR_CHECKS]
    assert names.index("missing_slugs") == names.index("rule_integrity") + 1


def _extract_cfg(vault_root: Path) -> dict:
    return {
        "vaultRoot": str(vault_root),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "hintThreshold": 5,
            "preferAPI": False,
            "subprocessTimeout": 60,
            "costSoftCap": None,
        },
    }


def test_extract_migrates_legacy_pages_before_applying(populated_vault: Path, monkeypatch):
    legacy = populated_vault / "shared" / "feedback" / "use-yarn.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(LEGACY, encoding="utf-8")

    text = json.dumps({"pages": []})
    calls: list[str] = []

    def fake_call(prompt, *, system, model, timeout):
        calls.append(prompt)
        return llm_mod.LLMResponse(
            text=text, total_cost_usd=0.0, input_tokens=1, output_tokens=1,
            api_key_source="none", raw={"result": text},
        )

    monkeypatch.setattr(llm_mod, "call", fake_call)

    run_extraction(_extract_cfg(populated_vault))

    assert "slug: use-yarn" in legacy.read_text(encoding="utf-8")
    idx = rule_activation.load_index(populated_vault)
    assert idx is not None and "use-yarn" in idx["rules"] and "Use Yarn" not in idx["rules"]
