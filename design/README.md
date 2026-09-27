# design/

mnemo is built in the open with Claude Code. These are the design notes and
measurement write-ups behind it — not documentation. If you want to use
mnemo, start at [`docs/getting-started.md`](../docs/getting-started.md).

- `specs/` — design documents and measurement write-ups, dated by when they
  were written.
- `contracts/` — dispatch contracts: a feature split into pieces that ran as
  parallel Claude Code sessions.
- `findings/` — one-off investigations.
- `archive/` — superseded backlogs.

A number here is meant to reproduce: the counters live in
[`tools/measure_*.py`](../tools/), each with unit tests over synthetic
transcripts, and a write-up names the script it ran. The oldest notes predate
that rule and may cite numbers no script reproduces. Measurements taken on the
maintainer's private repositories name them by alias (`repo-a`, `repo-b`, …),
the same alias everywhere, so one repo still reads as one repo.

Implementation plans are not kept: they were agent scaffolding with no reader
once the work landed. Git history has them.
