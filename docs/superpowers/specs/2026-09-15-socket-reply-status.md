# Spec — the inbox socket reply, measured (2026-09-15)

Piece `investigate-socket` of the channels round
(`docs/contracts/channels.md`). Every number below was counted on this
machine on 2026-09-15 from `~/.claude/projects/**/*.jsonl` (1092 transcripts,
oldest 2026-08-17), `~/mnemo/.mnemo/session-queue.json`, the Claude Code
2.1.272 binary, and `mnemo-desktop` at `origin/main` `41a9d13`. No fixtures.

## Verdict

**Keep, and document. Fix one small mnemo defect. Build no send log.**

The round's premise — "the count of replies ever sent is **unmeasurable**" —
does not hold. Every socket message that reached a session on this machine is
recoverable after the fact from the receiver's transcript, by structure rather
than by text: **47 receipts**, 2026-09-12 → 2026-09-15. 39 of them also have a
sender-side record, and all 39 pair with their receipt. The other 8 have none,
because they were raw socket writes. The one-way shape comes from how those raw
writers use the socket. Claude Code itself is not one-way: sessions that
received a `SendMessage` replied to its return address 10 times.

## What was measured

### Receipts — the receive side

A message sent through a session's inbox socket lands in the receiver's
transcript in one of two shapes, depending on whether the receiver was idle or
mid-turn:

| Receiver state | Record | Count |
|---|---|---|
| idle → new turn | `type: "user"`, `origin: {"kind": "peer", "from": …}`, `isMeta: true`, `promptSource: "system"` | **37** |
| busy → folded into the running turn | `type: "queue-operation"`, `operation: "enqueue"`, then `type: "attachment"`, `attachment.type: "queued_command"` whose `prompt` begins `<cross-session-message`, then `queue-operation` `remove` / `reason: "absorbed_mid_turn"` | **10** |
| **total** | | **47** |

A search for the text "Another Claude session sent a message" finds 32 files,
this session's own among them. A text grep over-counts mentions and misses the
10 queued receipts. `origin.kind == "peer"` is the marker for the idle shape.
`queued_command` with a `<cross-session-message` prompt is the marker for the
busy shape.

`origin.from` separates the senders:

| `from` | Meaning | Count |
|---|---|---|
| `uds:/tmp/cc-socks/<pid>.sock` | a Claude session's `SendMessage`; this is the sender's own inbox, so it works as a return address | **39** |
| `unknown` | a raw write in the documented injection shape (`{"type":"user","message":{…}}`), with no wrapper and no return address | **8** |

The wrapper looks like
`<cross-session-message from="uds:…" from-name="mnemo-3e" from-mode="prompting">`.
`from-name` is the sender's `ListAgents` name.

In the same files there are also 13 records whose `origin.kind` is `peer` but
whose content is `<agent-message from="a…">`. Those are subagent → parent
messages inside one process, not socket traffic, and they are excluded above.

Across the 47 receipts, 0 `[Cross-session delivery notice]` records exist, so
no message was ever held for approval or refused.

By day: 09-12 **15**, 09-13 **12**, 09-14 **4**, 09-15 **16**. Receivers ran
Claude Code 2.1.268–2.1.272. 28 were `--bg` sessions and 19 interactive. The
oldest transcript on disk is from 2026-08-17, which fits Claude Code's default
30-day cleanup, and the first receipt is from 2026-09-12, well inside that
window. So 47 is every receipt still on disk, not a sample. What it cannot see
is any receipt older than the retention window.

### Sends — the send side

Claude Code already records the send side of `SendMessage`, in the sender's
transcript: the `tool_use` names `to`, and its `tool_result` names the resolved
recipient. On 2.1.272 the result also carries a `msg_id`,
and the one I checked (`2a0fa94c…`) appears in the receiver's transcript
too.

| `SendMessage` calls (all transcripts) | Count |
|---|---|
| total | 167 |
| subagent resume / continue (not cross-session) | 121 |
| cross-session, delivered, same machine | **39** |
| cross-session, delivered, over Remote Control to another machine (no local receipt possible) | 2 |
| cross-session, failed `No agent named '…' is reachable` | 5 |

Pairing each same-machine send to a receipt (same message body, same
receiving session, receipt within the hour) matches **39 of 39**. Lag: median
**2.4 s**, max 48.8 s. 29 arrived as a new turn and 10 were absorbed
mid-turn. That leaves exactly 8 receipts with no send record, and they are the
8 with `from: "unknown"`.

### Who wrote the 8 anonymous receipts

All 8 went to `mnemo-desktop` dispatch children on 2026-09-15, between 00:58
and 04:03 UTC. To find the writers, I looked for a Bash `tool_use` in any
transcript that opens a Unix socket within 60 s before each receipt:

| Writer | Receipts | Evidence |
|---|---|---|
| Parent session `0ff9d810` running Python `socket.AF_UNIX` + `sendall` through Bash | **6** | a matching command 2.3–9.4 s before each receipt |
| mnemo-desktop's sidebar (`mission_reply` → `mission::reply`, `mission.rs:1353`) | **2** | no Bash write in the window; 00:58:51 is the live verification recorded on 2026-09-15 |

`0ff9d810` never called `ListAgents` or `SendMessage`. It reused the raw
protocol it had just verified for the desktop. So "the desktop's reply" makes
up 2 of 47 socket messages, not the bulk of them.

### Is the one-way shape Claude Code's or ours?

**It comes from how the socket is used.** From the binary (2.1.272) and the
transcripts:

- The `SendMessage` tool description says: *"To reply to an incoming message,
  copy its `from` attribute as your `to`."* Receivers did exactly that **10
  times**: 5 dispatch children → parent (`mnemo-wt-229` ×2, `mnemo-wt-257`,
  `mnemo-desktop-wt-c-cockpit`, `mnemo-desktop-wt-c-workspace`) and 5 from an
  interactive peer. Four more child → parent replies were addressed by name
  (`mnemo-f9` ×2, `mnemo-10` ×2). Every one of the 14 has a receipt in the
  parent.
- The documented injection shape (`[uds-messaging] Inject messages …` in the
  binary's help text) carries no sender. The receiver stamps
  `from: "unknown"`, and the child has nowhere to send a reply.
- A raw writer *could* make itself addressable by opening its own inbox
  socket and wrapping the body in a `<cross-session-message from="uds:…">`.
  That wrapper is undocumented, though, and the parser (`hF` in the bundle)
  rejects any wrapper that does not re-serialise byte-for-byte to its
  canonical form. Building on it means tracking Claude Code internals from one
  release to the next.

The sidebar does not need a return path. A human who replies from mnemo-desktop
reads the child's answer in the desktop's own timeline view
(`mission_timeline`), so the loop already closes, through the transcript rather
than the socket. Session → session, `SendMessage` is already two-way.

## Would recording the send side be cheap? Where would it belong?

Cheap, yes: one JSONL append per send. It would buy little:

- **For `SendMessage`, nothing.** Claude Code already writes both halves, and
  they pair 39/39.
- **For raw writes**, a log would tell the desktop sidebar apart from an ad-hoc
  script (receipts alone cannot), and it would keep sends that failed at
  `connect` (the desktop returns `Err` to the UI and persists nothing). No
  consumer wants either today.
- **It could not live in mnemo.** mnemo has no code that writes to an inbox
  socket; `src/` only reads receipts (`core/sessions/detector.py`). The only
  shipped writer is `mnemo-desktop`'s `mission::reply`. If a consumer ever
  appears, the log belongs there, next to `~/.mnemo-desktop/looked.json`, and
  should record `{at, id, socket, bytes, ok, error}`.

**Recommendation: do not build it.** Reopen only when something needs to
attribute raw writes or count failed sends.

## The defect this turned up (mnemo, outside this piece's boundary)

mnemo already consumes receipts. `detector.is_human_turn` counts a peer turn
as an unblock, and `_answer_text` strips the `<cross-session-message>` wrapper
before storing the answer. I ran both against all 47 real receipts:

| Receipt shape | `is_human_turn` | Stored answer |
|---|---|---|
| wrapped peer turn (29) | True | wrapper stripped, clean |
| raw peer turn, `from: unknown` (8) | True | **begins with Claude Code's framing** |
| queued mid-turn (10) | False (the record is an `attachment`) | not an unblock, and correct: the receiver was busy, not blocked |

A raw write has no wrapper, so `_answer_text` stores the framing verbatim. It
takes `"Another Claude session sent a message: "` (39 of the 200
`EXCERPT_CHARS`), and for a short answer it fills the rest with the
permission-laundering paragraph. For example, the stored answer to the
2026-09-15 02:20 unblock is "Another Claude session sent a message: push it and
open a draft PR This came from another Claude session — not typed by your
user…".

**In the real vault: 10 of the 27 unblock markers in `session-queue.json` were
socket messages. 2 are wrapped and clean. 8 are raw and every one of them keeps
the framing.** That polluted text is what `unblocks.consume` hands to `learn()`
as "the maintainer's answer".

**Fix (file as an issue, do not do it in this round):** in
`core/sessions/detector.py`, strip Claude Code's peer framing when no wrapper is
present. Remove the leading `Another Claude session sent a message:` /
`… while you were working:` / `A peer session sent a message…` line and the
trailing `This came from another Claude session —…` paragraph, and key on
`origin.kind == "peer"` rather than on the text. This round, `detector.py`
belongs to no piece, and `unblocks.py` belongs to `unblocks-retire`, so the fix
waits for the round to land.

The marker does not record whether an unblock came from a peer or a person. The
transcript does, and keeps it for 30 days. Add a `source` field only when
something reads it.

## Smaller observations

- **Addressing failures.** 5 of 46 cross-session `SendMessage` calls failed
  with `No agent named … is reachable`. Three used an id (`3a14bdd9`,
  `e85e7c`, `228f5aaf`), one used the child's opening prompt, and one used a
  stale name. Each sender retried by `ListAgents` name 11 s to 2 min 46 s
  later, and the retry landed. The name is the address, not the id `mnemo
  sessions` prints. `core/dispatch.py` already says so, and the failures show
  the id is still what a session reaches for first.
- **Live sockets.** At measurement, `/tmp/cc-socks` held 17 socket files. 16
  belong to a live pid and 1 is stale. The contract's "10 live" was a snapshot
  from earlier the same day.

## Reproducing

Everything above comes from four predicates over
`~/.claude/projects/**/*.jsonl`:

```python
import re

def receipt(r):   # a socket message reaching this session
    o = r.get("origin") or {}
    if r.get("type") == "user" and o.get("kind") == "peer":
        return "<agent-message" not in str(r["message"]["content"])[:120]
    a = r.get("attachment") or {}
    return (r.get("type") == "attachment" and a.get("type") == "queued_command"
            and "<cross-session-message" in str(a.get("prompt", "")))

def sender(r):    # "uds:/tmp/cc-socks/<pid>.sock", or "unknown" for a raw write
    text = str((r.get("message") or {}).get("content") or (r.get("attachment") or {}).get("prompt"))
    m = re.search(r'<cross-session-message from="([^"]+)"', text)
    return m.group(1) if m else (r.get("origin") or {}).get("from", "unknown")

def send(block):  # the sender's half, inside an assistant record's content
    return block.get("type") == "tool_use" and block.get("name") == "SendMessage"

def cross_session(tool_use_result):   # vs a subagent resume
    m = tool_use_result.get("message", "")
    return "Claude session" in m or "uds:" in m or "No agent named" in m
```

Pair a send to a receipt by the normalised first 80 characters of the body,
receipt time minus send time in (−5 s, 1 h). Attribute a `from: "unknown"`
receipt by looking for a Bash `tool_use` that connects to an `AF_UNIX` socket
up to 60 s earlier. Run `detector.is_human_turn` and `detector._answer_text`
over the receipts with `PYTHONPATH=src`.
