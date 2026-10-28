---
name: decomposing-for-dispatch
description: Use after designing a feature, when deciding whether its parts can be built in parallel - produces the contract file that `mnemo dispatch --contract` consumes
---

# Decomposing a feature for dispatch

Turn a designed feature into a **contract**: the pieces, and the boundary
between them.

Your input is the conversation you are already in — a brainstorm, a plan, or the
maintainer describing the work out loud. Do not go looking for a plan file;
plain plan mode writes none, and the contract is the durable artifact, not a
copy of something else.

Write the contract to `docs/superpowers/contracts/YYYY-MM-DD-<feature>.md`.
Then stop. Dispatching is the maintainer's decision, not yours.

Nothing reads that directory — `--contract` takes an explicit path, so the
location is a filing convention and not a lookup. It is named here because it
is where every contract in this repo actually lives, next to the specs and
plans the decomposition came out of. An earlier version of this file
prescribed `docs/mnemo/contracts/`, which no contract ever used and which has
never existed in the repository.

## The test

For every pair of candidate pieces, ask:

> Can piece A be written and tested without reading the interior of B?

If no, they are not two pieces. Merge them and ask again.

Two pieces may depend on each other's **signatures** — that is what the contract
records. They may not depend on each other's **internals**.

## Shared code is the seam, not a piece

Code that more than one piece calls *and depends on the content of*, or that
more than one piece edits, belongs to no piece. It is the **seam**: you land
it on the base branch yourself, before dispatching, and every piece branches
from a base that already has it. Children branch from the base, so a seam
that is not merged yet is not there for them.

Spot it while decomposing:

- **A file in two pieces' `files`.** Two children will edit it in parallel.
  `mnemo dispatch --contract` warns about every such pair, `--dry-run`
  included. Treat the warning as a cut to redo, not as noise.
- **A registration block.** Every piece adds one line to the same list in the
  same file: a command table, a router, the `mod`/`invoke_handler` lines in a
  `lib.rs`, an `__init__` export. Land the block with every piece's line
  already in it, pointing at a stub, so each piece only fills its own stub. In
  one wave, three pieces edited the same registration lines in one `lib.rs`,
  and every pair conflicted at merge.
- **A table or type whose values two pieces rely on.** A permission matrix, a
  shared enum, a config schema. If a piece consuming it would be *wrong*
  rather than just unbuilt when its content changes, the boundary runs through
  its content, not its signature, and the test above already failed. In one
  contract, two pieces relied on a third piece's permission matrix. After all
  three stopped, the parent fixed the matrix and then fixed both consumers.

The seam is small: signatures, stubs, the shared table, the registration
lines. If it turns out to be most of the feature, the answer is `sequential`.

## `sequential` is a real answer

If the work does not divide, write `verdict: sequential` and say why. That is a
correct outcome, not a failure. A decomposition that always finds a cut produces
only bad merges.

Cutting by area — one piece for the backend, one for the frontend, one for the
tests — almost always fails the test above, because all three land in the same
files. Teams exist because a boundary already does; the boundary does not appear
because you named teams.

## Boundary, not approach

The contract says **where** a piece may work and **what** it must deliver. It
never says **how**.

- Boundary: "only `src/mnemo/core/contracts.py`", "deliver `parse_contract(path) -> Contract`"
- Approach: "use a regex", "subclass `dict`", "cache it"

A child given an approach cannot refuse a wrong one. This has already cost a real
dispatch here (#187): the child was handed a prescribed fix, refused it, and was
right — the instructed change would have caused a large-scale regression.

## Format

```markdown
---
feature: <slug>
created: YYYY-MM-DD
verdict: parallel
---

## <piece-slug>
- **files:** path/one.py, path/two.py
- **exposes:** `literal_signature(arg) -> Type`
- **consumes:** `other_signature(x) -> T` from other-piece
- **model:** haiku
- **effort:** medium
```

Rules the parser enforces — a contract breaking one is refused before any
worktree is created:

- Slugs are lowercase letters, digits and hyphens.
- Every piece declares at least one file.
- Every `consumes` names a piece that exists, and never the consuming piece
  itself.
- `exposes` is a **literal signature**. Other pieces are written against it while
  they wait, so a prose description cannot be delivered against.
- `model` is optional and takes one `--model` value — an alias (`haiku`,
  `sonnet`, `opus`) or a full id. A sentence there is refused.
- `effort` is optional and takes one of `low`, `medium`, `high`, `xhigh`,
  `max`. Anything else is refused.
- `may` is optional and takes `push`, `pr` (push and open the PR) or `none`.
  Omitted, the piece takes `mnemo dispatch --may`, which is `pr` by default.
  `merge` is refused.

Signatures go in backticks; commas inside them are safe. `files` is a plain
comma-separated list.

`model` is the one field about *cost* rather than boundary, and it is
admissible for the same reason `files` is: it says what to spend on a piece,
never how to build it. Name it where a piece's boundary is small and its work
is mechanical — two files, one signature, nothing to decide; leave it off
where the piece has to fit itself around an interface it does not own, and it
will take whatever `mnemo dispatch --model` was given, or the machine's
default. A piece that names one wins over the flag, so a contract's per-piece
judgement survives a blanket typed at the command line. `effort` is the
same kind of field and resolves the same way against `mnemo dispatch
--effort`; leave it off unless the piece plainly needs more or less
reasoning than the dispatch gives.

`may:` is optional and defaults to `pr` — the piece's child publishes its own
pull request. Write `may: none` for a piece that should not become a branch
at all, such as an exploratory spike. `may: merge` is refused: landing belongs
to `mnemo land`.

It is a **permission**, not a budget: what the piece's child may publish once
its suite passes, without stopping to ask. Leave it off and the piece takes
whatever `mnemo dispatch --may` gave the rest, which is `pr` unless the
maintainer said otherwise.

Prose is free-form anywhere except a `##` heading, which the parser reads as a
piece slug — a section like `## Notes` is refused as an unaddressable slug.
Explain the decomposition in the preamble, in a piece's body, or in an HTML
comment. Note that a `- **files:**` bullet written *before* the first `##` is
silently ignored, because it belongs to no piece.

For a commented example that is itself parsed by the parser it documents:

    mnemo dispatch --contract --example

## What each piece is already told

Each piece's brief is built from the contract by `mnemo dispatch`, so the
contract need not say any of this. It names the piece's files as a hard boundary,
its signatures, and what it may publish. It also asks for two checks the
parent used to run at review: a **mutation pass** before the child stops
(break the code each new test guards and watch the test fail), and, where it
may publish, a **push** before it stops. A piece that stops with commits that
are not pushed is reported that way in the parent's finish notice.

## After writing

If the seam is not on the base branch yet, say so: it lands first.

Tell the maintainer the file is ready for review, and show the command:

    mnemo dispatch --contract docs/superpowers/contracts/<file>.md --dry-run

Do not run it.
