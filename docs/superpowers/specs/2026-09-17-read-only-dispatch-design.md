# Read-only dispatch: a child that investigates and reports, and cannot write

**Date:** 2026-09-17
**Status:** design, awaiting review
**Related:** #357 (nothing wakes the parent when a child finishes)

## The problem

`mnemo dispatch` has one posture: implement. Every child is told to "work on"
an issue, to "run the full test suite before claiming the work is done", and to
ask git whether it has commits to publish (`core/dispatch.py:235-256`,
`:301-313`).

Sometimes the work is not an implementation. An issue needs to be understood
before it can be scoped; a suspicion needs evidence before it becomes a fix; a
question ("is this even a bug?") deserves an answer, not a patch. Today the only
way to get that is to dispatch an implementer and hope it refuses.

Hoping is not idle. The templates already value the refusal —
`_closing_clause`'s docstring (`dispatch.py:324-327`) says a child that refused
"has no commits and publishes nothing, but its reasoning is the most valuable
briefing in the system, because no diff carries it". So the outcome this design
wants is already reachable and already prized. What is missing is the ability to
**ask for it up front** instead of discovering it afterwards.

## What `--may none` is not

`--may none` withholds *publishing*. The child still clones a worktree, writes
code, commits, and runs the suite; it merely stops before `push`. `grants.py:13`
states the axis plainly: "The vocabulary is what a child can publish, and
nothing past it."

Read-only is not a publishing verb. It belongs on a different axis, and this
design keeps it there.

## Goals

1. A maintainer can dispatch a child that **investigates and reports**.
2. The child **cannot** write code — enforced, not requested.
3. The finding lands somewhere durable: a comment on the issue it investigated.
4. Nothing about the existing implement-path changes behaviour.

## Non-goals

- Waking the dispatching session when the analysis lands. That is #357, and it
  is orthogonal: this design's finding is durable in the issue whether or not a
  notification ever exists. When #357 ships, a read-only child benefits from it
  with no change here.
- A general agent-review feature. "Review" in this repo means the maintainer
  reads a diff (`cli/parser.py:177-179`, `grants.py:21-23`), and this design
  does not touch that meaning.
- Restricting Bash. A read-only child needs `gh`, `git log`, `rg`, and the test
  suite to investigate anything. The restriction is on *mutating the working
  tree*, not on running commands.

## Design

### Surface

A flag on `mnemo dispatch`:

```
mnemo dispatch 361 --read-only
```

and the matching per-piece field on a contract:

```
- **read-only:** yes
```

`--read-only` and `--may` are **mutually exclusive**, refused by name at parse
time with a message that says why: a read-only child produces nothing to
publish, so a grant on it is not a tightening or a loosening, it is a
contradiction. This follows the precedent of `merge` in `grants.parse`
(`grants.py:85-89`), which is refused by name rather than silently ignored.

This is the third per-child knob that becomes a *spawn flag* rather than prompt
prose, joining `model` and `effort` (`contracts.py:223,227` →
`dispatch.py:740-743`). Those two are admissible because they are budgets, not
approaches (`contracts.py:215-222`). `read-only` is admissible on the same test:
it says what posture the child holds, never what approach it takes. "Investigate
rather than implement" is a posture; "use a regex" would be an approach and
remains inexpressible.

### The three changes

**1. The prompt.** A second template, `_ANALYSIS_PROMPT`, selected in
`build_prompt` when read-only. It keeps the issue body, the `gh issue view`
instruction, and the worktree sentence. It drops "run the full test suite before
claiming the work is done" and the changelog fragment. It replaces the scope
limits with an investigation brief, and the closing's step 2 (the
commits-ahead check) with "post your finding as a comment on the issue".

The "no approach is prescribed" paragraph stays verbatim: it is the passage that
already licenses a measured refusal, and it reads correctly for an investigator.

**2. The spawn.** `spawn_child` appends `--disallowedTools Edit,Write,NotebookEdit`
to the argv before the positional prompt (`dispatch.py:739-750`). This is the
first tool-level restriction mnemo has ever placed on a child. Everything
existing — `--may`, `may:`, `files:`, `NO_GRANT` — restricts by *wording the
prompt*, and nothing enforces any of it at the runtime.

**3. The closing.** A read-only variant of `_closing_clause` whose step 2 is
"post your finding as a comment on the issue with `gh issue comment`", not the
git commits-ahead check. Steps 1 (write the report) and 3 (stop yourself) are
unchanged — they do not depend on the posture, and a read-only child's briefing
is exactly the artefact `dispatch.py:324-327` calls the most valuable in the
system.

### Worktree

Unchanged: a read-only child gets its own worktree, as every child does. The
module docstring calls one-tree-per-child "mandatory, not advisory"
(`dispatch.py:49-52`), citing three git accidents from tree sharing. The tree
will simply stay clean, which `deliver --review` already reports correctly
(`sessions/delivery.py:200`, `:213`).

Keeping the tree also means the restriction is defence in depth rather than the
only thing standing between a child and the maintainer's working copy: if
`--disallowedTools` turns out not to hold, the damage is confined to a throwaway
tree instead of landing in the repo the maintainer is sitting in.

### Delivery

The finding goes to **a comment on the issue**, via `gh issue comment`, written
by the child itself. Reasons: it lands where the work already lives; it survives
the worktree's removal; it needs no new mnemo surface to read it; and it is
visible to anyone looking at the issue, not only to whoever runs
`mnemo sessions`.

The session briefing remains the second copy, as for every child. A read-only
child that cannot reach `gh` still writes its briefing, so the finding is never
lost outright — it is merely less discoverable.

## The enforcement question — stated honestly

`claude --help` documents `--allowedTools`, `--disallowedTools` and `--tools`.
The design above assumes `--disallowedTools Edit,Write,NotebookEdit` causes the
child to refuse those tool calls.

**This has not been verified in this session.** An attempt to test it live —
spawning `claude --print` with the flag in a throwaway directory and asking it to
write a file — was denied by the auto-mode classifier (`[Create Unsafe Agents]`),
and was not worked around.

The implementation plan must open with that verification, executed rather than
inferred, before any of the three changes are written. The repo's own standing
rule applies: grep is a proxy, run the function.

**If the flag does not hold**, the design degrades to prompt-only: changes 1 and
3 still stand and still deliver the feature, and change 2 is dropped along with
every claim that the restriction is enforced. What must not happen is shipping
change 2 while describing it as a guarantee that was never observed — the repo
has a documented history of exactly that failure
(`verify-subagent-claims-by-running`, `grep-is-a-proxy-run-the-function`).

## Blast radius

`Grant` is threaded as a keyword parameter through every dispatch entry point —
`_spawn_into`, `dispatch_issue`, `dispatch_all`, `dispatch_piece`,
`dispatch_contract`. A read-only flag carried on the same channel touches all
five, plus:

- `core/dispatch.py` — the new template, the `build_prompt` branch, the
  `_closing_clause` variant, three lines of argv
- `core/contracts.py` — `_FIELD_RE` alternation (`:58`), a `Piece` field
  (`:204-235`), the parse branch (`:489-495`), precedence beside `piece_grant`
  (`dispatch.py:916-918`)
- `cli/parser.py` — the argument and its help text
- `cli/commands/dispatch.py` — the mutual-exclusion refusal, dry-run rendering
- `core/sessions/` — whether the queue shows the posture is an open question
  below

Roughly the footprint of `--effort` (#351), which is the closest precedent for
adding a per-child spawn flag end to end.

## Testing

- `parse`-level: `--read-only` with `--may` is refused, by name, with a reason.
- Prompt-level: a read-only prompt contains no "run the full test suite" and no
  changelog fragment; contains the `gh issue comment` instruction; still
  contains the refusal paragraph and all three closing steps.
- Argv-level: `spawn_child` emits `--disallowedTools` before the positional
  prompt, and emits nothing when not read-only (byte-identical argv to today —
  the same invariant `dispatch.py:282` already holds for `--may`).
- Contract-level: `read-only:` parses; an invalid value raises `ContractError`
  before any tree exists, as `may:` does (`contracts.py:490-491`).
- Live: the verification above, run once, recorded in the PR with its output.

## Open questions

1. **Does the queue show the posture?** `sessions/render.py:339-355` renders a
   grants column. A read-only child has no grant, so it renders blank — which
   reads as "no permission" rather than "nothing to permit". Worth a distinct
   marker, but it is a rendering decision that can follow the feature rather
   than gate it.
2. **Does a read-only child need `--may` refused, or silently emptied?** This
   design refuses it. The alternative — accept and ignore — is cheaper but
   teaches the maintainer that flags can be no-ops, which is the habit
   `grants.py:20-24` argues against.
3. **Should `files:` on a read-only piece mean anything?** A piece that names
   files it may change, in a posture where it may change none, is probably a
   contract error worth catching at parse time. Left open: it needs a real
   contract to reason about, and none exists yet.
