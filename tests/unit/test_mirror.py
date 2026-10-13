# tests/unit/test_mirror.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core import mirror


def _make_claude_project(home: Path, encoded_name: str, files: dict[str, str]) -> Path:
    project_dir = home / ".claude" / "projects" / encoded_name / "memory"
    project_dir.mkdir(parents=True)
    for rel, content in files.items():
        p = project_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return project_dir


def test_extracts_agent_name_from_encoded_dir():
    assert mirror._agent_from_project_dir("-home-user-github-repo-d") == "repo-d"
    assert mirror._agent_from_project_dir("-Users-foo-Code-app") == "app"
    assert mirror._agent_from_project_dir("-") == "root"
    assert mirror._agent_from_project_dir("") == "root"


def test_mirror_copies_memory_files(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {
        "feedback.md": "# feedback content",
        "user_role.md": "# user role",
    })
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    target_dir = tmp_vault / "bots" / "myrepo" / "memory"
    assert (target_dir / "feedback.md").read_text(encoding="utf-8") == "# feedback content"
    assert (target_dir / "user_role.md").read_text(encoding="utf-8") == "# user role"


def test_mirror_never_deletes_user_notes(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "from claude"})
    cfg = {"vaultRoot": str(tmp_vault)}
    target_dir = tmp_vault / "bots" / "myrepo" / "memory"
    target_dir.mkdir(parents=True)
    (target_dir / "user_note.md").write_text("user wrote this", encoding="utf-8")
    mirror.mirror_all(cfg)
    assert (target_dir / "user_note.md").exists()
    assert (target_dir / "a.md").exists()


def test_mirror_skips_when_no_claude_projects(tmp_home: Path, tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)  # must not raise
    assert (tmp_vault / "bots").exists()


def test_mirror_copies_nested_directories(tmp_home: Path, tmp_vault: Path):
    """Once the rsync fallback's test (spec § 10.3); since #649 the Python
    walk is the only copy path."""
    _make_claude_project(tmp_home, "-home-x-myrepo", {
        "deep/nested/file.md": "deep content",
        "top.md": "top content",
    })
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    target = tmp_vault / "bots" / "myrepo" / "memory"
    assert (target / "top.md").read_text(encoding="utf-8") == "top content"
    assert (target / "deep" / "nested" / "file.md").read_text(encoding="utf-8") == "deep content"


def test_mirror_lock_prevents_concurrent_runs(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "x"})
    cfg = {"vaultRoot": str(tmp_vault)}
    # Hold the lock manually
    lock = tmp_vault / ".mirror.lock"
    lock.mkdir()
    os.utime(lock, None)  # fresh
    try:
        mirror.mirror_all(cfg)
    finally:
        lock.rmdir()
    # Second mirror noop'd, so target memory dir should not exist.
    assert not (tmp_vault / "bots" / "myrepo" / "memory" / "a.md").exists()


def test_decode_resolves_to_real_git_root(tmp_path: Path):
    """When the encoded path corresponds to a real filesystem path with .git, use git-root basename.

    Regression: previously, the encoded path was parsed via a brittle 'skip first 3 components'
    heuristic that produced 'refactor-repo-d' for /home/you/github/refactor/repo-d
    when the real git-root basename is 'repo-d'.
    """
    repo = tmp_path / "github" / "refactor" / "repo-d"
    (repo / ".git").mkdir(parents=True)
    encoded = "-" + str(repo).lstrip("/").replace("/", "-")
    assert mirror._agent_from_project_dir(encoded) == "repo-d"


def test_decode_handles_repo_with_internal_dashes(tmp_path: Path):
    """Repo names containing dashes should be recovered intact via filesystem decoding."""
    repo = tmp_path / "code" / "my-cool-project"
    (repo / ".git").mkdir(parents=True)
    encoded = "-" + str(repo).lstrip("/").replace("/", "-")
    assert mirror._agent_from_project_dir(encoded) == "my-cool-project"


def test_decode_falls_back_to_heuristic_when_path_missing():
    """When the encoded path no longer exists on disk, fall back to the existing heuristic."""
    # /no/such/path/here-bogus does not exist on this machine
    encoded = "-no-such-path-here-bogus"
    result = mirror._agent_from_project_dir(encoded)
    # Either the heuristic produces "here-bogus" (skip 3 → ['here','bogus']) or "bogus" (last segment).
    # Both are acceptable as long as it does not raise.
    assert result and isinstance(result, str)


def test_decode_walks_up_to_git_root_when_subdir_passed(tmp_path: Path):
    """If the encoded path points inside a subdir of a git repo, walk up to the git root."""
    repo = tmp_path / "work" / "myrepo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "deep" / "module"
    sub.mkdir(parents=True)
    encoded = "-" + str(sub).lstrip("/").replace("/", "-")
    assert mirror._agent_from_project_dir(encoded) == "myrepo"


def test_mirror_skips_an_empty_memory_dir(tmp_home: Path, tmp_vault: Path):
    """#420: Claude Code makes `memory/` for every cwd a session ran in, and
    leaves it empty. An empty source must not become a `bots/` directory."""
    empty = _make_claude_project(tmp_home, "-private-var-folders-T-pytest-of-x-pytest-881-live", {})
    (empty / "sub").mkdir()  # a directory tree with no file is still empty
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "kept"})

    mirror.mirror_all({"vaultRoot": str(tmp_vault)})

    assert sorted(p.name for p in (tmp_vault / "bots").iterdir()) == ["myrepo"]


# --- #649: what the mirror writes has been redacted --------------------------

import shutil  # noqa: E402

from mnemo.core.redact import find, redact_secrets  # noqa: E402

_PASSWORD = "Tr0ub4dor&3"
_HEX_ID = "0123456789abcdef0123456789abcdef"
_SECRETS = (
    "# staging\n"
    f"Log in as `qa@acme-corp.io` / `{_PASSWORD}`.\n"
    f"Password: {_PASSWORD}\n"
    f"CF_ACCOUNT_KEY=cf-{_PASSWORD}\n"
    f"account id {_HEX_ID}\n"
)
_CLEAN = "# clean\r\nUse yarn, not npm.  \r\n\n\ttabs\tand trailing spaces   \n"


def _memory_scan(vault: Path) -> list:
    """Every secret shape under ``bots/*/memory``, as (file, kind)."""
    hits = []
    for p in sorted((vault / "bots").glob("*/memory/**/*")):
        if p.is_file():
            for f in find(p.read_bytes().decode("utf-8", errors="replace")):
                hits.append((p.relative_to(vault).as_posix(), f.kind))
    return hits


def _stamp(p: Path) -> tuple:
    st = p.stat()
    return (p.read_bytes(), st.st_mtime_ns)


def test_the_copy_carries_no_shape_the_redactor_knows(tmp_home: Path, tmp_vault: Path):
    """Item 1: the copy is exactly ``redact_secrets`` of the source — the
    secrets-only pass, so the login address a rule needs survives (#418)."""
    _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _SECRETS, "deep/notes.md": _SECRETS})
    mirror.mirror_all({"vaultRoot": str(tmp_vault)})
    for rel in ("staging.md", "deep/notes.md"):
        copy = (tmp_vault / "bots" / "myrepo" / "memory" / rel).read_text(encoding="utf-8")
        assert copy == redact_secrets(_SECRETS)[0]
        assert _PASSWORD not in copy and _HEX_ID not in copy
        assert "qa@acme-corp.io" in copy and "Password: [redacted]" in copy
    assert _memory_scan(tmp_vault) == []


def test_claude_codes_own_file_is_only_read(tmp_home: Path, tmp_vault: Path):
    """Item 2: a run that redacted the copy left the source's bytes and mtime alone."""
    src_dir = _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _SECRETS})
    src = src_dir / "staging.md"
    os.utime(src, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = _stamp(src)
    mirror.mirror_all({"vaultRoot": str(tmp_vault)})
    mirror.mirror_all({"vaultRoot": str(tmp_vault)})
    assert _stamp(src) == before
    assert _PASSWORD in src.read_text(encoding="utf-8")
    assert _PASSWORD not in (tmp_vault / "bots" / "myrepo" / "memory" / "staging.md").read_text(encoding="utf-8")


def test_a_file_with_nothing_to_redact_is_copied_byte_for_byte(tmp_home: Path, tmp_vault: Path):
    """Item 3: CRLF, trailing whitespace and a byte that is not UTF-8 all
    survive, so the extraction state's hash of the copy does not move."""
    from mnemo.core.extract.inbox.io import content_hash

    src_dir = _make_claude_project(tmp_home, "-home-x-myrepo", {})
    raw = _CLEAN.encode("utf-8") + b"\xff latin byte\n"
    (src_dir / "clean.md").write_bytes(raw)
    mirror.mirror_all({"vaultRoot": str(tmp_vault)})
    copy = tmp_vault / "bots" / "myrepo" / "memory" / "clean.md"
    assert copy.read_bytes() == raw
    assert content_hash(copy) == content_hash(raw)
    assert copy.stat().st_mtime_ns == (src_dir / "clean.md").stat().st_mtime_ns


def test_one_run_brings_an_unredacted_vault_in_line(tmp_home: Path, tmp_vault: Path):
    """Item 4: copies rsync made before this change are byte-identical to
    their source, same size and mtime. One run leaves nothing under
    ``bots/*/memory`` for a scan to find."""
    src_dir = _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _SECRETS, "clean.md": _CLEAN})
    target = tmp_vault / "bots" / "myrepo" / "memory"
    target.mkdir(parents=True)
    for name in ("staging.md", "clean.md"):
        shutil.copy2(src_dir / name, target / name)  # what `rsync -a` left
    assert _memory_scan(tmp_vault) != []
    mirror.mirror_all({"vaultRoot": str(tmp_vault)})
    assert _memory_scan(tmp_vault) == []
    # Against the source's own bytes, not the fixture string: the helper wrote
    # it with `write_text`, which turns "\n" into "\r\n" on Windows.
    assert (target / "clean.md").read_bytes() == (src_dir / "clean.md").read_bytes()


def test_a_second_run_over_unchanged_sources_rewrites_nothing(
    tmp_home: Path, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Item 5: a copy that differs from its source only by redaction is not
    out of date. Compared by mtime, as the issue asks, and by counting the
    writes the second run makes, since the copy keeps its source's mtime."""
    _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _SECRETS, "clean.md": _CLEAN})
    cfg = {"vaultRoot": str(tmp_vault)}
    target = tmp_vault / "bots" / "myrepo" / "memory"
    first = mirror.mirror_all(cfg)
    assert first.written == 2 and first.redacted == 1
    stamps = {p.name: _stamp(p) for p in target.iterdir()}

    writes: list = []
    real_write = mirror._write
    monkeypatch.setattr(mirror, "_write", lambda dst, data: writes.append(dst) or real_write(dst, data))
    second = mirror.mirror_all(cfg)
    assert writes == []
    assert second.written == 0 and second.seen == 2
    assert {p.name: _stamp(p) for p in target.iterdir()} == stamps


def test_a_redacted_copy_from_an_earlier_run_is_not_out_of_date(
    tmp_home: Path, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Item 5, the case the quick check must get right: the copy is smaller
    than its source and carries its source's mtime. It is left alone."""
    src_dir = _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _SECRETS})
    target = tmp_vault / "bots" / "myrepo" / "memory"
    target.mkdir(parents=True)
    copy = target / "staging.md"
    copy.write_text(redact_secrets(_SECRETS)[0], encoding="utf-8")
    shutil.copystat(src_dir / "staging.md", copy)
    before = _stamp(copy)

    writes: list = []
    monkeypatch.setattr(mirror, "_write", lambda dst, data: writes.append(dst), raising=False)
    mirror.mirror_all({"vaultRoot": str(tmp_vault)})
    assert writes == []
    assert _stamp(copy) == before
    assert _PASSWORD not in copy.read_text(encoding="utf-8")


def test_a_changed_source_is_copied_again_redacted(tmp_home: Path, tmp_vault: Path):
    src_dir = _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _CLEAN})
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    src = src_dir / "staging.md"
    src.write_text(_SECRETS + "\nmore\n", encoding="utf-8")
    os.utime(src, ns=(src.stat().st_mtime_ns + 5_000_000_000,) * 2)
    report = mirror.mirror_all(cfg)
    assert report.written == 1
    copy = (tmp_vault / "bots" / "myrepo" / "memory" / "staging.md").read_text(encoding="utf-8")
    assert copy == redact_secrets(_SECRETS + "\nmore\n")[0]
    assert _memory_scan(tmp_vault) == []


def test_a_new_redactor_rechecks_every_copy_once(
    tmp_home: Path, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The quick check trusts a copy with its source's size and mtime because
    this mirror never writes one that carries a secret. That trust is keyed
    on the redactor: when its rules change, the next run reads every source
    again, and a copy an older list let through is rewritten."""
    src_dir = _make_claude_project(tmp_home, "-home-x-myrepo", {"staging.md": _SECRETS})
    cfg = {"vaultRoot": str(tmp_vault)}
    target = tmp_vault / "bots" / "myrepo" / "memory"
    target.mkdir(parents=True)
    shutil.copy2(src_dir / "staging.md", target / "staging.md")

    monkeypatch.setattr(mirror, "_redactor_fingerprint", lambda: "old-rules")
    # An older mirror ran under "old-rules" and the copy passed its list.
    mirror._mark_checked(tmp_vault)
    assert mirror.mirror_all(cfg).written == 0  # same size and mtime: trusted
    assert _memory_scan(tmp_vault) != []

    monkeypatch.setattr(mirror, "_redactor_fingerprint", lambda: "new-rules")
    assert mirror.mirror_all(cfg).written == 1
    assert _memory_scan(tmp_vault) == []
    assert mirror.mirror_all(cfg).written == 0
