"""mnemo's hooks in a Grok Build session (spec 2026-10-16-grok-host, decisions 6-8).

The payload shapes below are the ones grok 1.0.60 sent probe hooks on
2026-10-16 (a throwaway HOME, canary hooks in its ~/.claude/settings.json),
with the paths replaced. Facts the code leans on, each measured there:

- Grok's payload carries camelCase keys (``hookEventName`` in snake case,
  ``sessionId``, ``toolName``, ``toolUseId``, ``promptId``, ``timestamp``)
  beside Claude's snake_case aliases; ``tool_name`` is Grok's own.
- The same event fired from two hook files reaches both with the same
  ``timestamp`` and ``toolUseId``/``promptId``.
- PreToolUse context reaches the model; SessionStart and UserPromptSubmit
  context does not.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

from mnemo.core.grok import briefing_file
from mnemo.core.grok import hooks as grok
from mnemo.core.rule_activation import build_index, write_index

SID = "01a144cd-aa48-7271-b671-114727e068a2"


def _stamp() -> str:
    """A payload timestamp no other test uses, so claims never collide."""
    return "2026-10-16T13:01:%02d.%06d+00:00" % (uuid.uuid4().int % 60, uuid.uuid4().int % 10 ** 6)


def grok_payload(event: str, cwd: Path, sid: str = SID, **extra: Any) -> Dict[str, Any]:
    """A payload shaped like grok 1.0.60's for ``event`` (PascalCase)."""
    snake = {"SessionStart": "session_start", "UserPromptSubmit": "user_prompt_submit",
             "PreToolUse": "pre_tool_use", "SessionEnd": "session_end"}[event]
    transcript = str(cwd / ".grok-home" / "sessions" / "enc" / sid / "updates.jsonl")
    out: Dict[str, Any] = {
        "hookEventName": snake, "sessionId": sid, "cwd": str(cwd),
        "workspaceRoot": str(cwd), "timestamp": _stamp(), "permissionMode": "default",
        "hook_event_name": event, "session_id": sid, "permission_mode": "default",
    }
    if event == "SessionStart":
        out["source"] = "new"
    if event == "UserPromptSubmit":
        out["promptId"] = str(uuid.uuid4())
    if event in ("PreToolUse", "SessionEnd"):
        out.update(transcriptPath=transcript, transcript_path=transcript)
    if event == "SessionEnd":
        out["reason"] = "shutdown"
    out.update(extra)
    return out


def tool_payload(tool: str, tool_input: dict, cwd: Path, sid: str = SID) -> Dict[str, Any]:
    tid = "call-%s-0" % uuid.uuid4()
    return grok_payload("PreToolUse", cwd, sid, toolName=tool, tool_name=tool,
                        toolInput=tool_input, tool_input=tool_input,
                        toolUseId=tid, tool_use_id=tid, toolInputTruncated=False)


def _run(module: str, payload: dict, monkeypatch) -> str:
    import importlib

    hook = importlib.import_module("mnemo.hooks." + module)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert hook.main() == 0
    return out.getvalue()


@pytest.fixture(autouse=True)
def _isolated(tmp_tempdir, monkeypatch):
    """Claims live under the temp dir; no Grok variable leaks in from the host."""
    for name in ("GROK_HOOK_EVENT", "GROK_SESSION_ID", "GROK_HOME", "MNEMO_GROK_SESSION_END"):
        monkeypatch.delenv(name, raising=False)
    return tmp_tempdir


def _config(vault: Path, monkeypatch, **cfg: Any) -> dict:
    full = {"vaultRoot": str(vault)}
    full.update(cfg)
    path = vault / "mnemo.config.json"
    path.write_text(json.dumps(full), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(path))
    return full


# --- telling a Grok payload apart ------------------------------------------------


def test_a_grok_payload_is_told_apart_by_its_camelcase_keys(tmp_path: Path) -> None:
    assert grok.is_grok_payload(tool_payload("read_file", {"target_file": "/x"}, tmp_path), env={})
    assert grok.is_grok_payload(grok_payload("SessionEnd", tmp_path), env={})


def test_a_claude_payload_is_never_grok_even_inside_a_grok_shell(tmp_path: Path) -> None:
    claude = {"session_id": "c1a0de00-1111", "hook_event_name": "PreToolUse",
              "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(tmp_path)}
    assert not grok.is_grok_payload(claude, env={})
    # A `claude` started from a Grok session (or by a Grok hook) inherits both
    # variables; its own session id is not Grok's.
    env = {"GROK_HOOK_EVENT": "pre_tool_use", "GROK_SESSION_ID": SID}
    assert not grok.is_grok_payload(claude, env=env)
    assert grok.is_grok_payload(dict(claude, session_id=SID), env=env)
    assert not grok.is_grok_payload(["not", "a", "payload"], env=env)  # type: ignore[arg-type]


@pytest.mark.parametrize("grok_name, claude_name", [
    ("run_terminal_command", "Bash"), ("read_file", "Read"), ("write", "Write"),
    ("search_replace", "Edit"), ("hashline_edit", "Edit"), ("grep", "Grep"),
    ("list_dir", "Glob"), ("spawn_subagent", "Agent"),
])
def test_grok_tool_names_map_to_claude_ones(grok_name: str, claude_name: str) -> None:
    assert grok.claude_tool_name(grok_name) == claude_name


def test_claude_names_pass_through_and_unknown_names_are_none() -> None:
    for name in ("Bash", "Read", "Edit", "Write", "MultiEdit", "Grep"):
        assert grok.claude_tool_name(name) == name
    assert grok.claude_tool_name("mcp__mnemo__read_mnemo_rule") is None
    assert grok.claude_tool_name("") is None


def test_normalize_puts_a_grok_call_in_claudes_vocabulary(tmp_path: Path) -> None:
    read = grok.normalize(tool_payload("read_file", {"target_file": "/r/a.py"}, tmp_path))
    assert read["tool_name"] == "Read" and read["grok_tool_name"] == "read_file"
    assert read["tool_input"] == {"target_file": "/r/a.py", "file_path": "/r/a.py"}
    bash = grok.normalize(tool_payload("run_terminal_command", {"command": "ls"}, tmp_path))
    assert bash["tool_name"] == "Bash" and bash["tool_input"] == {"command": "ls"}
    odd = grok.normalize(tool_payload("image_gen", {"prompt": "x"}, tmp_path))
    assert odd["tool_name"] == "image_gen"


# --- one run per event ----------------------------------------------------------


def test_the_second_copy_of_an_event_is_refused(tmp_path: Path) -> None:
    payload = tool_payload("read_file", {"target_file": "/x"}, tmp_path)
    assert grok.claim_once(payload, root=tmp_path / "once")
    # The copy from the other hook file: same stamp, same toolUseId.
    assert not grok.claim_once(dict(payload, GROK_HOOK_NAME="project/x"), root=tmp_path / "once")
    # The next call is a new event.
    assert grok.claim_once(tool_payload("read_file", {"target_file": "/x"}, tmp_path),
                           root=tmp_path / "once")


def test_a_payload_with_no_timestamp_always_runs(tmp_path: Path) -> None:
    payload = tool_payload("read_file", {"target_file": "/x"}, tmp_path)
    del payload["timestamp"]
    assert grok.claim_once(payload, root=tmp_path / "once")
    assert grok.claim_once(payload, root=tmp_path / "once")


def test_old_claims_are_swept(tmp_path: Path) -> None:
    root = tmp_path / "once"
    assert grok.claim_once(grok_payload("SessionStart", tmp_path), root=root)
    (old,) = list(root.iterdir())
    os.utime(old, (1_000_000, 1_000_000))
    assert grok.sweep_claims(root) == 1 and not list(root.iterdir())


# --- context waiting for the first tool call --------------------------------------


def test_pending_context_goes_out_once_marked_and_in_order(tmp_path: Path) -> None:
    grok.stash(tmp_path, SID, grok.PROMPT, "reflex one")
    grok.stash(tmp_path, SID, grok.BRIEFING, "the briefing")
    grok.stash(tmp_path, SID, grok.HELD, "notice a", append=True)
    grok.stash(tmp_path, SID, grok.HELD, "notice b", append=True)
    grok.stash(tmp_path, SID, grok.PROMPT, "reflex two")  # a new prompt replaces the old
    text = grok.take(tmp_path, SID)
    assert text == ("[mnemo:briefing via=first-tool-call]\nthe briefing\n\n"
                    "[mnemo:held-notices]\nnotice a\n\nnotice b\n\n"
                    "[mnemo:prompt-context]\nreflex two")
    assert grok.take(tmp_path, SID) == ""


def test_a_prompt_with_nothing_clears_the_last_prompts_context_only(tmp_path: Path) -> None:
    grok.stash(tmp_path, SID, grok.BRIEFING, "the briefing")
    grok.stash(tmp_path, SID, grok.PROMPT, "stale reflex")
    grok.stash(tmp_path, SID, grok.PROMPT, "")
    assert grok.take(tmp_path, SID) == "[mnemo:briefing via=first-tool-call]\nthe briefing"


def test_stale_context_is_dropped_and_sessions_do_not_mix(tmp_path: Path) -> None:
    grok.stash(tmp_path, SID, grok.PROMPT, "old", now=1_000.0)
    grok.stash(tmp_path, "other-session", grok.PROMPT, "theirs")
    assert grok.take(tmp_path, SID, now=1_000.0 + grok.PENDING_TTL_S + 1) == ""
    assert "theirs" in grok.take(tmp_path, "other-session")


def test_context_joins_a_hooks_envelope_but_never_a_deny() -> None:
    enrich = json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "additionalContext": "rule body",
        "updatedInput": {"file_path": "/real"}}})
    merged = json.loads(grok.with_context(enrich, "CTX"))["hookSpecificOutput"]
    assert merged["additionalContext"] == "CTX\n\nrule body"
    assert merged["updatedInput"] == {"file_path": "/real"}
    alone = json.loads(grok.with_context("", "CTX"))["hookSpecificOutput"]
    assert alone == {"hookEventName": "PreToolUse", "additionalContext": "CTX"}
    deny = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny"}})
    assert grok.with_context(deny, "CTX") == deny and grok.is_deny(deny)


# --- PreToolUse ---------------------------------------------------------------------


def _enforce_rule(vault: Path, project: str) -> Path:
    feedback = vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / "no-force-push.md").write_text(
        "---\nname: no-force-push\nstability: stable\ntags:\n  - auto-promoted\n"
        f"sources:\n  - bots/{project}/memory/no-force-push.md\n"
        "enforce:\n  tool: Bash\n  deny_pattern: \"git push --force\"\n"
        "  reason: \"Never force-push\"\n---\n\nBody.\n", encoding="utf-8")
    project_dir = vault / "bots" / project
    (project_dir / ".git").mkdir(parents=True, exist_ok=True)
    write_index(vault, build_index(vault))
    return project_dir


def _enrich_rule(vault: Path, project: str, glob: str) -> Path:
    feedback = vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / "read-the-guide.md").write_text(
        "---\nname: read-the-guide\nslug: read-the-guide\nstability: stable\n"
        f"tags:\n  - auto-promoted\nsources:\n  - bots/{project}/memory/g.md\n"
        f"activates_on:\n  tools: [Edit, Write]\n  path_globs: [\"{glob}\"]\n"
        "---\n\nRead GUIDE.md before touching the parser.\n", encoding="utf-8")
    project_dir = vault / "bots" / project
    (project_dir / ".git").mkdir(parents=True, exist_ok=True)
    write_index(vault, build_index(vault))
    return project_dir


def test_enforcement_denies_a_grok_terminal_command(tmp_vault: Path, monkeypatch) -> None:
    project_dir = _enforce_rule(tmp_vault, "mnemo")
    _config(tmp_vault, monkeypatch, enforcement={"enabled": True})
    out = _run("pre_tool_use", tool_payload(
        "run_terminal_command", {"command": "git push --force origin main"}, project_dir),
        monkeypatch)
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert hook["permissionDecisionReason"].startswith("Never force-push")


def test_a_deny_keeps_pending_context_for_the_next_call(tmp_vault: Path, monkeypatch) -> None:
    project_dir = _enforce_rule(tmp_vault, "mnemo")
    _config(tmp_vault, monkeypatch, enforcement={"enabled": True})
    grok.stash(tmp_vault, SID, grok.PROMPT, "reflex block")
    _run("pre_tool_use", tool_payload(
        "run_terminal_command", {"command": "git push --force"}, project_dir), monkeypatch)
    out = _run("pre_tool_use", tool_payload(
        "run_terminal_command", {"command": "ls"}, project_dir), monkeypatch)
    assert "reflex block" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("tool", ["search_replace", "hashline_edit", "write"])
def test_enrichment_reads_grok_file_tools(tmp_vault: Path, monkeypatch, tool: str) -> None:
    """Rules enrich Edit and Write (``_VALID_ENRICH_TOOLS``), Read never."""
    project_dir = _enrich_rule(tmp_vault, "mnemo", "src/parser.py")
    _config(tmp_vault, monkeypatch, enrichment={"enabled": True})
    out = _run("pre_tool_use", tool_payload(
        tool, {"file_path": str(project_dir / "src" / "parser.py")}, project_dir), monkeypatch)
    assert "Read GUIDE.md" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_the_first_tool_call_carries_the_prompts_context_once(tmp_vault: Path, monkeypatch) -> None:
    _config(tmp_vault, monkeypatch)
    grok.stash(tmp_vault, SID, grok.PROMPT, "mnemo reflex context:\n• [[use-x]]: body")
    first = _run("pre_tool_use", tool_payload("read_file", {"target_file": "/r/a"}, tmp_vault),
                 monkeypatch)
    text = json.loads(first)["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("[mnemo:prompt-context]\nmnemo reflex context:")
    second = _run("pre_tool_use", tool_payload("read_file", {"target_file": "/r/b"}, tmp_vault),
                  monkeypatch)
    assert second == ""


def test_a_duplicate_pre_tool_use_is_a_no_op(tmp_vault: Path, monkeypatch) -> None:
    project_dir = _enforce_rule(tmp_vault, "mnemo")
    _config(tmp_vault, monkeypatch, enforcement={"enabled": True})
    payload = tool_payload("run_terminal_command", {"command": "git push --force"}, project_dir)
    assert _run("pre_tool_use", payload, monkeypatch)
    assert _run("pre_tool_use", payload, monkeypatch) == ""


def test_a_claude_payload_never_reaches_the_grok_path(tmp_vault: Path, monkeypatch) -> None:
    """Decision 8: the Claude path is master's, byte for byte."""
    project_dir = _enforce_rule(tmp_vault, "mnemo")
    _config(tmp_vault, monkeypatch, enforcement={"enabled": True})
    grok.stash(tmp_vault, "c1a0de00", grok.PROMPT, "must stay put")

    def _boom(*_a, **_k):
        raise AssertionError("Grok helper called for a Claude payload")

    for name in ("claim_once", "normalize", "take", "with_context", "stash"):
        monkeypatch.setattr(grok, name, _boom)
    payload = {"session_id": "c1a0de00", "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "git push --force"}, "cwd": str(project_dir)}
    out = _run("pre_tool_use", payload, monkeypatch)
    assert out == json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"],
    }})
    assert (tmp_vault / ".mnemo" / "grok-pending" / "c1a0de00.json").exists()
    up = _run("user_prompt_submit", {"session_id": "c1a0de00", "cwd": str(project_dir),
                                     "prompt": "ok"}, monkeypatch)
    assert up == ""


# --- UserPromptSubmit -------------------------------------------------------------------


def test_a_grok_prompts_reflex_waits_for_the_first_tool_call(
        tmp_vault: Path, monkeypatch, synthetic_index) -> None:
    _config(tmp_vault, monkeypatch, reflex={"enabled": True})
    synthetic_index(tmp_vault)
    prompt = grok_payload("UserPromptSubmit", tmp_vault,
                          prompt="How do I mock prisma in a jest test with typescript")
    assert _run("user_prompt_submit", prompt, monkeypatch) == ""
    out = _run("pre_tool_use", tool_payload("read_file", {"target_file": "/r/a"}, tmp_vault),
               monkeypatch)
    text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("[mnemo:prompt-context]\nmnemo reflex context:")
    assert "[[use-prisma-mock]]" in text


def test_held_notices_wait_for_a_tool_call_across_prompts(
        tmp_vault: Path, monkeypatch) -> None:
    from mnemo.core.sessions import held_notices

    _config(tmp_vault, monkeypatch, dispatch={"notifyParent": True})
    held_notices.hold(tmp_vault, SID, '<mnemo-child-finished id="c0da0f55">\ndone',
                      short_id="c0da0f55")
    assert _run("user_prompt_submit", grok_payload("UserPromptSubmit", tmp_vault, prompt="hi"),
                monkeypatch) == ""
    # A second prompt before any tool call: the notice is still waiting.
    _run("user_prompt_submit", grok_payload("UserPromptSubmit", tmp_vault, prompt="and?"),
         monkeypatch)
    out = _run("pre_tool_use", tool_payload("read_file", {"target_file": "/r/a"}, tmp_vault),
               monkeypatch)
    text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("[mnemo:held-notices]") and 'id="c0da0f55"' in text


# --- SessionStart -------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false"] + list(args),
        capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _repo(tmp_path: Path, name: str = "proj") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("# proj\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _briefing(vault: Path, project: str, body: str) -> None:
    folder = vault / "bots" / project / "briefings" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "prev0001.md").write_text(
        "---\ntype: briefing\nagent: %s\nsession_id: prev0001\ndate: 2026-10-15\n"
        "duration_minutes: 42\n---\n\n## TL;DR\n%s\n" % (project, body), encoding="utf-8")


def _grok_session_dir(tmp_path: Path, monkeypatch, cwd: Path, sid: str, loaded: bool) -> Path:
    from mnemo.core.grok import paths

    home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(home))
    folder = home / "sessions" / paths.encode_cwd(str(cwd)) / sid
    folder.mkdir(parents=True)
    files = [{"file_name": briefing_file.FILE_NAME, "source": "project",
              "file_path": str(briefing_file.rule_path(cwd))}] if loaded else []
    (folder / "prompt_context.json").write_text(json.dumps({"agents_md_files": files}),
                                               encoding="utf-8")
    return folder


def test_a_grok_session_start_writes_the_next_sessions_briefing(
        tmp_path: Path, tmp_vault: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _config(tmp_vault, monkeypatch, injection={"enabled": True})
    _briefing(tmp_vault, "proj", "Codeword BRIEFOWL.")
    _grok_session_dir(tmp_path, monkeypatch, repo, SID, loaded=False)
    out = _run("session_start", grok_payload("SessionStart", repo), monkeypatch)
    assert out == ""  # Grok would drop it
    written = briefing_file.rule_path(repo).read_text(encoding="utf-8")
    assert "[mnemo:briefing via=rules-file project=proj]" in written and "BRIEFOWL" in written
    assert _git(repo, "status", "--porcelain") == ""
    # This session started before the file existed: its first tool call has it.
    assert "BRIEFOWL" in grok.take(tmp_vault, SID)


def test_a_session_that_loaded_the_file_is_not_briefed_twice(
        tmp_path: Path, tmp_vault: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _config(tmp_vault, monkeypatch, injection={"enabled": True})
    _briefing(tmp_vault, "proj", "Codeword BRIEFOWL.")
    _grok_session_dir(tmp_path, monkeypatch, repo, SID, loaded=True)
    _run("session_start", grok_payload("SessionStart", repo), monkeypatch)
    assert briefing_file.rule_path(repo).exists()
    assert grok.take(tmp_vault, SID) == ""


def test_the_briefing_reaches_every_worktree(tmp_path: Path, tmp_vault: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    tree = tmp_path / "proj-wt-1"
    _git(repo, "worktree", "add", "-q", str(tree))
    _config(tmp_vault, monkeypatch, injection={"enabled": True})
    _briefing(tmp_vault, "proj", "Codeword BRIEFOWL.")
    _run("session_start", grok_payload("SessionStart", tree), monkeypatch)
    for where in (repo, tree):
        assert "project=proj]" in briefing_file.rule_path(where).read_text(encoding="utf-8")
        assert _git(where, "status", "--porcelain") == ""


def test_a_claude_session_start_writes_no_rules_file(
        tmp_path: Path, tmp_vault: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _config(tmp_vault, monkeypatch, injection={"enabled": True})
    _briefing(tmp_vault, "proj", "Codeword BRIEFOWL.")
    out = _run("session_start", {"session_id": "c1a0de00", "hook_event_name": "SessionStart",
                                 "cwd": str(repo), "source": "startup"}, monkeypatch)
    assert "BRIEFOWL" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert not (repo / ".grok").exists()


# --- SessionEnd ---------------------------------------------------------------------------


def test_a_grok_session_end_hands_off_and_returns(tmp_path: Path, monkeypatch) -> None:
    from mnemo.hooks import session_end

    started = []
    monkeypatch.setattr(session_end, "_spawn_grok_worker", started.append)

    def _no_config():
        raise AssertionError("the hook itself read config")

    monkeypatch.setattr("mnemo.core.config.load_config", _no_config)
    payload = grok_payload("SessionEnd", tmp_path)
    assert _run("session_end", payload, monkeypatch) == ""
    (name,) = started
    assert json.loads(Path(name).read_text(encoding="utf-8")) == payload
    # The copy from a second hook file starts nothing.
    assert _run("session_end", payload, monkeypatch) == "" and len(started) == 1


def test_the_worker_briefs_from_chat_history_then_rewrites_the_file(
        tmp_path: Path, tmp_vault: Path, monkeypatch) -> None:
    from mnemo.hooks import session_end

    repo = _repo(tmp_path)
    _config(tmp_vault, monkeypatch, injection={"enabled": True}, briefings={"enabled": True})
    folder = _grok_session_dir(tmp_path, monkeypatch, repo, SID, loaded=True)
    (folder / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (folder / "chat_history.jsonl").write_text('{"type": "system"}\n', encoding="utf-8")
    grok.stash(tmp_vault, SID, grok.PROMPT, "never sent")
    briefed = []

    def _brief(path, agent):
        briefed.append((Path(path), agent))
        _briefing(tmp_vault, "proj", "Written by this session: OWLNEW.")

    monkeypatch.setattr(session_end, "_run_briefing", _brief)
    payload = grok_payload("SessionEnd", repo, transcript_path=str(folder / "updates.jsonl"))
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(session_end.GROK_PAYLOAD_ENV, str(handoff))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert session_end.main() == 0
    assert briefed == [(folder / "chat_history.jsonl", "proj")]
    assert "OWLNEW" in briefing_file.rule_path(repo).read_text(encoding="utf-8")
    assert not handoff.exists() and session_end.GROK_PAYLOAD_ENV not in os.environ
    assert grok.take(tmp_vault, SID) == ""


def test_a_grok_childs_parent_is_told_under_its_grok_short_id(
        tmp_path: Path, tmp_vault: Path, monkeypatch) -> None:
    from mnemo.hooks import session_end

    told = []
    monkeypatch.setattr(session_end, "_maybe_notify_parent",
                        lambda *a, **k: told.append(k.get("short")))
    _config(tmp_vault, monkeypatch)
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps(grok_payload("SessionEnd", tmp_vault)), encoding="utf-8")
    monkeypatch.setenv(session_end.GROK_PAYLOAD_ENV, str(handoff))
    assert session_end.main() == 0
    assert told == ["27e068a2"]  # the last eight hex digits, not the first


def test_the_memory_path_guard_reads_grok_write_and_edit(tmp_path: Path, monkeypatch) -> None:
    """#637 on Grok's ``write`` (rewritten) and ``search_replace`` (redirected)."""
    real_projects = tmp_path / "home" / ".claude" / "projects"
    (real_projects / "-proj" / "memory").mkdir(parents=True)
    account = tmp_path / "home" / ".claude-account"
    account.mkdir()
    try:
        os.symlink(str(real_projects), str(account / "projects"), target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip("creating a symlink needs a privilege this platform withholds: %r" % exc)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    real = Path(os.path.realpath(str(real_projects))) / "-proj" / "memory"
    asked = account / "projects" / "-proj" / "memory" / "new-rule.md"
    out = _run("pre_tool_use", tool_payload(
        "write", {"file_path": str(asked), "content": "- a rule\n"}, tmp_path), monkeypatch)
    rewritten = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert Path(rewritten["file_path"]) == real / "new-rule.md"
    assert rewritten["content"] == "- a rule\n"
    (real / "MEMORY.md").write_text("- a\n", encoding="utf-8")
    out = _run("pre_tool_use", tool_payload(
        "search_replace", {"file_path": str(account / "projects" / "-proj" / "memory" / "MEMORY.md"),
                           "old_string": "a", "new_string": "b"}, tmp_path), monkeypatch)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_an_unreadable_hand_off_ends_nothing(tmp_path: Path, monkeypatch) -> None:
    from mnemo.hooks import session_end

    ran = []
    monkeypatch.setattr(session_end, "_run", lambda *a, **k: ran.append(a) or 0)
    handoff = tmp_path / "handoff.json"
    handoff.write_text("not json", encoding="utf-8")
    monkeypatch.setenv(session_end.GROK_PAYLOAD_ENV, str(handoff))
    assert session_end.main() == 0 and ran == [] and not handoff.exists()
