# Read-only dispatch: a child that investigates and reports instead of building

**Date:** 2026-09-17
**Status:** approved; Bash bypass measured 2026-09-17
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
2. The child's **file-editing tools are closed** — measured, not merely asked
   for. See "The enforcement question" below for the exact, deliberately
   narrow claim this supports.
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

**2. The spawn.** `spawn_child` emits `--disallowedTools Edit Write NotebookEdit`
into the argv (`dispatch.py:739-750`). This is the first tool-level restriction
mnemo has ever placed on a child. Everything existing — `--may`, `may:`,
`files:`, `NO_GRANT` — restricts by *wording the prompt*, and nothing constrains
the runtime at all.

The list is space-separated and variadic: it consumes every following token
until a flag, so the prompt must be fenced off with `--`. Ending it by relying
on a later flag is not enough — `--model`, `--effort` and the lean flags are all
optional, and `--full-profile` with no model puts the prompt straight after the
list, where the CLI reads it as a tool name and the child starts with no
instructions at all. Both halves measured 2026-09-17.

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

The tree is **not** belt-and-braces here. Since the measurement below shows a
read-only child can still write through the shell, its own worktree is the only
thing confining such a write to a throwaway directory rather than the checkout
the maintainer is sitting in. Removing it to save a little cleanup would trade
away the containment the posture actually has.

### Delivery

The finding goes to **a comment on the issue**, via `gh issue comment`, written
by the child itself. Reasons: it lands where the work already lives; it survives
the worktree's removal; it needs no new mnemo surface to read it; and it is
visible to anyone looking at the issue, not only to whoever runs
`mnemo sessions`.

The session briefing remains the second copy, as for every child. A read-only
child that cannot reach `gh` still writes its briefing, so the finding is never
lost outright — it is merely less discoverable.

## The enforcement question — what was measured

Measured 2026-09-17 against the real CLI, `claude --print` in a throwaway
directory.

**The file tools are genuinely blocked.** Asked to put a word in a file with the
Write tool under `--disallowedTools Edit Write`, the child answered "Cannot —
Write tool disabled this session (and in subagents)" and left the file
untouched. The restriction reaches subagents, which matters: a read-only child
that could delegate its way around the flag would not be read-only at all.

**Tool names are validated.** A malformed invocation produced one
`Permission deny rule "<word>" matches no known tool — check for typos.` per
word. A misspelled tool name is reported, not silently ignored — so
`--disallowedTools NotebookEdit` fails loudly if that name is ever wrong.

**Argv shape is load-bearing.** `--disallowedTools` takes a space-separated
variadic list, so it swallows whatever follows it. In the first attempt it ate
the prompt itself and the run died with "Input must be provided either through
stdin or as a prompt argument". mnemo passes the prompt as a *positional*
(`dispatch.py:750`), and the comment at `dispatch.py:745-748` already warns that
flags must precede it — which is exactly the collision. The flag must therefore
be emitted early, with another flag after it, or the prompt must be separated
with `--`. This is a test requirement, not a footnote: an argv that silently
absorbs the prompt produces a child that never gets its instructions.

### Bash bypasses the restriction — measured

A read-only child needs Bash (`gh`, `git log`, `rg`, the suite), so Bash is not
restricted by this design. Whether the child can therefore write files through
the shell with the file tools closed was the design's one open question.

**It can.** Measured 2026-09-17 against a real background child, which is the
only environment that reproduces the case — the sandbox wrapping `claude --print`
blocks shell redirection on its own and masks the question entirely:

```
claude --bg --disallowedTools Edit Write NotebookEdit --model haiku \
  "...run exactly printf CHANGED > target.txt and then run cat target.txt..."
```

The file read `original` before and `CHANGED` about thirty seconds later. The
child's own transcript shows the path it took, and it is not a subtle one:

```
Bash | printf CHANGED > target.txt
Bash | cat target.txt
```

No refusal, no sandbox, no warning. `--disallowedTools` closes the named tools
and nothing else.

**So the claim this design may make is exactly this, and no wider:**

> A read-only child's file-editing tools are closed — it cannot call Edit,
> Write or NotebookEdit, and neither can its subagents. It can still write
> through the shell. The restriction prevents accident and drift, not intent.

That is materially stronger than prompt-only (approach B), where nothing is
closed at all and a child that reaches for Edit out of habit succeeds. It is
materially weaker than a sandbox, and the feature must not be described as one.

Three consequences for the implementation:

1. The `--read-only` help text says what is closed, never that the child
   "cannot write".
2. The README says the same (Task 8 of the plan).
3. The worktree stays, and its justification is now load-bearing rather than
   belt-and-braces: it is the only thing confining a shell write to a throwaway
   tree instead of the maintainer's checkout.

What must not happen is shipping wording that describes a guarantee which was
never observed — the repo has a documented history of exactly that failure
(`verify-subagent-claims-by-running`, `grep-is-a-proxy-run-the-function`). This
section exists because the first draft of this spec did precisely that.

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
- Argv-level: `spawn_child` emits `--disallowedTools` with at least one flag
  after it, and the positional prompt arrives intact and last — the regression
  this pins is the measured one, where the variadic list ate the prompt and the
  child got no instructions at all. Emits nothing when not read-only
  (byte-identical argv to today — the same invariant `dispatch.py:282` already
  holds for `--may`).
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
4. **Can a read-only child write through Bash?** Answered: yes, measured
   2026-09-17 — see "Bash bypasses the restriction". Closed as an open question;
   what it leaves behind is a wording constraint, not a design choice.
