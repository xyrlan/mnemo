---
feature: grok-host-w2
created: 2026-10-16
verdict: parallel
---

Wave 2 of the Grok round: mnemo's session runtime — dispatch, the session
readers, the verbs that act on a child, and the notices a parent gets — for
children spawned on Grok. Read `design/specs/2026-10-16-grok-host.md` first.
Wave 1 is merged: `mnemo.core.grok.acp.Leader` is the only way into a live
Grok session (`new_session`, `prompt`, `roster`, `close`), and the hooks,
transcripts and model provider already understand Grok.

The same rules as wave 1 hold: Claude byte-identical with a test, Grok
behaviour measured live or read from the source and the PR says which, no
`grok` in the default suite, `PYTHONPATH=src`, Python 3.8, paths as paths.

What a Grok child is, for all four pieces:

- It is a leader session created by `Leader.new_session` in the child's
  worktree, with the dispatch prompt as its first turn. It runs under the
  `GROK_HOME` of the dispatching process (the selected account).
- Its key in every `.mnemo/` ledger is `paths.short_id(session_id)`; the
  full id, the home and the CLI are recorded once at spawn (grok-spawn's
  ledger) and read from there by everyone else, never re-derived.
- It finishes by ending its last turn with its closing report. There is no
  SessionEnd to count on (the spec: a dormant session fires none). Nothing in
  its prompt names a Claude command.

## grok-spawn

- **files:** src/mnemo/core/dispatch.py, src/mnemo/core/child_profile.py, src/mnemo/core/sessions/parents.py, src/mnemo/core/sessions/grants.py, src/mnemo/core/sessions/child_agents.py, src/mnemo/cli/commands/dispatch.py, tests/unit/test_dispatch_grok.py, tests/unit/test_child_agents.py
- **exposes:** `ChildAgent(short_id: str, agent: str, session_id: str, home: Optional[str], cwd: str)`, `child_agents.record(child: ChildAgent, *, vault_root: Union[Path, str, None] = None) -> None`, `child_agents.read(vault_root: Union[Path, str]) -> Dict[str, ChildAgent]`, `parents.parent_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[str]`
- **consumes:** nothing
- **effort:** high

`mnemo dispatch` (issues and `--contract`) spawns on the CLI that
`agent_cli.current_cli(override=args.agent)` names.

- A Grok child gets the same worktree, branch, prompt substance, grant and
  parent record a Claude child gets, and its `ChildAgent` line. `--model` and
  `--effort` reach Grok in Grok's vocabulary; a value Grok cannot take is
  refused before any worktree exists. `--read-only` keeps its meaning (the
  child cannot use file-editing tools) on Grok.
- `--twins` on Grok is refused with one line saying it is not supported yet.
- The dispatcher's own session id comes from `GROK_SESSION_ID` when the
  dispatching session is a Grok one (`parent_from_env`).
- What dispatch prints for the maintainer (`attach:` and similar) names the
  Grok command that opens the child.
- A Grok spawn that fails (no `grok`, no login, leader down) leaves no
  worktree half-made and says what to do.

## grok-sessions

- **files:** src/mnemo/core/sessions/jobs.py, src/mnemo/core/sessions/liveness.py, src/mnemo/core/sessions/residents.py, src/mnemo/core/sessions/stalls.py, src/mnemo/core/sessions/accounts.py, src/mnemo/core/sessions/render.py, src/mnemo/core/activity/context.py, src/mnemo/cli/commands/sessions.py, src/mnemo/cli/commands/session.py, tests/unit/test_sessions_grok.py, tests/unit/test_stalls_grok.py
- **exposes:** `read_sessions(root: Optional[Path] = None, *, cwd: Optional[str] = None, …) -> List[Session]` (Grok children included, `agent="grok"`), `read_stall(session: Session, *, window: int = TAIL_WINDOW) -> Optional[Stall]`
- **consumes:** `child_agents.read(vault_root: Union[Path, str]) -> Dict[str, ChildAgent]` from grok-spawn
- **effort:** high

Everything that lists or describes sessions — `mnemo sessions [--json]`,
`mnemo session <id>`, the statusline count, `deliver --review` — shows Grok
children beside Claude ones.

- A Grok child's `state`, `tempo`, `needs`, `live`, `model`, `effort`,
  `context_tokens` mean what they mean for a Claude child, mapped from the
  roster's `activity` and Grok's session files; where Grok has no
  equivalent, the field is `None`, never a guess. A child waiting on a
  person (`needs_input`) reads as blocked.
- A Grok child whose leader is down, or whose home is gone, still appears,
  with what the disk says.
- `read_stall` recognises a Grok rate limit (StopFailure `rate_limit`,
  `events.jsonl`, or what you measure) so the resume path can wake it.
- `mnemo sessions --json` gains `agent`; every other field keeps its shape.

## grok-verbs

- **files:** src/mnemo/core/sessions/wake.py, src/mnemo/core/sessions/delivery.py, src/mnemo/core/sessions/rewake.py, src/mnemo/core/sessions/pr_follow.py, src/mnemo/core/sessions/tree_sweep.py, src/mnemo/cli/commands/deliver.py, src/mnemo/cli/commands/resume.py, tests/unit/test_verbs_grok.py
- **exposes:** `wake(session_id: str, *, cwd: str, timeout: float = 120.0) -> Optional[str]`, `wake_for_pr(session_id: str, *, cwd: str, pr: int, events, …)`, `stop_session(short_id: str) -> Optional[str]`
- **consumes:** `child_agents.read(vault_root: Union[Path, str]) -> Dict[str, ChildAgent]` from grok-spawn, `read_sessions(root: Optional[Path] = None, *, cwd: Optional[str] = None, …) -> List[Session]` from grok-sessions
- **effort:** high

Every verb mnemo applies to a child works on a Grok child, each on the CLI the
child was spawned on (never the current one):

- wake (manual `mnemo resume`, automatic rewake after a rate limit, and
  pr-follow's nudge when checks fail) sends the turn through the leader;
- stop (`deliver --stop-done`, delivery's own stop) closes the session;
- `deliver`, `tree-sweep` and `resume --agent` treat Grok children as they
  treat Claude ones.

A verb on a Grok child that cannot reach its leader reports it and leaves
the child's ledgers as they were.

Found in wave 1 (grok-hooks, PR #711): `pr_follow` still keys a session by
`session_id[:8]`. For a Grok child that collides (UUIDv7, spec decision 5);
every ledger this piece touches keys Grok children by `paths.short_id`.

## grok-notices

- **files:** src/mnemo/core/sessions/inbox.py, src/mnemo/core/sessions/child_notices.py, src/mnemo/core/sessions/report_card.py, src/mnemo/core/sessions/held_notices.py, src/mnemo/cli/commands/child_report.py, src/mnemo/cli/commands/child_notices.py, src/mnemo/hooks/session_end.py, tests/unit/test_notices_grok.py
- **exposes:** `notify(vault_root: Union[Path, str], session_id: str, text: str) -> bool`, `deliver(vault_root: Union[Path, str], session_id: str, text: str) -> str`
- **consumes:** `child_agents.read(vault_root: Union[Path, str]) -> Dict[str, ChildAgent]` from grok-spawn, `read_sessions(root: Optional[Path] = None, *, cwd: Optional[str] = None, …) -> List[Session]` from grok-sessions
- **effort:** high

A parent learns its Grok child finished, and a Grok parent learns any child
finished, as a Claude parent does today:

- **Finish.** A Grok child is finished when its last turn ended with its
  closing report and nothing resumed it. When that happens the end-of-session
  pipeline the SessionEnd hook runs for a Claude child (notify the parent,
  follow the PR, extraction, briefing) runs **once** for it, from whichever
  watcher sees it first. Two watchers, a restart, or a later SessionEnd never
  run it twice.
- **Report card.** The `<mnemo-child-finished …>` notice for a Grok child
  carries the same fields, read from its `chat_history.jsonl`.
- **Grok parent.** A parent that is a Grok session is addressed by its
  session id and home (recorded when it dispatched), and the notice reaches
  it as a user turn through the leader — waking it if it is dormant. A
  notice that cannot be delivered is held, as today, and handed over at the
  parent's next turn through the channel grok-hooks built.

## grok-backfill

- **files:** src/mnemo/core/backfill/ledger.py, src/mnemo/core/backfill/harvest.py, src/mnemo/core/friction/capture.py, src/mnemo/cli/commands/backfill.py, tests/unit/test_backfill_grok.py
- **exposes:** `mnemo backfill` covers Grok sessions
- **consumes:** nothing
- **effort:** medium

Found by wave 1's grok-transcripts (PR #707): `find_transcripts(grok=True)`
adds Grok sessions, but the backfill ledger, harvest and friction capture
key a session by its file's stem, and every Grok history is
`chat_history.jsonl`. Turned on as they are, backfill would file every Grok
session under one entry and re-run each one, with a model call, on every
sweep, and a Grok session's corrections would be recorded under
`chat_history`.

- Every session these files record is keyed by its real id
  (`briefing.session_id_for`), Grok and Claude alike; a Claude session's key
  is what it is today, byte for byte, and existing ledger lines still match.
- `mnemo backfill` then asks discovery for Grok sessions too, and a second
  sweep over the same sessions spends nothing.
