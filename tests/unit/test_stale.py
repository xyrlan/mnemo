"""``mnemo stale`` — the citation checker and what it refuses to check (#274).

The point of these tests is as much the *skipping* as the finding: the command
exists because a naive check has no precision, so the cases that must stay
silent are load-bearing, not edge cases.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core import stale as ST
from tests.unit._export_fixtures import write_rule


def _repo(root: Path, files: dict[str, str]) -> Path:
    """A real git repo with *files* committed — `git ls-tree HEAD` must work."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    import os
    env = {**os.environ, **env}
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True, env=env)
    return root


# --- parsing: what counts as a path citation at all ------------------------

@pytest.mark.parametrize("span,expected", [
    ("src/mnemo/cli/parser.py", "src/mnemo/cli/parser.py"),
    ("src/parser.py:45", "src/parser.py"),
    ("core/reflex/index.py:32-37", "core/reflex/index.py"),
    ("./engine/build.ps1", "engine/build.ps1"),
    # A leading dot *segment* is part of the path and must survive: stripping
    # it made every dotfile citation read as stale (`.github/` -> `github/`).
    (".github/workflows/ci.yml:124", ".github/workflows/ci.yml"),
    (".claude-plugin/marketplace.json", ".claude-plugin/marketplace.json"),
    # Next.js route groups and dynamic segments are real tracked directories;
    # excluding `()`/`[]` dropped these citations instead of checking them.
    ("src/app/(auth)/_lib/format.ts", "src/app/(auth)/_lib/format.ts"),
    ("src/app/[id]/page.tsx", "src/app/[id]/page.tsx"),
])
def test_parses_path_citations(span, expected):
    cite = ST.parse_citation(span)
    assert cite is not None and cite.path == expected


@pytest.mark.parametrize("span", [
    "resolve_agent",             # a bare identifier
    "os.kill(pid, 0)",           # a call
    "mnemo stale --json",        # a command line
    "--apply",                   # a flag
    "mfull1/2/3.png",            # prose that happens to have slashes
    "trading/bot.start",         # ditto, and `.start` is not a source ext
    "ci.yml",                    # single segment, no directory
])
def test_rejects_non_path_spans(span):
    assert ST.parse_citation(span) is None


def test_line_suffix_is_kept_but_not_checked():
    """A moved line inside a file that still exists is not a broken citation."""
    cite = ST.parse_citation("src/a.py:45")
    assert cite is not None and cite.line == "45"


# --- skipping: paths the repo is not authoritative about -------------------

@pytest.mark.parametrize("span", [
    "node_modules/next/dist/lib/x.js",
    "dist/main.js",
    "build/exports.json",
    "target/release/app.rs",
    ".mnemo/recall-report.json",
    ".obsidian/app.json",
    "shared/feedback/rule.md",
    "docs/superpowers/plans/2026-09-09-coletor.md",
])
def test_untracked_by_design_is_skipped(span):
    cite = ST.parse_citation(span)
    assert cite is not None, span
    assert ST.skip_reason(cite) is not None


def test_source_path_is_checkable():
    cite = ST.parse_citation("src/lib/format.ts")
    assert cite is not None and ST.skip_reason(cite) is None


# --- the hedge: a path the rule proposes is not a path it cites ------------

@pytest.mark.parametrize("preceding", [
    "**How to apply:** create ",
    "extract formatter to a shared utility (e.g., ",
    "Create a mock version of each external API (e.g. ",
    "- **Files** — path globs affected (e.g., ",
    "provide an idempotent backfill script (such as ",
])
def test_hedged_citations_are_not_flagged(preceding):
    assert ST.is_hedged(preceding)


@pytest.mark.parametrize("preceding", [
    "the check lives in ",
    "2026-09-12: ",
    "sets `continue-on-error: true` in ",
    # Word-boundary regressions: an unanchored `like` matched "unlike" and a
    # bare `add` matched "we add the guard in", suppressing real citations.
    "the fix is unlike the one in ",
    "we add the guard in ",
])
def test_plain_citations_are_flagged(preceding):
    assert not ST.is_hedged(preceding)


def test_hedged_path_lands_in_skipped_not_missing():
    body = "**How to apply:** create `src/lib/gone.ts` with the helper.\n"
    finding = ST.check_page(Path("p.md"), body, "slug", tracked={"src/lib/here.ts"})
    assert finding.missing == []
    assert [reason for _, reason in finding.skipped] == [
        "proposed or illustrative, not cited"
    ]
    assert not finding.is_stale


# --- identifiers: counted, never flagged ----------------------------------

def test_identifiers_are_counted_not_checked():
    """0 of 153 unresolvable identifiers on the real vault had ever been
    defined in their repo. They are reported as skipped, never as findings."""
    body = (
        "`resolve_agent` and `os_kill_impl` and `login_customer_id` "
        "and `formatDistanceToNow()` are all absent from HEAD.\n"
    )
    assert ST.count_identifiers(body) == 4
    finding = ST.check_page(Path("p.md"), body, "slug", tracked={"src/a.py"})
    assert finding.missing == [] and not finding.is_stale


# --- relocation: the actionable half --------------------------------------

def test_unique_basename_says_where_it_went():
    assert ST.relocated("src/parser.py", {"src/mnemo/cli/parser.py"}) == \
        "src/mnemo/cli/parser.py"


def test_ambiguous_basename_offers_no_guess():
    """Two candidates is a guess, and a wrong 'did you mean' is worse than none."""
    assert ST.relocated("a/format.ts", {"x/format.ts", "y/format.ts"}) is None


def test_suffix_match_is_anchored_on_a_separator():
    """`lib/a.py` must not resolve against `src/xlib/a.py`."""
    finding = ST.check_page(
        Path("p.md"), "see `lib/a.py`\n", "slug", tracked={"src/xlib/a.py"},
    )
    assert finding.resolved == [] and len(finding.missing) == 1


def test_nested_match_resolves_without_being_a_finding():
    """A rule citing `core/llm.py` for `src/mnemo/core/llm.py` is not stale."""
    finding = ST.check_page(
        Path("p.md"), "see `core/llm.py`\n", "slug",
        tracked={"src/mnemo/core/llm.py"},
    )
    assert finding.missing == [] and len(finding.resolved) == 1


# --- run(): end to end over a real vault and a real repo ------------------

def test_run_finds_the_moved_path(tmp_vault, tmp_path):
    repo = _repo(tmp_path / "app", {"src/mnemo/cli/parser.py": "x = 1\n"})
    write_rule(tmp_vault, slug="cites-moved", projects=("app",),
               body="The parser lives in `src/parser.py` today.\n")
    report = ST.run(tmp_vault, project="app", repo_root=repo)

    assert report.pages_scanned == 1
    assert len(report.stale_pages) == 1
    cite, moved = report.stale_pages[0].missing[0]
    assert cite.path == "src/parser.py"
    assert moved == "src/mnemo/cli/parser.py"
    assert report.rate == 1.0


def test_run_is_silent_when_every_citation_resolves(tmp_vault, tmp_path):
    repo = _repo(tmp_path / "app", {"src/a.py": "x = 1\n"})
    write_rule(tmp_vault, slug="ok", projects=("app",), body="see `src/a.py`\n")
    report = ST.run(tmp_vault, project="app", repo_root=repo)
    assert report.pages_scanned == 1 and report.stale_pages == []
    assert report.paths_resolved == 1


def test_run_ignores_other_projects_rules(tmp_vault, tmp_path):
    repo = _repo(tmp_path / "app", {"src/a.py": "x = 1\n"})
    write_rule(tmp_vault, slug="elsewhere", projects=("other",),
               body="see `src/gone.py`\n")
    report = ST.run(tmp_vault, project="app", repo_root=repo)
    assert report.pages_scanned == 0 and report.stale_pages == []


def test_run_ignores_inbox_and_evolving(tmp_vault, tmp_path):
    repo = _repo(tmp_path / "app", {"src/a.py": "x = 1\n"})
    write_rule(tmp_vault, slug="staged", projects=("app",),
               body="see `src/gone.py`\n", inbox=True)
    write_rule(tmp_vault, slug="fluid", projects=("app",),
               body="see `src/gone.py`\n", stability="evolving")
    report = ST.run(tmp_vault, project="app", repo_root=repo)
    assert report.pages_scanned == 0 and report.stale_pages == []


def test_graph_section_wikilinks_are_not_citations(tmp_vault, tmp_path):
    """The trailing `## Sources` block is mnemo's own metadata."""
    repo = _repo(tmp_path / "app", {"src/a.py": "x = 1\n"})
    write_rule(tmp_vault, slug="linked", projects=("app",),
               body="see `src/a.py`\n", graph_section=True)
    report = ST.run(tmp_vault, project="app", repo_root=repo)
    assert report.stale_pages == []


def test_run_on_a_non_repo_scans_nothing(tmp_vault, tmp_path):
    plain = tmp_path / "notgit"
    plain.mkdir()
    write_rule(tmp_vault, slug="x", projects=("app",), body="see `src/gone.py`\n")
    report = ST.run(tmp_vault, project="app", repo_root=plain)
    assert report.pages_scanned == 0


def test_read_only(tmp_vault, tmp_path):
    """No `--apply`, and the run must not touch a byte of the vault."""
    repo = _repo(tmp_path / "app", {"src/a.py": "x = 1\n"})
    write_rule(tmp_vault, slug="cites-moved", projects=("app",),
               body="The parser is `src/parser.py`.\n")
    before = {p: p.read_bytes() for p in (tmp_vault / "shared").rglob("*.md")}
    ST.run(tmp_vault, project="app", repo_root=repo)
    after = {p: p.read_bytes() for p in (tmp_vault / "shared").rglob("*.md")}
    assert before == after


def test_format_report_names_the_move(tmp_vault, tmp_path):
    repo = _repo(tmp_path / "app", {"src/mnemo/cli/parser.py": "x = 1\n"})
    write_rule(tmp_vault, slug="cites-moved", projects=("app",),
               body="The parser is `src/parser.py`.\n")
    out = ST.format_report(ST.run(tmp_vault, project="app", repo_root=repo))
    assert "src/parser.py" in out
    assert "now src/mnemo/cli/parser.py" in out
    # The report must say what it did not check, or the number reads as
    # "everything else is fine".
    assert "not checked" in out


# --- CLI + doctor wiring --------------------------------------------------

def test_cli_reports_and_exits_zero(tmp_vault, tmp_path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli.runtime import main

    repo = _repo(tmp_path / "app", {"src/mnemo/cli/parser.py": "x = 1\n"})
    write_rule(tmp_vault, slug="cites-moved", projects=("app",),
               body="The parser is `src/parser.py`.\n")
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.chdir(repo)

    assert main(["stale"]) == 0
    assert "src/parser.py" in capsys.readouterr().out


def test_cli_json_is_machine_readable(tmp_vault, tmp_path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli.runtime import main

    repo = _repo(tmp_path / "app", {"src/mnemo/cli/parser.py": "x = 1\n"})
    write_rule(tmp_vault, slug="cites-moved", projects=("app",),
               body="The parser is `src/parser.py`.\n")
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.chdir(repo)

    assert main(["stale", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale_pages"] == 1
    assert payload["findings"][0]["missing"][0]["moved_to"] == "src/mnemo/cli/parser.py"


def test_cli_why_explains_the_refusal(monkeypatch, capsys):
    from mnemo.cli.runtime import main

    assert main(["stale", "--why"]) == 0
    out = capsys.readouterr().out
    assert "identifiers" in out or "identifier" in out


def test_cli_outside_a_repo_says_so(tmp_vault, tmp_path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli.runtime import main

    plain = tmp_path / "notgit"
    plain.mkdir()
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.chdir(plain)
    assert main(["stale"]) == 2
    assert "not in a git repository" in capsys.readouterr().out


def test_stale_is_registered():
    from mnemo.cli.parser import COMMANDS
    assert "stale" in COMMANDS


def test_doctor_row_is_registered():
    from mnemo.cli.commands import doctor
    assert "stale_citations" in [name for name, _ in doctor.DOCTOR_CHECKS]


def test_doctor_check_warns_once_stale(tmp_vault, tmp_path, monkeypatch, capsys):
    from mnemo.cli.commands.doctor_checks.stale import _doctor_check_stale_citations

    repo = _repo(tmp_path / "app", {"src/mnemo/cli/parser.py": "x = 1\n"})
    write_rule(tmp_vault, slug="cites-moved", projects=("app",),
               body="The parser is `src/parser.py`.\n")
    monkeypatch.chdir(repo)
    assert _doctor_check_stale_citations(tmp_vault) is False
    assert "cite a file not in" in capsys.readouterr().out


def test_doctor_check_is_silent_outside_a_repo(tmp_vault, tmp_path, monkeypatch, capsys):
    from mnemo.cli.commands.doctor_checks.stale import _doctor_check_stale_citations

    plain = tmp_path / "notgit"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert _doctor_check_stale_citations(tmp_vault) is True
    assert capsys.readouterr().out == ""
