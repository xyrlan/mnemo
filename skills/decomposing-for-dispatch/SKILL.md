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

Write the contract to `docs/mnemo/contracts/YYYY-MM-DD-<feature>.md`. Then stop.
Dispatching is the maintainer's decision, not yours.

## The test

For every pair of candidate pieces, ask:

> Can piece A be written and tested without reading the interior of B?

If no, they are not two pieces. Merge them and ask again.

Two pieces may depend on each other's **signatures** — that is what the contract
records. They may not depend on each other's **internals**.

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
```

Rules the parser enforces — a contract breaking one is refused before any
worktree is created:

- Slugs are lowercase letters, digits and hyphens.
- Every piece declares at least one file.
- Every `consumes` names a piece that exists, and never the consuming piece
  itself.
- `exposes` is a **literal signature**. Other pieces are written against it while
  they wait, so a prose description cannot be delivered against.

Signatures go in backticks; commas inside them are safe. `files` is a plain
comma-separated list.

## After writing

Tell the maintainer the file is ready for review, and show the command:

    mnemo dispatch --contract docs/mnemo/contracts/<file>.md --dry-run

Do not run it.
