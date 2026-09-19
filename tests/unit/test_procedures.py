"""``core.procedures``: what a child worked out for itself, and the line it proposes (#392).

Transcripts here are shaped like Claude Code's — ``cwd`` on every event,
``message.content`` blocks, ``tool_use`` for Bash — and named like dispatch
worktrees, because that population is what the module reads.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import procedures as P


# --- reading a command -----------------------------------------------------


def _one(command: str):
    return list(P.invocations(command))


def test_an_env_prefix_is_a_carrier_of_the_shape_it_precedes() -> None:
    (shape, env, flags), = _one("PYTHONPATH=src python3 -m pytest -q")
    assert shape == "python3 pytest"
    assert env == {"PYTHONPATH": "src"}
    assert flags == ()


def test_a_runner_names_the_work_one_token_further_on() -> None:
    (shape, env, _), = _one("CARGO_TARGET_DIR=/tmp/t pnpm tauri dev --port 4321")
    assert shape == "pnpm tauri dev"
    assert env == {"CARGO_TARGET_DIR": "/tmp/t"}


def test_flags_come_back_beside_the_environment() -> None:
    (_shape, _env, flags), = _one("cargo test -- --nocapture --ignored")
    assert flags == ("--ignored", "--nocapture")


def test_each_statement_is_its_own_invocation() -> None:
    """One bare run and one correct one, not two correct ones."""
    runs = _one("pytest; PYTHONPATH=src pytest")
    assert [shape for shape, _e, _f in runs] == ["pytest", "pytest"]
    assert [env for _s, env, _f in runs] == [{}, {"PYTHONPATH": "src"}]


def test_a_pipe_does_not_start_a_new_run_of_the_same_command() -> None:
    """``cargo test | tail`` is one run of cargo and one of tail, not two of cargo."""
    shapes = [shape for shape, _e, _f in _one("SDKROOT=/sdk cargo test | tail -5")]
    assert shapes == ["cargo test", "tail"]


def test_an_export_reaches_the_statements_after_it_and_no_further() -> None:
    runs = _one("export SDKROOT=/sdk && cargo build && cargo test")
    assert [shape for shape, _e, _f in runs] == ["cargo build", "cargo test"]
    assert all(env == {"SDKROOT": "/sdk"} for _s, env, _f in runs)


def test_a_heredoc_body_that_quotes_a_command_is_not_a_run_of_it() -> None:
    command = "cat > t.py <<'EOF'\nsubprocess.run(['cargo', 'test'])\nEOF"
    assert [shape for shape, _e, _f in _one(command)] == ["cat"]


def test_a_quoted_pipe_does_not_split_a_statement() -> None:
    (shape, env, _), = _one("PYTHONPATH=src python3 -c \"print('a|b')\"")
    assert (shape, env) == ("python3", {"PYTHONPATH": "src"})


def test_the_live_suites_own_worktrees_are_not_children() -> None:
    assert P.is_test_tree("/private/var/pytest-of-x/t0/live-wt-1") is True
    assert P.is_test_tree("/private/var/pytest-of-x/t0/live-model-wt-3") is True
    assert P.is_test_tree("/private/var/pytest-of-x/t0/app-wt-1") is False
    assert P.is_test_tree("/Users/x/github/mnemo-wt-392") is False


# --- the three states ------------------------------------------------------


def test_a_carrier_on_every_run_is_kept() -> None:
    assert P.verdicts([{"PYTHONPATH": "src"}, {"PYTHONPATH": "src"}]) == {"PYTHONPATH": "kept"}


def test_a_carrier_added_after_a_bare_run_is_rediscovered() -> None:
    assert P.verdicts([{}, {"PYTHONPATH": "src"}]) == {"PYTHONPATH": "rediscovered"}


def test_a_carrier_dropped_after_a_correct_run_is_not_rediscovered() -> None:
    """Order is the whole measurement: right first, then wrong, is not learning."""
    assert P.verdicts([{"PYTHONPATH": "src"}, {}]) == {"PYTHONPATH": "never"}


# --- the scan --------------------------------------------------------------


def _child(projects: Path, repo_root: Path, worktree: str, commands: list,
           *, day: str = "2026-09-19") -> None:
    cwd = str(repo_root.parent / worktree)
    records = [{
        "type": "assistant", "cwd": cwd, "timestamp": f"{day}T10:00:00Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]},
    } for i, c in enumerate(commands)]
    directory = projects / worktree.replace("/", "-")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{len(list(directory.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _repo(tmp_path: Path, name: str, claude_md: str = "") -> Path:
    root = tmp_path / "repos" / name
    root.mkdir(parents=True, exist_ok=True)
    if claude_md:
        (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    return root


def test_two_children_of_one_repo_make_a_candidate(tmp_path: Path) -> None:
    projects, repo = tmp_path / "projects", _repo(tmp_path, "app")
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])

    candidate, = P.scan(str(projects))
    assert (candidate.repo, candidate.key) == ("app", "cargo-test")
    assert candidate.rediscovered == ("app-wt-1", "app-wt-2")
    assert [c.name for c in candidate.carriers] == ["SDKROOT"]
    assert candidate.command == "SDKROOT=/sdk cargo test"


def test_one_child_working_something_out_is_a_child_not_a_procedure(tmp_path: Path) -> None:
    projects, repo = tmp_path / "projects", _repo(tmp_path, "app")
    _child(projects, repo, "app-wt-1", ["cargo test", "SDKROOT=/sdk cargo test"])
    _child(projects, repo, "app-wt-2", ["cargo test"])
    assert P.scan(str(projects)) == []


def test_a_shape_many_repos_run_is_the_harness_not_this_repo(tmp_path: Path) -> None:
    """``git log`` is run in every repo, so a carrier on it says nothing here."""
    projects = tmp_path / "projects"
    for repo_name in ("app", "web", "api"):
        repo = _repo(tmp_path, repo_name)
        for n in (1, 2):
            _child(projects, repo, f"{repo_name}-wt-{n}", ["git log", "PAGER=cat git log"])
    assert P.scan(str(projects)) == []
    assert len(P.scan(str(projects), max_shape_repos=3)) == 3


def test_carriers_of_one_shape_are_one_proposed_line(tmp_path: Path) -> None:
    projects, repo = tmp_path / "projects", _repo(tmp_path, "app")
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}",
               ["pnpm test", "DEVELOPER_DIR=/clt PATH=/clt/bin pnpm test"])
    candidate, = P.scan(str(projects))
    assert [c.name for c in candidate.carriers] == ["DEVELOPER_DIR", "PATH"]
    assert candidate.command == "DEVELOPER_DIR=/clt PATH=/clt/bin pnpm test"


def test_a_carrier_the_file_already_states_is_listed_but_not_proposed(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    repo = _repo(tmp_path, "app", "# app\n\nRun `SDKROOT=/sdk cargo test`.\n")
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])
    candidate, = P.scan(str(projects))
    assert candidate.stated is True
    assert candidate.unstated == ()


def test_a_worktree_of_a_worktree_folds_to_the_same_repo(tmp_path: Path) -> None:
    projects, repo = tmp_path / "projects", _repo(tmp_path, "app")
    _child(projects, repo, "app-wt-round6-wt-1", ["cargo test", "SDKROOT=/sdk cargo test"])
    _child(projects, repo, "app-wt-2", ["cargo test", "SDKROOT=/sdk cargo test"])
    candidate, = P.scan(str(projects))
    assert candidate.repo == "app"
    assert len(candidate.rediscovered) == 2


def test_flags_are_only_reachable_on_purpose(tmp_path: Path) -> None:
    """The same bar over flags is what ``--rejected`` counts; it is never the default."""
    projects, repo = tmp_path / "projects", _repo(tmp_path, "app")
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "cargo test -- --nocapture"])
    assert P.scan(str(projects)) == []
    candidate, = P.scan(str(projects), kind=P.FLAG)
    assert [c.name for c in candidate.carriers] == ["--nocapture"]


# --- what the repo already says --------------------------------------------


def test_a_carrier_inside_a_longer_name_is_not_stated() -> None:
    assert P.states("MY_PYTHONPATH_HACK=1", "python3 pytest", "PYTHONPATH") is False
    assert P.states("PYTHONPATH=src pytest", "python3 pytest", "PYTHONPATH") is True


def test_a_flag_is_found_although_it_starts_with_a_dash() -> None:
    assert P.states("npm test -- --runInBand", "npm test", "--runInBand") is True


# --- the proposed line -----------------------------------------------------


def _candidate(tmp_path: Path, **over) -> P.Candidate:
    carrier = P.Carrier(name="SDKROOT", values=(("/sdk", 3),), rediscovered=("a", "b"),
                        kept=0, never=1, stated=False)
    fields = dict(repo="app", shape="cargo test", carriers=(carrier,), shape_children=9,
                  first_day="2026-09-01", last_day="2026-09-09",
                  repo_root=str(_repo(tmp_path, "app")))
    fields.update(over)
    return P.Candidate(**fields)


def test_the_proposed_section_says_what_was_measured(tmp_path: Path) -> None:
    text = P.proposed_section(_candidate(tmp_path), today="2026-09-19")
    assert text.startswith("## Run `cargo test` with `SDKROOT`")
    assert "SDKROOT=/sdk cargo test" in text
    assert "2 dispatched children of the 9" in text
    assert len(text.splitlines()) == 7


def test_a_value_children_spelled_differently_says_so(tmp_path: Path) -> None:
    carrier = P.Carrier(name="CARGO_TARGET_DIR", values=(("/a", 3), ("/b", 2)),
                        rediscovered=("a", "b"), kept=0, never=0, stated=False)
    text = P.proposed_section(_candidate(tmp_path, carriers=(carrier,)), today="2026-09-19")
    assert "2 values" in text


def test_a_stated_carrier_is_left_out_of_the_section(tmp_path: Path) -> None:
    stated = P.Carrier(name="PATH", values=(("/clt/bin", 2),), rediscovered=("a", "b"),
                       kept=0, never=0, stated=True)
    fresh = P.Carrier(name="DEVELOPER_DIR", values=(("/clt", 2),), rediscovered=("a", "b"),
                      kept=0, never=0, stated=False)
    text = P.proposed_section(_candidate(tmp_path, carriers=(stated, fresh)), today="2026-09-19")
    assert "DEVELOPER_DIR" in text
    assert "PATH=" not in text


# --- accept and drop -------------------------------------------------------


def test_accept_appends_and_keeps_every_byte_the_maintainer_wrote(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "app", "# app\n\nMine.\n")
    candidate = _candidate(tmp_path, repo_root=str(repo))
    result = P.accept(tmp_path / "vault", candidate, today="2026-09-19")
    assert result.ok
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# app\n\nMine.\n")
    assert "## Run `cargo test` with `SDKROOT`" in text


def test_accept_writes_a_file_the_repo_does_not_have_yet(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "app")
    assert P.accept(tmp_path / "vault", _candidate(tmp_path, repo_root=str(repo))).ok
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Working in this repo")
    assert "SDKROOT" in text


def test_accept_refuses_a_line_the_file_already_carries(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "app", "# app\n\nSDKROOT=/sdk cargo test\n")
    carrier = P.Carrier(name="SDKROOT", values=(("/sdk", 1),), rediscovered=("a", "b"),
                        kept=0, never=0, stated=True)
    result = P.accept(tmp_path / "vault", _candidate(tmp_path, repo_root=str(repo),
                                                     carriers=(carrier,)))
    assert not result.ok
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").count("SDKROOT") == 1


def test_accept_without_a_checkout_writes_nothing(tmp_path: Path) -> None:
    result = P.accept(tmp_path / "vault", _candidate(tmp_path, repo_root=str(tmp_path / "gone")))
    assert not result.ok


def test_a_dropped_candidate_does_not_come_back(tmp_path: Path) -> None:
    projects, repo = tmp_path / "projects", _repo(tmp_path, "app")
    vault = tmp_path / "vault"
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])
    candidate, = P.scan(str(projects))
    assert P.drop(vault, candidate).ok
    assert P.scan(str(projects), ledger_rows=P.ledger_rows(vault)) == []


def test_an_accepted_candidate_is_recorded_as_well(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    P.accept(vault, _candidate(tmp_path, repo_root=str(_repo(tmp_path, "app"))))
    rows = P.ledger_rows(vault)
    assert [(r["event"], r["repo"], r["key"]) for r in rows] == [
        (P.ACCEPTED, "app", "cargo-test")]
