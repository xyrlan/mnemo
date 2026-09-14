"""Reading Claude Code background-session state (the live session queue).

``state.json`` is written by Claude Code, never by mnemo. Every field is
optional from our side: a schema change upstream must degrade the render,
never raise. The one field that carries the whole feature is ``tempo`` —
see :mod:`mnemo.core.sessions.jobs`.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.sessions import jobs


def _job(root: Path, short_id: str, **fields) -> Path:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")
    return d


def test_returns_empty_when_jobs_dir_missing(tmp_path: Path) -> None:
    assert jobs.read_sessions(tmp_path / "nope") == []


def test_reads_a_blocked_session(tmp_path: Path) -> None:
    _job(
        tmp_path, "a3f1",
        state="working", tempo="blocked",
        needs="answer: bcrypt ou argon2?",
        detail="reading auth.py",
        name="auth-refactor",
        cwd="/repo",
        tokens=1200,
    )

    (s,) = jobs.read_sessions(tmp_path)

    assert s.short_id == "a3f1"
    assert s.is_blocked is True
    assert s.needs == "answer: bcrypt ou argon2?"
    assert s.name == "auth-refactor"
    assert s.tokens == 1200


def test_blocked_is_driven_by_tempo_not_state(tmp_path: Path) -> None:
    # The regression guard for the whole feature. A session waiting on a human
    # sits at state=working, tempo=blocked. Reading ``state`` files it under
    # "working" and the queue stops being a queue.
    _job(tmp_path, "a3f1", state="working", tempo="blocked", needs="q?")

    (s,) = jobs.read_sessions(tmp_path)

    assert s.is_blocked is True
    assert s.is_done is False


def test_done_is_driven_by_state(tmp_path: Path) -> None:
    _job(tmp_path, "e51f", state="done", tempo="idle", name="shipped")

    (s,) = jobs.read_sessions(tmp_path)

    assert s.is_done is True
    assert s.is_blocked is False


def test_missing_fields_degrade_and_never_raise(tmp_path: Path) -> None:
    _job(tmp_path, "bare")  # every field absent

    (s,) = jobs.read_sessions(tmp_path)

    assert s.short_id == "bare"
    assert s.needs is None
    assert s.name is None
    assert s.tokens is None
    assert s.is_blocked is False


def test_malformed_json_skips_that_session_only(tmp_path: Path) -> None:
    _job(tmp_path, "good", state="working", tempo="blocked", needs="q?")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")

    ids = [s.short_id for s in jobs.read_sessions(tmp_path)]

    assert ids == ["good"]


def test_pins_json_and_loose_files_are_ignored(tmp_path: Path) -> None:
    _job(tmp_path, "good", state="working", tempo="active")
    (tmp_path / "pins.json").write_text("{}", encoding="utf-8")

    assert [s.short_id for s in jobs.read_sessions(tmp_path)] == ["good"]


def test_filters_by_cwd_when_asked(tmp_path: Path) -> None:
    _job(tmp_path, "here", state="working", tempo="active", cwd="/repo/a")
    _job(tmp_path, "elsewhere", state="working", tempo="active", cwd="/repo/b")

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd="/repo/a")]

    assert ids == ["here"]


def test_suggested_reply_is_optional(tmp_path: Path) -> None:
    _job(tmp_path, "with", tempo="blocked", needs="q?", suggestedReply="sim")
    _job(tmp_path, "without", tempo="blocked", needs="q?")

    by_id = {s.short_id: s for s in jobs.read_sessions(tmp_path)}

    assert by_id["with"].suggested_reply == "sim"
    assert by_id["without"].suggested_reply is None


def test_cwd_match_ignores_a_trailing_slash(tmp_path: Path) -> None:
    # The user is in /repo/a; the query arrives with a trailing slash. Raw
    # string equality reports an empty queue and the user concludes nothing
    # is running.
    _job(tmp_path, "here", state="working", tempo="active", cwd=str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(tmp_path / "repo") + "/")]

    assert ids == ["here"]


def test_cwd_match_survives_a_symlinked_path(tmp_path: Path) -> None:
    # A git worktree — or /tmp on macOS — reaches the same directory through a
    # symlink. The stored cwd and the live one are then different strings for
    # one directory, and the queue silently empties.
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "linked-repo"
    link.symlink_to(real)

    _job(tmp_path, "here", state="working", tempo="active", cwd=str(real))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(link))]

    assert ids == ["here"]


def test_stored_symlinked_cwd_matches_a_canonical_query(tmp_path: Path) -> None:
    # The mirror image: Claude Code recorded the symlinked form and the live
    # process reports the resolved one. Normalizing only the query still misses.
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "linked-repo"
    link.symlink_to(real)

    _job(tmp_path, "here", state="working", tempo="active", cwd=str(link))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(real))]

    assert ids == ["here"]


def test_session_without_a_cwd_is_filtered_out_and_never_raises(tmp_path: Path) -> None:
    # realpath(None) raises; a session with no recorded cwd must simply not
    # match, not take the whole command down.
    _job(tmp_path, "nocwd", state="working", tempo="active")
    _job(tmp_path, "here", state="working", tempo="active", cwd=str(tmp_path))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(tmp_path))]

    assert ids == ["here"]


def test_label_shows_an_issue_number() -> None:
    s = jobs.Session(short_id="ab12", cwd="/x/mnemo-wt-193", name="fix the thing")
    assert s.label.startswith("#193 ")


def test_label_shows_a_piece_slug_without_a_hash() -> None:
    # ``#c-parser`` reads as an issue that does not exist; the slug alone
    # does not.
    s = jobs.Session(short_id="ab12", cwd="/x/mnemo-wt-c-parser", name="parse it")
    assert s.label.startswith("c-parser ")
    assert not s.label.startswith("#")


def test_label_of_an_undispatched_session_has_no_prefix() -> None:
    # An ordinary checkout is not a dispatch child and gets no marker.
    s = jobs.Session(short_id="ab12", cwd="/x/mnemo", name="just working")
    assert s.label == "just working"


def test_unlistable_jobs_dir_yields_nothing(tmp_path: Path, monkeypatch) -> None:
    # The directory passes is_dir() and then becomes unlistable — a permission
    # change, or it vanished. The module's contract is that an upstream change
    # degrades the render; raising OSError straight through `mnemo sessions`
    # is the one thing it must not do. A missing directory already yields [];
    # an unreadable one must too.
    _job(tmp_path, "here", state="working", tempo="blocked")

    real_iterdir = Path.iterdir

    def _boom(self):
        if self == tmp_path:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _boom)

    assert jobs.read_sessions(tmp_path) == []


# --- dispatch worktrees belong to the repo's queue (#281) ------------------


def test_a_dispatch_worktree_matches_its_repo(tmp_path: Path) -> None:
    # The bug that closed #281: the maintainer types `mnemo sessions` in
    # ~/github/clubinho and every child lives in ~/github/clubinho-wt-<issue>.
    # Exact-equality scoping answers "nothing is running" while a child sits
    # blocked asking for a prod query.
    repo = tmp_path / "clubinho"
    repo.mkdir()
    _job(tmp_path, "child", state="blocked", tempo="blocked",
         cwd=str(tmp_path / "clubinho-wt-213"))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(repo))]

    assert ids == ["child"]


def test_a_contract_piece_worktree_matches_its_repo(tmp_path: Path) -> None:
    repo = tmp_path / "mnemo"
    repo.mkdir()
    _job(tmp_path, "piece", state="working", tempo="active",
         cwd=str(tmp_path / "mnemo-wt-c-parser"))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(repo))]

    assert ids == ["piece"]


def test_a_neighbour_repo_is_not_swept_in(tmp_path: Path) -> None:
    # Prefix matching would file `clubinho-old` under `clubinho`. Only the two
    # shapes dispatch itself writes are admitted.
    repo = tmp_path / "clubinho"
    repo.mkdir()
    _job(tmp_path, "neighbour", state="working", tempo="active",
         cwd=str(tmp_path / "clubinho-old"))
    _job(tmp_path, "handmade", state="working", tempo="active",
         cwd=str(tmp_path / "clubinho-wt-feature"))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(repo))]

    assert ids == []


def test_the_worktree_itself_still_sees_its_own_session(tmp_path: Path) -> None:
    # A child running `mnemo sessions` inside its own worktree must still find
    # itself: the repo rule adds reach, it never removes the exact match.
    wt = tmp_path / "clubinho-wt-213"
    wt.mkdir()
    _job(tmp_path, "child", state="working", tempo="active", cwd=str(wt))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(wt))]

    assert ids == ["child"]


def test_sibling_worktrees_of_the_same_repo_see_each_other(tmp_path: Path) -> None:
    # Scoping from inside one child should show its siblings: they are all one
    # repo's queue, which is the unit the maintainer is tracking.
    wt = tmp_path / "clubinho-wt-212"
    wt.mkdir()
    _job(tmp_path, "sibling", state="blocked", tempo="blocked",
         cwd=str(tmp_path / "clubinho-wt-213"))

    ids = [s.short_id for s in jobs.read_sessions(tmp_path, cwd=str(wt))]

    assert ids == ["sibling"]
