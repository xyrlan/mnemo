"""``mnemo procedures`` — the proposed ``CLAUDE.md`` line, and the two decisions (#392).

The listing is the whole surface: ``--accept`` is the only write, it appends,
and there is no ``--accept-all``, because every line costs every session in
that repo for ever.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, INTERNAL_COMMANDS, _build_parser


def _child(projects: Path, repo_root: Path, worktree: str, commands: list) -> None:
    cwd = str(repo_root.parent / worktree)
    records = [{
        "type": "assistant", "cwd": cwd, "timestamp": "2026-09-19T10:00:00Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]},
    } for i, c in enumerate(commands)]
    directory = projects / worktree
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{len(list(directory.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _population(tmp_path: Path) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])
    return projects, repo


def _run(vault: Path, projects: Path, monkeypatch, *, repo: str | None = "app",
         **flags) -> tuple[int, str]:
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    from mnemo.cli.commands import procedures as cmd

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr(cmd, "_repo_here", lambda: repo)
    defaults = {"all": False, "show": None, "accept": None, "drop": None,
                "json": False, "projects": str(projects)}
    ns = argparse.Namespace(**{**defaults, **flags})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = COMMANDS["procedures"](ns)
    return rc, buf.getvalue()


def test_procedures_is_a_user_facing_command_with_a_handler() -> None:
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "procedures" in COMMANDS
    assert "procedures" not in ADVANCED_COMMANDS and "procedures" not in INTERNAL_COMMANDS
    ns = _build_parser().parse_args(["procedures", "--accept", "cargo-test"])
    assert ns.command == "procedures" and ns.accept == "cargo-test"


def test_the_listing_writes_nothing_and_says_so(tmp_path: Path, monkeypatch) -> None:
    projects, repo = _population(tmp_path)

    rc, out = _run(tmp_path / "vault", projects, monkeypatch)

    assert rc == 0
    assert "cargo-test" in out and "PROPOSE" in out
    assert "nothing was written" in out
    assert not (repo / "CLAUDE.md").exists()


def test_a_repo_with_nothing_rediscovered_says_so(tmp_path: Path, monkeypatch) -> None:
    projects, _repo = _population(tmp_path)
    rc, out = _run(tmp_path / "vault", projects, monkeypatch, repo="other")
    assert rc == 0
    assert "no procedure is rediscovered" in out


def test_show_prints_the_line_and_who_paid_for_it(tmp_path: Path, monkeypatch) -> None:
    projects, _repo = _population(tmp_path)
    rc, out = _run(tmp_path / "vault", projects, monkeypatch, show="cargo-test")
    assert rc == 0
    assert "app-wt-1" in out and "app-wt-2" in out
    assert "## Run `cargo test` with `SDKROOT`" in out


def test_accept_appends_the_section_to_the_repos_file(tmp_path: Path, monkeypatch) -> None:
    projects, repo = _population(tmp_path)
    (repo / "CLAUDE.md").write_text("# app\n\nMine.\n", encoding="utf-8")

    rc, out = _run(tmp_path / "vault", projects, monkeypatch, accept="cargo-test")

    assert rc == 0 and "appended" in out
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# app\n\nMine.\n")
    assert "SDKROOT=/sdk cargo test" in text


def test_accept_twice_writes_once(tmp_path: Path, monkeypatch) -> None:
    """The second run sees its own line and refuses; the ledger also holds it."""
    projects, repo = _population(tmp_path)
    vault = tmp_path / "vault"
    _run(vault, projects, monkeypatch, accept="cargo-test")
    rc, out = _run(vault, projects, monkeypatch, accept="cargo-test")
    assert rc == 1
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count("## Run `cargo test`") == 1


def test_drop_writes_nothing_and_takes_it_out_of_the_queue(tmp_path: Path, monkeypatch) -> None:
    projects, repo = _population(tmp_path)
    vault = tmp_path / "vault"

    rc, _out = _run(vault, projects, monkeypatch, drop="cargo-test")
    assert rc == 0
    assert not (repo / "CLAUDE.md").exists()

    _rc, out = _run(vault, projects, monkeypatch)
    assert "cargo-test" not in out


def test_an_unknown_key_is_refused_not_guessed(tmp_path: Path, monkeypatch) -> None:
    projects, _repo = _population(tmp_path)
    rc, out = _run(tmp_path / "vault", projects, monkeypatch, accept="npm-test")
    assert rc == 1 and "no candidate named npm-test" in out


def test_two_actions_at_once_are_refused(tmp_path: Path, monkeypatch) -> None:
    projects, repo = _population(tmp_path)
    rc, out = _run(tmp_path / "vault", projects, monkeypatch,
                   accept="cargo-test", drop="cargo-test")
    assert rc == 1 and "pick one action" in out
    assert not (repo / "CLAUDE.md").exists()


def test_json_is_machine_readable(tmp_path: Path, monkeypatch) -> None:
    projects, _repo = _population(tmp_path)
    _rc, out = _run(tmp_path / "vault", projects, monkeypatch, json=True)
    rows = json.loads(out)
    assert [(r["repo"], r["key"], r["stated"]) for r in rows] == [("app", "cargo-test", False)]


# --- the cache the session-start offer reads (#397) ------------------------


def test_refresh_and_stats_are_flags_of_this_command(tmp_path: Path) -> None:
    ns = _build_parser().parse_args(["procedures", "--refresh"])
    assert ns.refresh is True and ns.stats is False
    assert _build_parser().parse_args(["procedures", "--stats"]).stats is True


def test_refresh_writes_the_cache_and_no_repo_file(tmp_path: Path, monkeypatch) -> None:
    from mnemo.core import procedures as P

    projects, repo = _population(tmp_path)
    vault = tmp_path / "vault"

    rc, out = _run(vault, projects, monkeypatch, refresh=True)

    assert rc == 0 and "1 undecided" in out
    assert [c.key for c in P.read_cache(vault)[0]] == ["cargo-test"]
    assert not (repo / "CLAUDE.md").exists(), "a refresh writes nothing to a repo"


def test_refresh_stamps_the_marker_so_the_hook_does_not_respawn_it(
    tmp_path: Path, monkeypatch
) -> None:
    """A refresh run by hand is the same work the hook spawns, so it counts."""
    from mnemo.core import procedures as P
    from mnemo.core.config import DEFAULTS

    projects, _repo = _population(tmp_path)
    vault = tmp_path / "vault"

    _run(vault, projects, monkeypatch, refresh=True)

    assert P.scan_is_due(vault, DEFAULTS) is False


def test_stats_says_plainly_when_nothing_has_been_offered_and_decided(
    tmp_path: Path, monkeypatch
) -> None:
    projects, _repo = _population(tmp_path)

    rc, out = _run(tmp_path / "vault", projects, monkeypatch, stats=True)

    assert rc == 0
    assert "1 undecided candidate(s) across 1 repo(s)" in out
    assert "no candidate has been both offered and decided yet" in out


def test_stats_measures_the_offer_from_being_shown_to_being_decided(
    tmp_path: Path, monkeypatch
) -> None:
    """The number the offer exists to be judged on (#390's precedent, mirrored)."""
    from mnemo.core import procedures as P

    projects, _repo = _population(tmp_path)
    vault = tmp_path / "vault"
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")
    P.record(vault, event=P.DROPPED, repo="app", key="cargo-test")

    rc, out = _run(vault, projects, monkeypatch, stats=True)

    assert rc == 0
    assert "1 offered at session start, 0 accepted, 1 dropped (1 resolved)" in out
    assert "median offer → decision: 0d" in out


def test_refresh_and_an_action_at_once_are_refused(tmp_path: Path, monkeypatch) -> None:
    projects, repo = _population(tmp_path)

    rc, out = _run(tmp_path / "vault", projects, monkeypatch,
                   refresh=True, accept="cargo-test")

    assert rc == 1 and "pick one action" in out
    assert not (repo / "CLAUDE.md").exists()
