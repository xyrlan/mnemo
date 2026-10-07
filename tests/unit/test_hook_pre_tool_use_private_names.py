"""PreToolUse denies a Bash command that would publish a private repo's name (#597).

The names live in ``<vault>/.mnemo/private-names.tsv`` (``name<TAB>alias``).
Every test builds its own vault and, for ``git push``, a real local repo with
a bare "remote" under ``tmp_path`` — nothing touches the network, the real
vault or the real ``~/.claude``.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

NAME = "Zorblax-Engine"
ALIAS = "repo-q"


def _cfg(vault: Path) -> dict:
    # Rule enforcement and enrichment off: the gate stands on the file alone.
    return {
        "vaultRoot": str(vault),
        "enforcement": {"enabled": False},
        "enrichment": {"enabled": False},
    }


def _write_tsv(vault: Path, text: str = f"{NAME}\t{ALIAS}\n") -> Path:
    path = vault / ".mnemo" / "private-names.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(monkeypatch, vault: Path, command: str, cwd: Path) -> str:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: _cfg(vault))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    from mnemo.hooks.pre_tool_use import main

    assert main() == 0
    return buf.getvalue()


def _deny_reason(out: str) -> str:
    assert out, "expected a deny envelope"
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    return hook["permissionDecisionReason"]


def _assert_names_alias_only(reason: str) -> None:
    assert ALIAS in reason
    assert NAME.lower() not in reason.lower()


def _git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone with one commit already on its bare remote."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "init", str(work))
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "initial")
    _git(work, "push", "-u", "origin", "main")
    return work


def _commit(work: Path, line: str, message: str = "add notes") -> None:
    with (work / "notes.md").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    _git(work, "add", "notes.md")
    _git(work, "commit", "-m", message)


# -- git push ----------------------------------------------------------------


def test_push_whose_commit_adds_a_name_is_denied(tmp_vault, repo, monkeypatch):
    _write_tsv(tmp_vault)
    _commit(repo, f"ported from {NAME.lower()} last week")

    reason = _deny_reason(_run(monkeypatch, tmp_vault, "git push", repo))

    _assert_names_alias_only(reason)


def test_push_whose_commit_message_names_it_is_denied(tmp_vault, repo, monkeypatch):
    _write_tsv(tmp_vault)
    _commit(repo, "harmless", message=f"fix: same bug as {NAME}")

    reason = _deny_reason(_run(monkeypatch, tmp_vault, "git push origin HEAD", repo))

    _assert_names_alias_only(reason)


def test_push_with_only_the_alias_is_allowed(tmp_vault, repo, monkeypatch):
    _write_tsv(tmp_vault)
    _commit(repo, f"ported from {ALIAS} last week")

    assert _run(monkeypatch, tmp_vault, "git push", repo) == ""


def test_name_already_on_the_remote_does_not_block_a_later_push(tmp_vault, repo, monkeypatch):
    """Only the commits being pushed count, not history the remote has."""
    _commit(repo, f"old mention of {NAME}")
    _git(repo, "push")
    _write_tsv(tmp_vault)
    _commit(repo, "a clean line")

    assert _run(monkeypatch, tmp_vault, "git push", repo) == ""


def test_push_to_the_private_repo_itself_is_allowed(tmp_vault, tmp_path, monkeypatch):
    """Naming a private repo inside that repo publishes nothing."""
    remote = tmp_path / f"{NAME}.git"
    _git(tmp_path, "init", "--bare", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "init", str(work))
    _git(work, "remote", "add", "origin", str(remote))
    _commit(work, f"the {NAME} readme")
    _write_tsv(tmp_vault)

    assert _run(monkeypatch, tmp_vault, "git push -u origin main", work) == ""


def test_git_dash_c_and_cd_resolve_the_repo(tmp_vault, repo, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    _commit(repo, NAME)

    out1 = _run(monkeypatch, tmp_vault, f"git -C {repo} push", tmp_path)
    out2 = _run(monkeypatch, tmp_vault, f"cd {repo} && git status && git push", tmp_path)

    _assert_names_alias_only(_deny_reason(out1))
    _assert_names_alias_only(_deny_reason(out2))


# -- gh ------------------------------------------------------------------------


def test_gh_issue_create_with_a_name_in_the_inline_body_is_denied(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    cmd = f'gh issue create --title "x" --body "seen in {NAME} too"'

    reason = _deny_reason(_run(monkeypatch, tmp_vault, cmd, tmp_path))

    _assert_names_alias_only(reason)


@pytest.mark.parametrize("flag", ["--body-file", "-F"])
def test_gh_issue_create_with_a_name_in_the_body_file_is_denied(tmp_vault, tmp_path, monkeypatch, flag):
    _write_tsv(tmp_vault)
    (tmp_path / "body.md").write_text(f"## Why\n\nlike {NAME.upper()}\n", encoding="utf-8")
    cmd = f"gh issue create --title x {flag} body.md"

    reason = _deny_reason(_run(monkeypatch, tmp_vault, cmd, tmp_path))

    _assert_names_alias_only(reason)


def test_gh_pr_comment_heredoc_body_is_denied(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    cmd = f"gh pr comment 12 --body-file - <<'EOF'\nit's {NAME}\nEOF"

    _assert_names_alias_only(_deny_reason(_run(monkeypatch, tmp_vault, cmd, tmp_path)))


def test_gh_api_write_with_a_field_file_is_denied(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    (tmp_path / "c.md").write_text(f"see {NAME}\n", encoding="utf-8")
    cmd = "gh api repos/o/pub/issues/1/comments -F body=@c.md"

    _assert_names_alias_only(_deny_reason(_run(monkeypatch, tmp_vault, cmd, tmp_path)))


def test_gh_with_only_the_alias_is_allowed(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    (tmp_path / "body.md").write_text(f"like {ALIAS}\n", encoding="utf-8")
    cmd = f'gh pr create --title "{ALIAS}" --body-file body.md'

    assert _run(monkeypatch, tmp_vault, cmd, tmp_path) == ""


def test_gh_targeting_the_private_repo_is_allowed(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    cmd = f'gh issue comment 3 --repo me/{NAME} --body "{NAME} bug"'

    assert _run(monkeypatch, tmp_vault, cmd, tmp_path) == ""


def test_gh_read_is_untouched(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault)
    cmd = f'gh issue list --search "{NAME}"'

    assert _run(monkeypatch, tmp_vault, cmd, tmp_path) == ""


# -- cost and failure ------------------------------------------------------------


def _spy_reads(monkeypatch, tsv: Path) -> list:
    reads = []
    real_read_bytes = Path.read_bytes

    def spy(self):
        data = real_read_bytes(self)  # a missing file raises: nothing read
        if self == tsv:
            reads.append(self)
        return data

    monkeypatch.setattr(Path, "read_bytes", spy)
    return reads


def _spy_subprocess(monkeypatch) -> list:
    calls = []
    real = subprocess.run

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def test_unrelated_bash_reads_nothing(tmp_vault, repo, monkeypatch):
    tsv = _write_tsv(tmp_vault)
    reads = _spy_reads(monkeypatch, tsv)
    calls = _spy_subprocess(monkeypatch)

    out = _run(monkeypatch, tmp_vault, f"echo {NAME} && ls -la && git status", repo)

    assert out == ""
    assert reads == [] and calls == []


def test_no_tsv_allows_with_zero_reads(tmp_vault, repo, monkeypatch):
    _commit(repo, NAME)
    tsv = tmp_vault / ".mnemo" / "private-names.tsv"
    reads = _spy_reads(monkeypatch, tsv)
    calls = _spy_subprocess(monkeypatch)

    out = _run(monkeypatch, tmp_vault, "git push", repo)

    assert out == ""
    assert reads == [] and calls == [], "no list, so no git log either"


def test_malformed_tsv_fails_open_and_logs(tmp_vault, repo, monkeypatch):
    _write_tsv(tmp_vault, f"{NAME}\t{ALIAS}\nno tab on this line\n")
    _commit(repo, NAME)

    out = _run(monkeypatch, tmp_vault, "git push", repo)

    assert out == ""
    log = (tmp_vault / ".errors.log").read_text(encoding="utf-8")
    assert "private_names" in log
    assert NAME.lower() not in log.lower()


def test_comments_and_blank_lines_are_not_malformed(tmp_vault, tmp_path, monkeypatch):
    _write_tsv(tmp_vault, f"# name<TAB>alias\n\n{NAME}\t{ALIAS}\n")
    cmd = f'gh issue create --body "{NAME}"'

    _assert_names_alias_only(_deny_reason(_run(monkeypatch, tmp_vault, cmd, tmp_path)))
