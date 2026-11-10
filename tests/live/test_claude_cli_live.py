"""The ``claude`` CLI contract, checked against the **installed** binary (#235).

Opt-in — ``pytest -m live_claude`` — because it spawns one real background
session (a one-word prompt, ~30 tokens, ~6s), then stops and removes it. The
default run deselects it via ``addopts``.

Every assertion here corresponds to an entry in
:data:`mnemo.core.claude_cli.ASSUMPTIONS`; the failure message names it. When
one fails, the procedure is: read what the binary actually did, update the
assumption (and the code that implements it), bump ``VERIFIED_AGAINST``.
Fixtures elsewhere in the suite mirror the last observed output; this is the
only test that can tell when "last observed" has gone stale.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import warnings
from pathlib import Path

import pytest

from mnemo.core import child_profile, claude_cli, dispatch
from mnemo.core.sessions import jobs, liveness, residents

pytestmark = [pytest.mark.live_claude, pytest.mark.real_spawn, pytest.mark.real_ps]

PROMPT = "Reply with exactly the word OK and nothing else. Do not use any tools."


def _git_repo(root: Path) -> Path:
    root.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q", "-b", "master"], ["add", "."], ["commit", "-qm", "init"]):
        if args[0] == "add":
            (root / "README.md").write_text("live\n", encoding="utf-8")
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)
    return root


def _until(predicate, *, seconds: float, every: float = 0.5):
    """Poll *predicate* until it returns something truthy, or time runs out."""
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            return value
        time.sleep(every)


def _state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@pytest.fixture()
def real_claude(real_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Undo the suite's HOME isolation for the child, and for the jobs reader.

    The autouse fixtures point HOME and ``jobs_dir`` at a throwaway tree so no
    unit test can touch the developer's machine. This test's whole purpose is
    to touch it: the child inherits the environment, and under a fresh HOME
    ``claude`` would hit onboarding and never spawn. Returns the real
    ``~/.claude``.
    """
    if shutil.which("claude") is None:
        pytest.skip("no `claude` on PATH")
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("USERPROFILE", str(real_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: real_home))
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    claude_home = real_home / ".claude"
    monkeypatch.setattr(jobs, "jobs_dir", lambda: claude_home / "jobs")
    return claude_home


def test_installed_version_is_readable(real_claude: Path) -> None:
    version = claude_cli.claude_version()
    assert version, "`claude --version` printed no x.y.z; parse_version needs updating"
    if version != claude_cli.VERIFIED_AGAINST:
        warnings.warn(
            f"claude {version} installed; assumptions last verified against "
            f"{claude_cli.VERIFIED_AGAINST}. If this run passes, bump "
            "VERIFIED_AGAINST / VERIFIED_ON in mnemo.core.claude_cli.",
            stacklevel=1,
        )


def test_agents_refuses_a_pipe(real_claude: Path) -> None:
    """``agents-needs-tty``: routed around, but the note must stay true."""
    result = subprocess.run(["claude", "agents"], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, (
        "`claude agents` now works on a pipe; update the `agents-needs-tty` "
        "assumption and the dispatch hints that route around it"
    )


def test_bg_spawn_read_back_and_teardown(real_claude: Path, tmp_path: Path) -> None:
    """One real spawn, every assumption dispatch meets on the way, in order."""
    repo = _git_repo(tmp_path / "live")
    tree = dispatch.ensure_worktree(1, repo_root=repo)
    short_id = ""
    try:
        # bg-positional-prompt + bg-prints-short-id: the real spawn_child, no
        # fixture in the path. ContractBroken here names what changed.
        short_id = dispatch.spawn_child(PROMPT, cwd=tree)
        assert claude_cli.SHORT_ID_RE.match(short_id), short_id

        # jobs-state-json: present synchronously, cwd is the tree.
        entry = real_claude / "jobs" / short_id
        state_path = entry / "state.json"
        problem = claude_cli.verify_registered(short_id, cwd=tree)
        assert problem is None, problem
        data = _state(state_path)
        assert data is not None
        for key in ("state", "tempo", "sessionId", "intent"):
            assert isinstance(data.get(key), str) and data[key], (
                f"jobs-state-json: `{key}` missing or not a string at spawn: {data.get(key)!r}"
            )
        assert data["sessionId"].startswith(short_id), (
            "jobs-state-json: sessionId no longer prefixed by the short id"
        )
        assert data["intent"] == PROMPT, "jobs-state-json: `intent` is no longer the prompt"

        # The reader every consumer goes through resolves the tree to exactly
        # this session — what `mnemo sessions` and `mnemo deliver` rely on.
        found = jobs.read_sessions(real_claude / "jobs", cwd=str(tree), claude_home=real_claude)
        assert [s.short_id for s in found] == [short_id], found

        # state-tempo-vocabulary, at spawn.
        session = found[0]
        assert session.state in {"working", "blocked", "done", "stopped"}, session.state
        assert session.tempo in {"active", "blocked", "idle"}, session.tempo

        # daemon-roster-pid: present synchronously, and the pid is alive.
        roster = liveness.read_roster(real_claude)
        assert roster is not None, "daemon-roster-pid: roster.json unreadable"
        assert short_id in roster, f"daemon-roster-pid: {short_id} not in workers"
        assert session.live is True, "daemon-roster-pid: read_sessions did not stamp live=True"

        # daemon-spare-pool: the spawn claimed the idle spare, the daemon
        # replaced it, and the census files the child as a worker, not a spare.
        worker_pid = roster[short_id]

        def _census():
            table = residents.read_ps()
            assert table is not None, "daemon-spare-pool: `ps` unreadable"
            states = {s.short_id: s.state for s in jobs.read_sessions(real_claude / "jobs")}
            return residents.census(residents.parse_ps(table),
                                    residents.read_roster_raw(real_claude), states)

        pool = _until(lambda: (lambda c: c if len(c.spares) == 1 else None)(_census()), seconds=15)
        assert pool, f"daemon-spare-pool: expected one idle spare after a spawn, got {_census().spares}"
        assert worker_pid in {w.pid for w in pool.workers}, (
            f"daemon-spare-pool: worker pid {worker_pid} not filed as a worker"
        )
        assert worker_pid not in {s.pid for s in pool.spares}, (
            "daemon-spare-pool: a claimed spare was counted as idle"
        )

        # Let the one-word child finish, then check the terminal vocabulary
        # and the transcript it leaves behind.
        finished = _until(
            lambda: (lambda d: d if d and d.get("state") in ("done", "stopped") else None)(
                _state(state_path)
            ),
            seconds=180,
        )
        assert finished, f"child never reached done/stopped: {_state(state_path)}"
        assert finished.get("tempo") == "idle", (
            f"state-tempo-vocabulary: finished child has tempo={finished.get('tempo')!r}"
        )

        # bg-model-flag, half one: `respawnFlags` is the argv `Session.model`
        # reads; the sibling `model` key is null on every real session.
        #
        # This child was spawned on the lean profile (#270, the default since
        # `spawn_child` grew one), which passes its own `--settings`. Claude
        # Code therefore has no user-level default to resolve, and writes no
        # `--model` at all — measured, both arms, on 2.1.270:
        #
        #   lean          -> ['--setting-sources', ..., '--settings', ...]
        #   --full-profile-> ['--model', 'opus[1m]']
        #
        # So the resolution half of the assumption is asserted below on a
        # full-profile child, which is the only arm that can exhibit it. Here
        # we pin what the lean arm must do: carry the profile it was given,
        # and never a half-written `--model` with no value after it.
        flags = finished.get("respawnFlags")
        assert isinstance(flags, list), (
            f"bg-model-flag: respawnFlags is not a list: {flags!r}"
        )
        assert "--settings" in flags, (
            f"lean-child-profile: no `--settings` in respawnFlags={flags!r}; "
            "the lean profile is what this child was spawned with"
        )
        assert "--model" not in flags, (
            f"bg-model-flag: a lean child resolved a default model into "
            f"respawnFlags={flags!r}; the lean profile passes its own "
            "`--settings`, so there is no user default left to resolve — if "
            "this now fires, `mnemo sessions` can read a model for lean "
            "children and the assumption's measured arms are stale"
        )

        # transcript-jsonl: linkScanPath is filled in after spawn.
        transcript = _until(
            lambda: (_state(state_path) or {}).get("linkScanPath"), seconds=60
        )
        assert isinstance(transcript, str) and os.path.isfile(transcript), (
            f"transcript-jsonl: linkScanPath={transcript!r}"
        )
        with open(transcript, encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
        assert lines, "transcript-jsonl: file is empty"
        events = [json.loads(line) for line in lines]
        assert all(isinstance(e, dict) and "type" in e for e in events), (
            "transcript-jsonl: a line is not an object with `type`"
        )
        assert any(e.get("type") == "assistant" and isinstance(e.get("message"), dict)
                   for e in events), "transcript-jsonl: no assistant line with `message`"

        # stop-rm-noninteractive: stop on a done session exits 0 and leaves
        # state/tempo alone.
        stop = subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
        assert stop.returncode == 0, f"stop-rm-noninteractive: stop rc={stop.returncode} {stop.stderr!r}"
        after = _state(state_path)
        assert after and after.get("state") in ("done", "stopped") and after.get("tempo") == "idle", after
        # daemon-spare-pool: stop on a done child ends its process — what the
        # doctor hint for finished-but-resident children tells a maintainer.
        assert _until(lambda: not liveness.pid_alive(worker_pid), seconds=30), (
            f"daemon-spare-pool: `claude stop` left worker pid {worker_pid} running"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            rm = subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)
            assert rm.returncode == 0, f"stop-rm-noninteractive: rm rc={rm.returncode} {rm.stderr!r}"
            assert not (real_claude / "jobs" / short_id).exists(), (
                "stop-rm-noninteractive: `claude rm` left the jobs entry behind"
            )
        dispatch.remove_worktree(tree, repo_root=repo, branch=dispatch.branch_name(1))


def test_bg_and_model_compose(real_claude: Path, tmp_path: Path) -> None:
    """``bg-model-flag``: ``--bg --model <id>`` is accepted and honoured (#268).

    A second real spawn, and it earns its ~6s: the whole of ``mnemo dispatch
    --model`` rests on these two flags composing, and no fixture can tell when
    they stop. Deliberately on the cheapest model available, both because the
    prompt is one word and because a test that spends the expensive default to
    prove a cheaper one works would be self-defeating.

    The transcript is the proof rather than ``respawnFlags``: the flags only
    say what was *asked for*, and the question this assumption answers is what
    the child actually ran on.
    """
    repo = _git_repo(tmp_path / "live-model")
    tree = dispatch.ensure_worktree(2, repo_root=repo)
    short_id = ""
    try:
        short_id = dispatch.spawn_child(PROMPT, cwd=tree, model="haiku")
        assert claude_cli.SHORT_ID_RE.match(short_id), (
            f"bg-model-flag: `--bg --model haiku` printed no id: {short_id!r}"
        )

        state_path = real_claude / "jobs" / short_id / "state.json"
        finished = _until(
            lambda: (lambda d: d if d and d.get("state") in ("done", "stopped") else None)(
                _state(state_path)
            ),
            seconds=180,
        )
        assert finished, f"child never finished: {_state(state_path)}"

        session = jobs._parse(short_id, finished)
        assert session.model == "haiku", (
            f"bg-model-flag: spawned with --model haiku, respawnFlags say "
            f"{finished.get('respawnFlags')!r}"
        )

        transcript = _until(lambda: finished.get("linkScanPath") or
                            (_state(state_path) or {}).get("linkScanPath"), seconds=60)
        assert isinstance(transcript, str) and os.path.isfile(transcript), transcript
        with open(transcript, encoding="utf-8") as fh:
            models = {
                json.loads(line).get("message", {}).get("model")
                for line in fh
                if line.strip() and json.loads(line).get("type") == "assistant"
            }
        models.discard(None)
        assert models, "bg-model-flag: no assistant record named a model"
        assert all("haiku" in m for m in models), (
            f"bg-model-flag: asked for haiku, the child ran on {sorted(models)} — "
            "`--model` is no longer honoured under `--bg`"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)
        dispatch.remove_worktree(tree, repo_root=repo, branch=dispatch.branch_name(2))


def test_effort_levels_match_the_cli(real_claude: Path) -> None:
    """``bg-effort-flag``, half one: ``EFFORT_LEVELS`` is the CLI's own list (#351).

    Free: ``--version`` short-circuits, so the unknown level is warned about
    and no model is called. The warning is the only place the list is printed.
    """
    result = subprocess.run(
        ["claude", "--effort", "mnemo-not-a-level", "--version"],
        capture_output=True, text=True, timeout=30,
    )
    printed = result.stdout + result.stderr
    assert "Valid values:" in printed, (
        f"bg-effort-flag: an unknown --effort no longer lists the valid ones: {printed!r}"
    )
    listed = printed.split("Valid values:", 1)[1].split("\n", 1)[0].strip().rstrip(".")
    assert tuple(v.strip() for v in listed.split(",")) == claude_cli.EFFORT_LEVELS, (
        f"bg-effort-flag: the CLI accepts {listed!r}; update EFFORT_LEVELS"
    )


def test_bg_and_effort_compose(real_claude: Path, tmp_path: Path) -> None:
    """``bg-effort-flag``, half two: ``--effort`` survives ``--bg`` (#351).

    On the lean default, where no other flag would put a value there, and on
    haiku at ``low`` so the proof is the cheapest spawn available. What is
    checked is what was *recorded*: nothing observable says how hard a child
    actually reasoned.
    """
    repo = _git_repo(tmp_path / "live-effort")
    tree = dispatch.ensure_worktree(3, repo_root=repo)
    short_id = ""
    try:
        short_id = dispatch.spawn_child(PROMPT, cwd=tree, model="haiku", effort="low")
        assert claude_cli.SHORT_ID_RE.match(short_id), (
            f"bg-effort-flag: `--bg --effort low` printed no id: {short_id!r}"
        )
        state_path = real_claude / "jobs" / short_id / "state.json"
        data = _until(lambda: _state(state_path), seconds=30)
        assert data, "bg-effort-flag: no state.json for the child"
        assert jobs._parse(short_id, data).effort == "low", (
            f"bg-effort-flag: spawned with --effort low, respawnFlags say "
            f"{data.get('respawnFlags')!r}"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)
        dispatch.remove_worktree(tree, repo_root=repo, branch=dispatch.branch_name(3))


def test_lean_child_profile(real_claude: Path, tmp_path: Path) -> None:
    """``lean-child-profile``: the flags drop the user profile, and mnemo survives.

    Two ``-p`` sessions rather than two ``--bg`` children, because this checks
    what the child can *see*, and ``--print`` answers that in one call without
    leaving a background session to reap. The token *saving* is not asserted:
    it depends on what the maintainer has installed, and a threshold here
    would fail on a clean machine for the right reason. What is asserted is
    the mechanism the saving rests on — the user's servers gone, mnemo's kept.

    Skipped rather than failed when mnemo is not installed in this HOME: the
    hand-back has nothing to hand back, which is a machine state, not a broken
    assumption.
    """
    key = "lean-child-profile"
    if not claude_cli.assumption(key):  # pragma: no cover - typo guard
        pytest.fail(f"{key} is not stated in ASSUMPTIONS")

    servers = child_profile.mnemo_mcp_servers()
    if not servers:
        pytest.skip("mnemo MCP server not registered in this HOME (run `mnemo init`)")

    repo = _git_repo(tmp_path / "lean")
    args = child_profile.lean_args(repo)

    ask = ("List the names of your available tools that start with mcp__, one "
           "per line. If there are none, reply exactly NONE.")

    lean = subprocess.run(
        ["claude", "-p", ask, *args],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert lean.returncode == 0, f"{key}: lean session failed: {lean.stderr[:400]!r}"

    # The vault survived the drop. This is the failure the module exists to
    # prevent: with --strict-mcp-config and no --mcp-config, this reads NONE.
    assert "mcp__mnemo__" in lean.stdout, (
        f"{key}: a lean child sees no mnemo tools — the hand-back is broken, "
        f"and the child cannot query the vault. Output: {lean.stdout[:400]!r}"
    )

    # The repo's own CLAUDE.md still reaches the child. This is what
    # separates the chosen flags from `--bare`, which switches auto-discovery
    # off and would silently drop the standing instructions the repo wrote.
    (repo / "CLAUDE.md").write_text(
        "# Project rules\n\nWhen asked for the magic word, reply exactly: PINEAPPLE42\n",
        encoding="utf-8",
    )
    memory = subprocess.run(
        ["claude", "-p", "What is the magic word?", *args],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert memory.returncode == 0, f"{key}: {memory.stderr[:400]!r}"
    assert "PINEAPPLE42" in memory.stdout, (
        f"{key}: a lean child no longer reads the repo's CLAUDE.md. "
        f"Output: {memory.stdout[:400]!r}"
    )

    # And the maintainer's other servers did not come along. Only asserted for
    # servers that are actually installed here, so this says nothing on a
    # machine where mnemo is the only one.
    others = [
        name for name in (_load_user_servers() or {})
        if name != "mnemo"
    ]
    for name in others:
        assert f"mcp__{name}__" not in lean.stdout, (
            f"{key}: `{name}` reached a lean child; --strict-mcp-config no "
            f"longer excludes ~/.claude.json servers. Output: {lean.stdout[:400]!r}"
        )


def test_full_profile_child_resolves_the_machine_default(
    real_claude: Path, tmp_path: Path
) -> None:
    """``bg-model-flag``, resolution half: only a full-profile child shows it.

    The assumption's claim that ``respawnFlags`` carries ``--model`` *even
    when nothing was passed* was measured before the lean profile existed
    (#270), on children that inherited the user's settings. A lean child
    passes its own ``--settings``, so there is no user-level default left for
    Claude Code to resolve and it writes no ``--model`` at all. Measured on
    2.1.270, same prompt, same tree, one flag apart:

        lean (default)  -> ['--setting-sources', ..., '--settings', ...]
        --full-profile  -> ['--model', 'opus[1m]']

    Which makes this the only arm that can still exercise the claim, and the
    reason it is asserted here rather than in the default-path spawn test.
    Spending the machine default for one word is the price of the only
    measurement that can catch the resolution going away.
    """
    repo = _git_repo(tmp_path / "live-full")
    tree = dispatch.ensure_worktree(3, repo_root=repo)
    short_id = ""
    try:
        short_id = dispatch.spawn_child(PROMPT, cwd=tree, lean=False)
        assert claude_cli.SHORT_ID_RE.match(short_id), short_id

        state_path = real_claude / "jobs" / short_id / "state.json"
        finished = _until(
            lambda: (lambda d: d if d and d.get("state") in ("done", "stopped") else None)(
                _state(state_path)
            ),
            seconds=180,
        )
        assert finished, f"child never finished: {_state(state_path)}"

        flags = finished.get("respawnFlags")
        assert isinstance(flags, list) and "--model" in flags, (
            f"bg-model-flag: a full-profile child passed no model and got "
            f"respawnFlags={flags!r}; Claude Code no longer resolves the "
            "machine default, so `mnemo sessions` shows no model for any "
            "child that did not name one"
        )
        assert jobs._parse(short_id, finished).model, (
            f"bg-model-flag: respawnFlags={flags!r} has `--model` with no value"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)
        dispatch.remove_worktree(tree, repo_root=repo, branch=dispatch.branch_name(3))


def test_a_bypass_child_needs_user_settings(real_claude: Path, tmp_path: Path) -> None:
    """``bg-bypass-needs-user-settings``: refused lean, started with user settings.

    Two halves, against the installed binary. The lean flags as dispatch passed
    them before #822 are refused before anything starts. The flags dispatch
    passes now start a child that runs ``bypassPermissions``. Both need the
    disclaimer accepted in user settings, which a machine that never ran bypass
    has not done, so that machine skips rather than failing for the wrong
    reason. A plain directory, not a fresh git repo: ``--bg`` does not trust a
    new repository ("Workspace not trusted"). One haiku child, one word.
    """
    from mnemo.core import child_mode

    key = "bg-bypass-needs-user-settings"
    bypass = child_profile.BYPASS_MODE
    if child_mode.bypass_accepted_in(real_claude) is None:
        pytest.skip("the bypass disclaimer was never accepted in user settings here")

    old = tmp_path / "old-shape"
    old.mkdir()
    refused = subprocess.run(
        ["claude", "--bg", "--permission-mode", bypass, *child_profile.LEAN_FLAGS, PROMPT],
        cwd=old, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    said = (refused.stderr or refused.stdout).strip()
    if refused.returncode == 0:
        started = claude_cli.short_id_from(refused.stdout)
        if started:
            subprocess.run(["claude", "stop", started], capture_output=True, timeout=60)
            subprocess.run(["claude", "rm", started], capture_output=True, timeout=60)
    assert refused.returncode != 0 and claude_cli.BYPASS_REFUSAL in said, (
        f"{key}: the pre-#822 lean shape in bypass was not refused as measured "
        f"(exit {refused.returncode}: {said[:300]!r}). If the CLI starts it again, "
        "the user settings a bypass child now loads may no longer be needed"
    )

    tree = tmp_path / "new-shape"
    tree.mkdir()
    short_id = ""
    try:
        short_id = dispatch.spawn_child(PROMPT, cwd=tree, model="haiku",
                                        permission_mode=bypass)
        state_path = real_claude / "jobs" / short_id / "state.json"
        finished = _until(
            lambda: (lambda d: d if d and d.get("state") in ("done", "stopped")
                     and d.get("linkScanPath") else None)(_state(state_path)),
            seconds=180,
        )
        assert finished, f"{key}: child never finished: {_state(state_path)}"
        flags = finished.get("respawnFlags") or []
        assert "user,project,local" in flags, f"{key}: respawnFlags={flags!r}"
        from mnemo.core.sessions import inbound_policy

        mode = inbound_policy.last_mode(Path(finished["linkScanPath"]))
        assert mode == bypass, (
            f"{key}: a lean child with user settings ran {mode!r}, not {bypass}; "
            "the acceptance is no longer read from user settings, or a bypass "
            "child is downgraded for another reason"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)


# --- bg-advisor-flag (#818) ----------------------------------------------------

#: Asks for the one advisor call the assertions read back. The session decides
#: when to consult it (Claude Code's docs: nothing forces or caps a call), so
#: the prompt asks outright, as the docs advise.
ADVISOR_PROMPT = (
    "Do not read, write or run anything. Call the advisor tool exactly once, "
    "then reply with exactly the word OK and nothing else."
)

#: For a child that should have no advisor. Conditional, because told
#: outright to call a tool it lacks, the 2026-11-09 child blocked and asked
#: ("no advisor tool in session; awaiting alternative tool choice") rather
#: than finishing.
ADVISOR_IF_ANY_PROMPT = (
    "Do not read, write or run anything. If you have a tool named advisor, "
    "call it exactly once. Either way, reply with exactly the word OK."
)


def _finished_transcript(real_claude: Path, short_id: str) -> tuple:
    """``(state.json, [records])`` once the child is done, or fail."""
    state_path = real_claude / "jobs" / short_id / "state.json"
    finished = _until(
        lambda: (lambda d: d if d and d.get("state") in ("done", "stopped") else None)(
            _state(state_path)
        ),
        seconds=300,
    )
    assert finished, f"child never finished: {_state(state_path)}"
    transcript = _until(lambda: (_state(state_path) or {}).get("linkScanPath"), seconds=60)
    assert isinstance(transcript, str) and os.path.isfile(transcript), transcript
    with open(transcript, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    return finished, records


def _advisor_calls(records: list) -> tuple:
    """``(server_tool_use blocks named advisor, advisor iterations)``.

    Iterations once per response: one response is written as several
    assistant records sharing ``message.id`` and its ``usage``.
    """
    blocks, iterations, seen = 0, [], set()
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message") or {}
        blocks += sum(1 for b in message.get("content") or []
                      if isinstance(b, dict) and b.get("type") == "server_tool_use"
                      and b.get("name") == "advisor")
        if message.get("id") in seen:
            continue
        seen.add(message.get("id"))
        iterations += claude_cli.split_usage(message.get("usage"))[1]
    return blocks, iterations


def test_bg_and_advisor_compose(real_claude: Path, tmp_path: Path) -> None:
    """``bg-advisor-flag``: ``--advisor`` reaches a lean ``--bg`` child (#818).

    The real ``spawn_child``, on the lean default, and the cheapest advised
    pair the advisor docs allow: a Haiku 5.5 child with a Sonnet advisor —
    the advisor re-reads the whole context at its own rate, so the advisor's
    model is most of the bill. The transcript is the proof: the flag only
    says what was asked for, and a child whose advisor is dropped keeps it.
    """
    # A plain directory, as for the bypass test above: `--bg` refuses a
    # fresh git repository as untrusted ("Workspace not trusted").
    tree = tmp_path / "advisor"
    tree.mkdir()
    short_id = ""
    try:
        short_id = dispatch.spawn_child(
            ADVISOR_PROMPT, cwd=tree, model="claude-haiku-5-5", advisor="sonnet",
        )
        assert claude_cli.SHORT_ID_RE.match(short_id), short_id
        finished, records = _finished_transcript(real_claude, short_id)

        session = jobs._parse(short_id, finished)
        assert session.advisor == "sonnet", (
            f"bg-advisor-flag: spawned with --advisor sonnet, respawnFlags say "
            f"{finished.get('respawnFlags')!r}"
        )
        assert session.model == "claude-haiku-5-5", finished.get("respawnFlags")

        blocks, iterations = _advisor_calls(records)
        assert blocks >= 1, (
            "bg-advisor-flag: asked to call the advisor once, the child wrote no "
            "`server_tool_use` block named `advisor` — the tool did not reach it"
        )
        assert iterations, (
            "bg-advisor-flag: an advisor call with no `advisor_message` in "
            "`usage.iterations` — the readout prices advisor calls from there"
        )
        assert all("sonnet" in str(it.get("model")) for it in iterations), (
            f"bg-advisor-flag: the advisor's iterations name {iterations!r}"
        )
        own_models = {(r.get("message") or {}).get("model") for r in records
                      if r.get("type") == "assistant" and not r.get("isApiErrorMessage")}
        own_models.discard(None)
        assert own_models and all("haiku" in m for m in own_models), (
            f"bg-advisor-flag: the child's own records name {sorted(own_models)}"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)


def test_advisor_ranked_below_the_child_is_dropped(real_claude: Path, tmp_path: Path) -> None:
    """``bg-advisor-flag``, the half dispatch refuses on: dropped, silently.

    A Sonnet 5.5 child takes no Haiku 5.5 advisor. Claude Code starts it
    anyway, keeps ``--advisor`` in ``respawnFlags``, and gives it no tool —
    which is why ``check_advisor`` refuses a pairing it can prove inverted.
    The full id is used because ``check_advisor`` ranks only aliases and
    would refuse ``sonnet``/``haiku`` before a child existed. If this starts
    failing because the child *was* advised, or the spawn now errors, the
    refusal is the thing to revisit.
    """
    # A plain directory, as for the bypass test above: `--bg` refuses a
    # fresh git repository as untrusted ("Workspace not trusted").
    tree = tmp_path / "advisor-below"
    tree.mkdir()
    short_id = ""
    try:
        short_id = dispatch.spawn_child(
            ADVISOR_IF_ANY_PROMPT, cwd=tree, model="sonnet", advisor="claude-haiku-5-5",
        )
        assert claude_cli.SHORT_ID_RE.match(short_id), short_id
        finished, records = _finished_transcript(real_claude, short_id)
        assert jobs._parse(short_id, finished).advisor == "claude-haiku-5-5", (
            f"bg-advisor-flag: respawnFlags={finished.get('respawnFlags')!r}"
        )
        blocks, iterations = _advisor_calls(records)
        assert blocks == 0 and not iterations, (
            "bg-advisor-flag: a Haiku 5.5 advisor reached a Sonnet 5.5 child "
            f"({blocks} call(s), {iterations!r}); a pairing below the child's "
            "rank is no longer dropped, so check_advisor's refusal may be stale"
        )
    finally:
        if short_id:
            subprocess.run(["claude", "stop", short_id], capture_output=True, text=True, timeout=60)
            subprocess.run(["claude", "rm", short_id], capture_output=True, text=True, timeout=60)


def _load_user_servers() -> dict:
    """``~/.claude.json``'s ``mcpServers``, or ``{}``. Never raises."""
    try:
        data = json.loads(child_profile.user_claude_json_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


# --- api-error-block: the sentences, checked without a spawn -----------------


def _claude_binary() -> Path:
    """The installed executable ``claude`` resolves to, or skip.

    ``~/.local/bin/claude`` is a symlink into ``versions/<x.y.z>``; the
    versioned file is the one carrying the strings.
    """
    found = shutil.which("claude")
    if not found:  # pragma: no cover - the fixture already skipped
        pytest.skip("no `claude` on PATH")
    real = Path(os.path.realpath(found))
    if not real.is_file():
        pytest.skip(f"`claude` resolves to {real}, which is not a file")
    return real


def _contains(path: Path, needles: dict) -> set:
    """Which of *needles*' keys appear in *path*, matched by any of its forms.

    One pass over a 217MB binary (~0.4s), carrying 256 bytes between chunks so
    a sentence straddling a boundary is still found.
    """
    seen: set = set()
    with path.open("rb") as fh:
        carry = b""
        while True:
            chunk = fh.read(1 << 22)
            if not chunk:
                return seen
            blob = carry + chunk
            for key, forms in needles.items():
                if key not in seen and any(form in blob for form in forms):
                    seen.add(key)
            carry = blob[-256:]


def test_the_api_error_sentences_are_still_the_ones_we_classify() -> None:
    """``api-error-block``: a rename upstream must not silently reclassify.

    ``stalls.from_needs`` is the fallback for a session whose transcript
    cannot be read, and it matches Claude Code's sentence exactly. If one is
    reworded, every such session falls through as "not a stall" and lands
    back under ABANDONED beside ``claude rm`` — the failure #393 is about,
    with no symptom anywhere else.

    A grep, not a spawn: the sentences are only produced by an API error, and
    provoking one is not something a test may do. The binary stores the em
    dash as the JavaScript escape ``\\u2014``, so both forms are searched.
    """
    from mnemo.core.sessions.stalls import NEEDS_BY_ERROR

    sentences = {s for forms in NEEDS_BY_ERROR.values() for s in forms}
    needles = {
        s: {s.encode("utf-8"), s.replace("—", "\\u2014").encode("utf-8")}
        for s in sentences
    }
    found = _contains(_claude_binary(), needles)
    missing = sorted(sentences - found)
    assert not missing, (
        "claude CLI assumption `api-error-block` did not hold (installed "
        f"claude {claude_cli.claude_version()}): these `needs` sentences are "
        f"no longer in the binary: {missing}. Update "
        "mnemo.core.sessions.stalls.NEEDS_BY_ERROR from the current switch "
        "and bump the assumption."
    )


# --- answer-keeps-blocked-state: the shapes, checked without a spawn ---------

#: The write a prompt makes: the previous record spread, the prompt as
#: ``detail``, ``tempo: "active"``, and no ``state`` key anywhere in it.
_ANSWER_WRITE = re.compile(
    rb'\{\.\.\.[\w$]+,(?:\.\.\.[^{}]{0,40}\{detail:[\w$]+,output:null\},)?'
    rb'tempo:"active",inFlight:[\w$]+\(\),needs:void 0')
#: The mid-turn updater leaving a record with a blocked ``state`` alone.
_MIDTURN_GUARD = re.compile(
    rb'if\([\w$]+\?\.tempo==="blocked"\|\|[\w$]+\?\.state==="blocked"\)return;')
#: Every literal pairing of a blocked ``state`` with a ``tempo``.
_BLOCKED_PAIR = re.compile(rb'state:"blocked",tempo:"(\w+)"')


def _scan(path: Path, patterns: dict) -> dict:
    """What each pattern matched in *path*: its first group, else the match.

    Chunked like :func:`_contains`, with a wider carry because a pattern is
    longer than a sentence. A match inside the carry is found twice, which a
    set absorbs.
    """
    found: dict = {key: set() for key in patterns}
    with path.open("rb") as fh:
        carry = b""
        while True:
            chunk = fh.read(1 << 22)
            if not chunk:
                return found
            blob = carry + chunk
            for key, pattern in patterns.items():
                for match in pattern.finditer(blob):
                    found[key].add(match.group(1) if pattern.groups else match.group(0))
            carry = blob[-1024:]


def test_an_answer_keeps_the_blocked_state() -> None:
    """``answer-keeps-blocked-state``: why ``is_blocked`` reads ``tempo`` alone.

    #737 asked whether ``state == "blocked"`` should count as a human being
    needed. It should not: the pair it would add, ``state=blocked,
    tempo=active``, is what a session reads while it works on the answer it
    was just given. That rests on three shapes in the binary. A grep, not a
    spawn, since producing the pair takes a session that asks and a person who
    answers. The names around the shapes are minified and change every build
    (``s``/``E``/``IZt`` in 2.1.321 were ``s``/``C``/``HXt`` in 2.1.319), so
    the patterns match any identifier.
    """
    found = _scan(_claude_binary(), {
        "write": _ANSWER_WRITE, "guard": _MIDTURN_GUARD, "pairs": _BLOCKED_PAIR})
    broke = (
        "claude CLI assumption `answer-keeps-blocked-state` did not hold "
        f"(installed claude {claude_cli.claude_version()}): "
    )
    assert found["write"], broke + (
        "no prompt write that sets tempo=active and leaves `state` alone. If a "
        "prompt now writes `state` too, the blocked/active pair may be gone; "
        "re-read the writer and re-run tools/measure_session_states.py.")
    assert found["guard"], broke + (
        "the mid-turn updater no longer skips a blocked `state`, so the pair "
        "may now last only until the first tool call.")
    assert found["pairs"] == {b"blocked"}, broke + (
        f"a blocked `state` is written with tempo {sorted(found['pairs'])}. "
        "One that is not `blocked` may be a question `Session.is_blocked` "
        "cannot see; that is #737's premise, and this time the binary says so.")
