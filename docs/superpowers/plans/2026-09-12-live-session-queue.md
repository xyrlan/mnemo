# Live Session Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the maintainer a blocked-first queue of running Claude Code background sessions, so six parallel sessions cost one stream of attention instead of six.

**Architecture:** A read-only reader over `~/.claude/jobs/*/state.json` (written by Claude Code, never by mnemo) feeds three human-facing surfaces — `mnemo sessions`, `--watch`, and a statusline segment. A detector rides existing triggers, spots `tempo: blocked → active` edges, and records where the human's answer is written so extraction can treat it as high signal. Nothing here enters model context.

**Tech Stack:** Python 3.8+, stdlib only (`json`, `pathlib`, `time`). pytest with `tmp_path`/`capsys`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-live-session-queue-design.md`

---

## Background the engineer needs

**What writes the data.** Claude Code 2.1.269 writes `~/.claude/jobs/<short-id>/state.json` for every background session (`claude --bg`). mnemo only ever reads it. Never write, never delete, never repair that directory.

**The one field everything hinges on.** `state` and `tempo` are different axes:

- `state` — process phase: `working`, `done`
- `tempo` — whether a human is needed: `blocked`, `active`, `idle`

A session waiting on a question sits at **`state=working, tempo=blocked`**. Reading `state` alone files that session under "working" and the queue loses its entire purpose. **Always branch on `tempo`.**

**Real observed shapes** (2026-09-12, three sessions). Fields the code uses:

```json
{
  "state": "working",
  "tempo": "blocked",
  "needs": "answer: Resumo do resto do README em qual idioma? (Português · Inglês)",
  "detail": "Showing top of README",
  "suggestedReply": "a loja é a domdogpva, pedido 42",
  "tokens": 275532,
  "name": "refactor-complementos-system",
  "intent": "uma pergunta eu nao lembro o que voce tinha falado sobre...",
  "cwd": "/Users/xyrlan/github/meunu",
  "sessionId": "e51f48ec-a41d-4484-8b90-75fecc62bd6c",
  "linkScanPath": "/Users/xyrlan/.claude/projects/-Users-.../678637f0-....jsonl",
  "updatedAt": "2026-08-19T21:59:28.654Z",
  "children": [{"id": "307", "href": "https://github.com/...", "kind": "pr"}]
}
```

`needs` appeared in 3/3. `suggestedReply` in 1/3 — optional. Any field may be absent: parse defensively, never raise.

**Do not spawn sessions in tests.** `tests/conftest.py:55` blocks `subprocess.Popen`; a test needing a real spawn must carry `@pytest.mark.real_spawn`. No task in this plan needs one. History: 46 orphan processes once froze the maintainer's Mac.

---

## File Structure

**Create:**

| path | responsibility |
|---|---|
| `src/mnemo/core/sessions/__init__.py` | package marker + public re-exports |
| `src/mnemo/core/sessions/jobs.py` | read + normalize `state.json` into a `Session` dataclass. Only module that knows the on-disk schema. |
| `src/mnemo/core/sessions/render.py` | format a `list[Session]` as the terminal block. Pure: takes data, returns str. |
| `src/mnemo/core/sessions/detector.py` | track `tempo` per session, record `blocked → active` edges into `<vault>/.mnemo/session-queue.json` |
| `src/mnemo/cli/commands/sessions.py` | `mnemo sessions` CLI command (`--json`, `--watch`, `--all`) |
| `tests/unit/test_sessions_jobs.py` | reader + schema-drift tests |
| `tests/unit/test_sessions_render.py` | render + ordering tests |
| `tests/unit/test_sessions_detector.py` | edge-detection + idempotence tests |
| `tests/unit/test_doctor_background_sessions.py` | doctor check tests |

**Modify:**

| path | change |
|---|---|
| `src/mnemo/cli/parser.py` | register the `sessions` subparser |
| `src/mnemo/cli/commands/__init__.py` | import the new command module so it registers |
| `src/mnemo/statusline.py` | append `N esperando` to the segment |
| `src/mnemo/cli/commands/doctor_checks/misc.py` | add `_doctor_check_background_sessions` |
| `src/mnemo/cli/commands/doctor.py` | register that check in the ordered list |
| `CHANGELOG.md` | one entry |

The split is by responsibility: `jobs.py` owns the schema, `render.py` owns presentation, `detector.py` owns state over time. Schema drift then touches exactly one file.

---

### Task 1: Session reader

**Files:**
- Create: `src/mnemo/core/sessions/__init__.py`
- Create: `src/mnemo/core/sessions/jobs.py`
- Test: `tests/unit/test_sessions_jobs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sessions_jobs.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mnemo.core.sessions'`

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/sessions/__init__.py`:

```python
"""Reading Claude Code's background-session state for the live queue.

Claude Code 2.1.269 writes ``~/.claude/jobs/<short-id>/state.json`` for every
``claude --bg`` session. mnemo reads it and never writes it: the state belongs
to Claude Code, and an upstream schema change must degrade our render rather
than break a command.

See ``docs/superpowers/specs/2026-09-12-live-session-queue-design.md``.
"""
from __future__ import annotations

from mnemo.core.sessions.jobs import Session, jobs_dir, read_sessions

__all__ = ["Session", "jobs_dir", "read_sessions"]
```

Create `src/mnemo/core/sessions/jobs.py`:

```python
"""Parse ``~/.claude/jobs/*/state.json`` into :class:`Session` records.

The only module that knows the on-disk schema, so an upstream change touches
one file.

``state`` and ``tempo`` are different axes and the distinction carries the
feature:

- ``state`` is the process phase: ``working``, ``done``
- ``tempo`` is whether a human is needed: ``blocked``, ``active``, ``idle``

A session waiting on a question sits at ``state=working, tempo=blocked``.
Branching on ``state`` files it under "working" and the queue stops surfacing
the only sessions it exists to surface. Measured on a real dispatch,
2026-09-12; ``timeline.jsonl`` records ``state`` transitions and never
mentions ``tempo``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def jobs_dir() -> Path:
    """Where Claude Code keeps background-session state."""
    return Path(os.path.expanduser("~/.claude/jobs"))


@dataclass(frozen=True)
class Session:
    """One background session, as much of it as the file actually had."""

    short_id: str
    state: str | None = None
    tempo: str | None = None
    needs: str | None = None
    detail: str | None = None
    suggested_reply: str | None = None
    name: str | None = None
    intent: str | None = None
    cwd: str | None = None
    tokens: int | None = None
    session_id: str | None = None
    link_scan_path: str | None = None
    updated_at: str | None = None
    children: tuple[dict[str, Any], ...] = ()

    @property
    def is_blocked(self) -> bool:
        """True when a human is needed. Driven by ``tempo``, never ``state``."""
        return self.tempo == "blocked"

    @property
    def is_done(self) -> bool:
        return self.state == "done"

    @property
    def label(self) -> str:
        """Best available human-readable name."""
        if self.name:
            return self.name
        if self.intent:
            return self.intent[:40].replace("\n", " ")
        return self.short_id


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse(short_id: str, data: dict[str, Any]) -> Session:
    children = data.get("children")
    return Session(
        short_id=short_id,
        state=_str_or_none(data.get("state")),
        tempo=_str_or_none(data.get("tempo")),
        needs=_str_or_none(data.get("needs")),
        detail=_str_or_none(data.get("detail")),
        suggested_reply=_str_or_none(data.get("suggestedReply")),
        name=_str_or_none(data.get("name")),
        intent=_str_or_none(data.get("intent")),
        cwd=_str_or_none(data.get("cwd")),
        tokens=data.get("tokens") if isinstance(data.get("tokens"), int) else None,
        session_id=_str_or_none(data.get("sessionId")),
        link_scan_path=_str_or_none(data.get("linkScanPath")),
        updated_at=_str_or_none(data.get("updatedAt")),
        children=tuple(c for c in children if isinstance(c, dict)) if isinstance(children, list) else (),
    )


def read_sessions(root: Path | None = None, *, cwd: str | None = None) -> list[Session]:
    """Every readable background session under *root* (default: real jobs dir).

    Unreadable or malformed entries are skipped, never raised: one corrupt
    file must not take out the whole listing. Pass *cwd* to keep only sessions
    started under that directory.
    """
    base = jobs_dir() if root is None else root
    if not base.is_dir():
        return []

    out: list[Session] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue  # pins.json and friends
        try:
            data = json.loads((entry / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        session = _parse(entry.name, data)
        if cwd is not None and session.cwd != cwd:
            continue
        out.append(session)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_jobs.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/sessions/ tests/unit/test_sessions_jobs.py
git commit -m "feat(sessions): read Claude Code background-session state

Parses ~/.claude/jobs/*/state.json into Session records. Read-only: the
state belongs to Claude Code, and any missing or malformed field degrades
rather than raising.

is_blocked reads tempo, not state. A session waiting on a human sits at
state=working, tempo=blocked, so a state-only reader would file it under
working and miss every session the queue exists to surface.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 2: Queue render

**Files:**
- Create: `src/mnemo/core/sessions/render.py`
- Test: `tests/unit/test_sessions_render.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sessions_render.py`:

```python
"""Rendering the session queue.

The ordering is the message: blocked first, oldest first. The maintainer
reads line one and knows where to go.
"""
from __future__ import annotations

from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue


def _blocked(short_id: str, needs: str, *, updated_at: str = "2026-09-12T12:00:00.000Z", **kw) -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs=needs, updated_at=updated_at, **kw)


def test_empty_says_so() -> None:
    assert "nenhuma sessão em background" in render_queue([])


def test_blocked_section_comes_first() -> None:
    out = render_queue([
        Session(short_id="w0", state="working", tempo="active", name="trabalhando"),
        _blocked("b0", "responde?", name="bloqueada"),
    ])

    assert out.index("TE ESPERANDO") < out.index("TRABALHANDO")
    assert out.index("bloqueada") < out.index("trabalhando")


def test_oldest_blocked_first() -> None:
    out = render_queue([
        _blocked("new", "recente", name="recente", updated_at="2026-09-12T12:30:00.000Z"),
        _blocked("old", "antiga", name="antiga", updated_at="2026-09-12T12:00:00.000Z"),
    ])

    assert out.index("antiga") < out.index("recente")


def test_needs_is_shown_and_falls_back_to_detail() -> None:
    out = render_queue([
        _blocked("a", "a pergunta", name="com-needs"),
        Session(short_id="b", state="working", tempo="blocked",
                detail="o detalhe", name="sem-needs"),
    ])

    assert "a pergunta" in out
    assert "o detalhe" in out


def test_suggested_reply_renders_when_present() -> None:
    out = render_queue([_blocked("a", "qual idioma?", name="x", suggested_reply="português")])

    assert "sugerido" in out
    assert "português" in out


def test_done_section_lists_pull_requests() -> None:
    out = render_queue([Session(
        short_id="e51f", state="done", tempo="idle", name="entregue",
        children=({"id": "307", "kind": "pr"}, {"id": "308", "kind": "pr"}),
    )])

    assert "PRONTAS" in out
    assert "#307" in out and "#308" in out


def test_attach_hint_names_the_first_blocked_session() -> None:
    out = render_queue([_blocked("a3f1", "q?", name="x")])

    assert "claude attach a3f1" in out


def test_no_attach_hint_when_nothing_is_blocked() -> None:
    out = render_queue([Session(short_id="w0", state="working", tempo="active", name="w")])

    assert "claude attach" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mnemo.core.sessions.render'`

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/sessions/render.py`:

```python
"""Format the session queue for a terminal.

Pure: takes :class:`Session` records, returns a string. No I/O, so the
ordering rules are testable without touching disk.

The ordering is the feature. Blocked first, oldest first — the maintainer
reads the first line and knows which session to attach to. Everything else
is context.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mnemo.core.sessions.jobs import Session

EMPTY = "  nenhuma sessão em background"


def _age(updated_at: str | None, *, now: datetime | None = None) -> str:
    """Human age of the last update, or '' when unknown."""
    if not updated_at:
        return ""
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    delta = (now or datetime.now(timezone.utc)) - ts
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "agora"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h"


def _sort_key(s: Session) -> str:
    """Oldest update first; sessions without a timestamp sort last."""
    return s.updated_at or "9999"


def _tokens(s: Session) -> str:
    if s.tokens is None:
        return ""
    return f"{s.tokens // 1000}k" if s.tokens >= 1000 else str(s.tokens)


def _prs(s: Session) -> str:
    ids = [f"#{c.get('id')}" for c in s.children if c.get("kind") == "pr" and c.get("id")]
    return ", ".join(ids)


def render_queue(sessions: list[Session]) -> str:
    """Render the whole queue, blocked first."""
    if not sessions:
        return EMPTY

    blocked = sorted((s for s in sessions if s.is_blocked), key=_sort_key)
    done = [s for s in sessions if s.is_done and not s.is_blocked]
    working = [s for s in sessions if not s.is_blocked and not s.is_done]

    lines: list[str] = []

    if blocked:
        lines.append(f"TE ESPERANDO ({len(blocked)})")
        for s in blocked:
            age = _age(s.updated_at)
            lines.append(f"  {s.short_id}  {s.label:<22} {age:>5}  {s.needs or s.detail or '—'}")
            if s.suggested_reply:
                lines.append(f"        ↳ sugerido: \"{s.suggested_reply}\"")
        lines.append("")

    if working:
        lines.append(f"TRABALHANDO ({len(working)})")
        for s in working:
            lines.append(f"  {s.short_id}  {s.label:<22} {s.detail or '—':<34}{_tokens(s):>6}")
        lines.append("")

    if done:
        lines.append(f"PRONTAS ({len(done)})")
        for s in done:
            lines.append(f"  {s.short_id}  {s.label:<22} {_prs(s) or s.detail or '—':<34}{_tokens(s):>6}")
        lines.append("")

    if blocked:
        lines.append(f"  attach: claude attach {blocked[0].short_id}")

    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_render.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/sessions/render.py tests/unit/test_sessions_render.py
git commit -m "feat(sessions): render the queue blocked-first

Pure formatting over Session records. Blocked sessions sort first and
oldest-first, so the first line answers 'which one needs me now' — the
question that replaces polling six sessions by hand.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 3: `mnemo sessions` command

**Files:**
- Create: `src/mnemo/cli/commands/sessions.py`
- Modify: `src/mnemo/cli/parser.py`
- Modify: `src/mnemo/cli/commands/__init__.py`

- [ ] **Step 1: Write the command**

Create `src/mnemo/cli/commands/sessions.py`:

```python
"""``mnemo sessions`` — the live queue of Claude Code background sessions.

Six sessions in parallel cost six streams of attention unless something says
which ones are actually waiting. This prints that, blocked first.

Deliberately human-only: no hook and no MCP tool exposes it. The queue must
never spend a token of the parent session's context — that context is the
scarce resource the whole feature exists to protect.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("sessions")
def cmd_sessions(args: argparse.Namespace) -> int:
    """Print background sessions, blocked first."""
    import json as _json
    import os
    import time
    from dataclasses import asdict

    from mnemo.core.sessions.jobs import read_sessions
    from mnemo.core.sessions.render import render_queue

    scope = None if getattr(args, "all", False) else os.getcwd()

    def _read():
        return read_sessions(cwd=scope)

    if bool(getattr(args, "json", False)):
        print(_json.dumps([asdict(s) for s in _read()], indent=2, ensure_ascii=False))
        return 0

    if bool(getattr(args, "watch", False)):
        try:
            while True:
                print("\033[2J\033[H", end="")  # clear + home
                print(render_queue(_read()))
                time.sleep(2)
        except KeyboardInterrupt:
            return 0

    print(render_queue(_read()))
    return 0
```

- [ ] **Step 2: Register the subparser**

In `src/mnemo/cli/parser.py`, insert immediately **before** the
`sub.add_parser("doctor", ...)` line (currently `parser.py:76`), i.e. right
after the top-level `status` parser's `add_argument` block closes.

Beware: `add_parser("status")` appears twice in this file — the second is
`autosub.add_parser("status", ...)` at line 85, an autopilot subcommand. Anchor
on the `doctor` line, not on `status`.

```python
    sessions = sub.add_parser("sessions", help="live queue of Claude Code background sessions")
    sessions.add_argument("--json", action="store_true", help="machine-readable listing")
    sessions.add_argument("--watch", action="store_true", help="redraw every 2s until Ctrl-C")
    sessions.add_argument("--all", action="store_true", help="every repo, not just this one")
```

- [ ] **Step 3: Import the module so it registers**

`src/mnemo/cli/commands/__init__.py` imports every command module in one
parenthesised block so the `@command` decorators run at import time. Add
`sessions,` to that block between `regen_graph_edges,` and `statusline,`:

```python
from mnemo.cli.commands import (  # noqa: F401  — trigger @command registration
    ...
    regen_graph_edges,
    sessions,
    statusline,
    status,
    ...
)
```

Do not add a separate `from ... import sessions` line; match the existing block.
Note the existing block ends `statusline, status,` — not strictly alphabetical.
Leave that as it is.

- [ ] **Step 4: Verify the command runs**

Run: `PYTHONPATH=src python3 -m mnemo sessions`
Expected: either the queue, or `nenhuma sessão em background`. No traceback.

Run: `PYTHONPATH=src python3 -m mnemo sessions --json --all`
Expected: a JSON array (`[]` when no sessions exist).

Run: `PYTHONPATH=src python3 -m mnemo help | grep sessions`
Expected: the new command listed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/cli/commands/sessions.py src/mnemo/cli/parser.py src/mnemo/cli/commands/__init__.py
git commit -m "feat(cli): add mnemo sessions

Prints the background-session queue, blocked first, scoped to the current
repo unless --all. --json for scripts, --watch to leave running in a split.

Human-only by design: no hook or MCP tool exposes the queue, so it costs
zero context tokens in the parent session.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 4: Statusline segment

**Files:**
- Modify: `src/mnemo/statusline.py`
- Test: `tests/unit/test_sessions_statusline.py` (create)

The statusline composer runs on **every** render under a 2s timeout. So: read `state.json` only, never `timeline.jsonl`, never run the detector, and degrade to nothing on any error.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sessions_statusline.py`:

```python
"""The blocked-session count in the statusline.

Latency-critical: the composer runs on every render under a 2s timeout, so
this path reads state.json and nothing else. Any failure degrades to an
empty string rather than slowing or breaking the line.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo import statusline


def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def test_empty_when_no_sessions(tmp_path: Path) -> None:
    assert statusline._blocked_segment(tmp_path) == ""


def test_empty_when_dir_missing(tmp_path: Path) -> None:
    assert statusline._blocked_segment(tmp_path / "nope") == ""


def test_empty_when_nothing_is_blocked(tmp_path: Path) -> None:
    _job(tmp_path, "w0", state="working", tempo="active")

    assert statusline._blocked_segment(tmp_path) == ""


def test_counts_blocked_sessions(tmp_path: Path) -> None:
    _job(tmp_path, "a", state="working", tempo="blocked", needs="q?")
    _job(tmp_path, "b", state="working", tempo="blocked", needs="q?")
    _job(tmp_path, "c", state="working", tempo="active")

    assert statusline._blocked_segment(tmp_path) == "2 esperando"


def test_singular_wording(tmp_path: Path) -> None:
    _job(tmp_path, "a", state="working", tempo="blocked", needs="q?")

    assert statusline._blocked_segment(tmp_path) == "1 esperando"


def test_malformed_state_does_not_break_the_line(tmp_path: Path) -> None:
    _job(tmp_path, "ok", state="working", tempo="blocked", needs="q?")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")

    assert statusline._blocked_segment(tmp_path) == "1 esperando"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_statusline.py -v`
Expected: FAIL — `AttributeError: module 'mnemo.statusline' has no attribute '_blocked_segment'`

- [ ] **Step 3: Add the helper**

In `src/mnemo/statusline.py`, after `_count_today_denials`, add:

```python
def _blocked_segment(jobs_root: Path | None = None) -> str:
    """``N esperando`` when background sessions are waiting, else ''.

    Latency-critical: the composer runs this on every render under a 2s
    timeout, so it reads ``state.json`` only — never ``timeline.jsonl``, and
    never the unblock detector. Any error degrades to an empty segment: a
    missing count is cheap, a slow or broken status line is not.
    """
    try:
        from mnemo.core.sessions.jobs import read_sessions

        n = sum(1 for s in read_sessions(jobs_root) if s.is_blocked)
    except Exception:
        return ""
    return f"{n} esperando" if n else ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_statusline.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Wire it into `render`**

`render` builds a `parts` list and joins it with `SEPARATOR`. It currently ends:

```python
    activation = _activation_segments(vault_root, cwd)
    parts.extend(activation)

    return SEPARATOR.join(parts)
```

Insert the blocked count between those two, so it renders last:

```python
    activation = _activation_segments(vault_root, cwd)
    parts.extend(activation)

    blocked = _blocked_segment()
    if blocked:
        parts.append(blocked)

    return SEPARATOR.join(parts)
```

`_blocked_segment` already swallows its own errors, so no `try` is needed here.
Do not restructure the function.

- [ ] **Step 6: Verify the line still renders**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/ -k statusline -v`
Expected: PASS — existing statusline tests plus the six new ones.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/statusline.py tests/unit/test_sessions_statusline.py
git commit -m "feat(statusline): show how many sessions are waiting

Appends 'N esperando' when background sessions sit at tempo=blocked. This
is the highest-value surface per token spent: the signal finds the
maintainer without them running a command.

Reads state.json only and swallows every error — the composer runs on each
render under a 2s timeout, so a missing count is cheap and a slow line is
not.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 5: Unblock detector

**Files:**
- Create: `src/mnemo/core/sessions/detector.py`
- Test: `tests/unit/test_sessions_detector.py`

Records `tempo: blocked → active` edges. `timeline.jsonl` cannot be used — it carries `state` transitions only and never mentions `tempo` (measured 2026-09-12). So the detector compares the current `tempo` against the `lastTempo` it stored.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sessions_detector.py`:

```python
"""Detecting the moment a blocked session gets its answer.

An answer to a blocked session is the highest-signal correction there is:
the maintainer is only consulted when it matters. The detector records where
that answer is written so extraction can treat it as such.

It cannot read timeline.jsonl — that file carries ``state`` transitions and
never mentions ``tempo`` (measured 2026-09-12). So it keeps ``lastTempo``
itself and compares.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.sessions import detector
from mnemo.core.sessions.jobs import Session


def _blocked(short_id: str = "a3f1") -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs="answer: bcrypt ou argon2?", session_id="sid-1",
                   link_scan_path="/transcripts/sid-1.jsonl", cwd="/repo")


def _active(short_id: str = "a3f1") -> Session:
    return Session(short_id=short_id, state="working", tempo="active",
                   session_id="sid-1", link_scan_path="/transcripts/sid-1.jsonl",
                   cwd="/repo")


def _state(vault: Path) -> dict:
    return json.loads((vault / ".mnemo" / "session-queue.json").read_text(encoding="utf-8"))


def test_first_sighting_records_no_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["lastTempo"] == "blocked"
    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_blocked_to_active_records_an_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    (unblock,) = _state(tmp_path)["seen"]["a3f1"]["unblocks"]
    assert unblock["needs"] == "answer: bcrypt ou argon2?"
    assert unblock["linkScanPath"] == "/transcripts/sid-1.jsonl"
    assert unblock["sessionId"] == "sid-1"
    assert unblock["extracted"] is False
    assert unblock["at"]


def test_staying_blocked_records_nothing(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_active_to_blocked_records_nothing(tmp_path: Path) -> None:
    detector.sweep([_active()], vault_root=tmp_path)
    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["unblocks"] == []


def test_repeated_sweeps_do_not_duplicate_an_unblock(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    assert len(_state(tmp_path)["seen"]["a3f1"]["unblocks"]) == 1


def test_tracks_sessions_independently(tmp_path: Path) -> None:
    detector.sweep([_blocked("aaa"), _blocked("bbb")], vault_root=tmp_path)
    detector.sweep([_active("aaa"), _blocked("bbb")], vault_root=tmp_path)

    seen = _state(tmp_path)["seen"]
    assert len(seen["aaa"]["unblocks"]) == 1
    assert seen["bbb"]["unblocks"] == []


def test_corrupt_state_file_is_replaced_not_fatal(tmp_path: Path) -> None:
    (tmp_path / ".mnemo").mkdir(parents=True)
    (tmp_path / ".mnemo" / "session-queue.json").write_text("{not json", encoding="utf-8")

    detector.sweep([_blocked()], vault_root=tmp_path)

    assert _state(tmp_path)["seen"]["a3f1"]["lastTempo"] == "blocked"


def test_pending_unblocks_lists_unextracted_only(tmp_path: Path) -> None:
    detector.sweep([_blocked()], vault_root=tmp_path)
    detector.sweep([_active()], vault_root=tmp_path)

    pending = detector.pending_unblocks(vault_root=tmp_path)

    assert len(pending) == 1
    assert pending[0]["needs"] == "answer: bcrypt ou argon2?"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mnemo.core.sessions.detector'`

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/sessions/detector.py`:

```python
"""Record the moment a blocked session gets answered.

Answering a blocked session is the highest-signal correction the maintainer
produces: they are only consulted when it matters. The detector notices the
``tempo: blocked -> active`` edge and writes down where the answer lives, so
extraction can treat that transcript region as high signal. It extracts
nothing itself.

It cannot use ``timeline.jsonl``: that file records ``state`` transitions and
never mentions ``tempo`` (measured on a real dispatch, 2026-09-12). So this
module keeps ``lastTempo`` per session and compares against the current value.

No daemon. The sweep rides triggers that already exist — every ``mnemo
sessions`` invocation and the ``session_end`` hook — mirroring how autopilot
schedules itself. Worst case a sweep is late, never lost: the edge is still
visible on the next one as long as ``tempo`` has not flipped back.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mnemo.core.sessions.jobs import Session

STATE_FILENAME = "session-queue.json"


def _state_path(vault_root: Path) -> Path:
    return vault_root / ".mnemo" / STATE_FILENAME


def _load(vault_root: Path) -> dict[str, Any]:
    """Stored state, or a fresh one. A corrupt file is replaced, not fatal.

    This file is disposable: losing it costs at most some un-extracted
    markers, never a rule or a proposal.
    """
    try:
        data = json.loads(_state_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": {}}
    if not isinstance(data, dict) or not isinstance(data.get("seen"), dict):
        return {"seen": {}}
    return data


def _save(vault_root: Path, data: dict[str, Any]) -> None:
    path = _state_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def sweep(sessions: list[Session], *, vault_root: Path) -> int:
    """Record any ``blocked -> active`` edge. Returns how many were recorded."""
    data = _load(vault_root)
    seen = data["seen"]
    recorded = 0

    for s in sessions:
        entry = seen.setdefault(s.short_id, {"lastTempo": None, "unblocks": []})
        previous = entry.get("lastTempo")
        if previous == "blocked" and s.tempo == "active":
            entry["unblocks"].append({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "needs": entry.get("lastNeeds"),
                "sessionId": s.session_id,
                "linkScanPath": s.link_scan_path,
                "cwd": s.cwd,
                "extracted": False,
            })
            recorded += 1
        entry["lastTempo"] = s.tempo
        if s.is_blocked and s.needs:
            entry["lastNeeds"] = s.needs

    _save(vault_root, data)
    return recorded


def pending_unblocks(*, vault_root: Path) -> list[dict[str, Any]]:
    """Every recorded unblock that extraction has not consumed yet."""
    out: list[dict[str, Any]] = []
    for entry in _load(vault_root)["seen"].values():
        out.extend(u for u in entry.get("unblocks", []) if not u.get("extracted"))
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_detector.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/sessions/detector.py tests/unit/test_sessions_detector.py
git commit -m "feat(sessions): record when a blocked session gets answered

Watches for the tempo blocked->active edge and writes down the needs, the
transcript path and the repo, so extraction can treat that answer as the
high-signal correction it is.

Cannot use timeline.jsonl: that file records state transitions and never
mentions tempo (measured 2026-09-12). The detector keeps lastTempo itself.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 6: Run the detector from the command

**Files:**
- Modify: `src/mnemo/cli/commands/sessions.py`

- [ ] **Step 1: Call the sweep on each read**

In `src/mnemo/cli/commands/sessions.py`, replace the `_read` helper with:

```python
    def _read():
        found = read_sessions(cwd=scope)
        try:
            from mnemo import cli
            from mnemo.core.sessions import detector

            detector.sweep(found, vault_root=cli._resolve_vault())
        except Exception:
            pass  # the queue must print even when the vault is unavailable
        return found
```

The sweep rides the command, as the spec's trigger list requires. The broad
`except` is deliberate: a queue that refuses to print because the vault moved
would defeat the feature.

- [ ] **Step 2: Verify the command still runs and writes state**

Run: `PYTHONPATH=src python3 -m mnemo sessions`
Expected: the queue prints, no traceback.

Run: `ls ~/mnemo/.mnemo/session-queue.json`
Expected: the file exists (created on the first run).

- [ ] **Step 3: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/ -q`
Expected: everything passes; no regressions.

- [ ] **Step 4: Commit**

```bash
git add src/mnemo/cli/commands/sessions.py
git commit -m "feat(sessions): sweep for unblocks whenever the queue is read

Rides the existing trigger instead of holding a daemon. Vault failures are
swallowed: the queue must print even when the vault is unavailable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 7: Doctor check

**Files:**
- Modify: `src/mnemo/cli/commands/doctor_checks/misc.py`
- Modify: `src/mnemo/cli/commands/doctor.py`
- Test: `tests/unit/test_doctor_background_sessions.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_doctor_background_sessions.py`:

```python
"""``mnemo doctor`` reports on background sessions.

The check exists because a packaging or schema break in this path is
invisible otherwise — the queue would simply print nothing and look
like "no sessions". Doctor exercises the read for real.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.cli.commands.doctor_checks import misc as doctor_misc


def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def test_quiet_when_no_background_sessions(tmp_path: Path, capsys) -> None:
    assert doctor_misc._doctor_check_background_sessions(tmp_path) is True
    assert "no background sessions" in capsys.readouterr().out


def test_reports_counts(tmp_path: Path, capsys) -> None:
    _job(tmp_path, "a", state="working", tempo="blocked", needs="q?")
    _job(tmp_path, "b", state="working", tempo="active")
    _job(tmp_path, "c", state="done", tempo="idle")

    assert doctor_misc._doctor_check_background_sessions(tmp_path) is True

    out = capsys.readouterr().out
    assert "3 background session" in out
    assert "1 waiting" in out


def test_unreadable_state_is_reported(tmp_path: Path, capsys) -> None:
    _job(tmp_path, "ok", state="working", tempo="active")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "state.json").write_text("{not json", encoding="utf-8")

    assert doctor_misc._doctor_check_background_sessions(tmp_path) is True
    assert "1 unreadable" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_doctor_background_sessions.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_doctor_check_background_sessions'`

- [ ] **Step 3: Write the check**

Append to `src/mnemo/cli/commands/doctor_checks/misc.py`:

```python
def _doctor_check_background_sessions(jobs_root: Path | None = None) -> bool:
    """Report Claude Code background sessions and whether we can read them.

    Advisory, never a failure: these sessions belong to Claude Code and their
    state is none of mnemo's business to repair. The check exists because a
    schema or packaging break in this path is otherwise invisible — the queue
    would print "no sessions" and look healthy. Counting unreadable entries
    makes that visible the way piping the CLI made the Windows crash visible.
    """
    from mnemo.core.sessions.jobs import jobs_dir, read_sessions

    base = jobs_dir() if jobs_root is None else jobs_root
    if not base.is_dir():
        print("  ✓ no background sessions")
        return True

    dirs = [d for d in base.iterdir() if d.is_dir()]
    sessions = read_sessions(base)
    unreadable = len(dirs) - len(sessions)

    if not dirs:
        print("  ✓ no background sessions")
        return True

    blocked = sum(1 for s in sessions if s.is_blocked)
    word = "session" if len(dirs) == 1 else "sessions"
    suffix = f", {unreadable} unreadable" if unreadable else ""
    print(f"  ✓ {len(dirs)} background {word} ({blocked} waiting{suffix})")
    return True
```

Confirm `Path` is already imported at the top of `misc.py`; add `from pathlib import Path` if it is not.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_doctor_background_sessions.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Register the check**

In `src/mnemo/cli/commands/doctor.py`, in the ordered check list, add after the `("legacy_wiki_dirs", ...)` line:

```python
    ("background_sessions", doctor_misc._doctor_check_background_sessions),
```

- [ ] **Step 6: Verify doctor runs**

Run: `PYTHONPATH=src python3 -m mnemo doctor 2>&1 | grep -i background`
Expected: a line reporting the background-session count.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/doctor_checks/misc.py src/mnemo/cli/commands/doctor.py tests/unit/test_doctor_background_sessions.py
git commit -m "feat(doctor): report background sessions and unreadable state

Advisory only — these sessions belong to Claude Code. The check exists so a
schema or packaging break is visible: without it the queue would print 'no
sessions' and look healthy.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

### Task 8: Changelog and full verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry**

Under the unreleased heading in `CHANGELOG.md`, matching the surrounding style:

```markdown
- `mnemo sessions` — a blocked-first queue of Claude Code background sessions,
  so several parallel sessions cost one stream of attention. `--json` for
  scripts, `--watch` to leave running, `--all` for every repo. The statusline
  gains `N esperando`. Read-only over `~/.claude/jobs/`; nothing is exposed to
  model context.
```

- [ ] **Step 2: Run the full suite**

Run: `PYTHONPATH=src python3 -m pytest tests/ -q`
Expected: all pass. Baseline before this work was 2579 passed, 2 skipped, 8 deselected; expect roughly 34 more passing.

- [ ] **Step 3: Verify no test spawned a session**

Run: `ls ~/.claude/jobs/`
Expected: unchanged from before the run — `331745e2`, `e51f48ec`, `pins.json`. If a new directory appeared, a test spawned a real session: find it and fix it before continuing.

- [ ] **Step 4: Exercise the real command end to end**

Run: `PYTHONPATH=src python3 -m mnemo sessions --all`
Expected: the two existing jobs render without a traceback. Both are stale (`updatedAt` in August) and will appear with hour-scale ages.

Run: `PYTHONPATH=src python3 -m mnemo doctor 2>&1 | tail -20`
Expected: no new warnings beyond the background-session line.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): mnemo sessions

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TgWwdbm1eKmeYhJCVeET9"
```

---

## Out of scope for this plan

Named so nobody builds them by accident. All are in the spec's roadmap:

- **Scoped proposals and promotion by recurrence.** Task 5 stores the markers; consuming them in the extraction pipeline is the next plan, and it depends on an open question the spec flags: whether an unblock actually yields an extractable correction in mnemo's pipeline. **Verify that before planning it.**
- **The disagreement row** in the queue. It depends on proposals existing.
- **Rich dashboard.** Examine `claude agents` in a TTY first, and design against what it does *not* show.
- **Dispatch via MCP.** The parent session can already run `claude --bg` through Bash.
- **Live cross-session memory** and **conflict coordination** between siblings.

## Verification checklist

- [ ] `mnemo sessions` prints blocked sessions first, oldest first
- [ ] a session at `state=working, tempo=blocked` lands under TE ESPERANDO (the regression guard for the whole feature)
- [ ] a malformed `state.json` never breaks a command
- [ ] the statusline shows `N esperando` and degrades to nothing on error
- [ ] no hook and no MCP tool exposes the queue (`grep -rn "read_sessions\|render_queue" src/mnemo/hooks/ src/mnemo/core/mcp/` returns nothing)
- [ ] mnemo never writes to `~/.claude/jobs/` (`grep -rn "jobs_dir" src/mnemo/ | grep -i "write\|mkdir\|unlink"` returns nothing)
- [ ] no test spawns a real background session
