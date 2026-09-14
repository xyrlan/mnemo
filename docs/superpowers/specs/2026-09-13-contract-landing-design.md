# Contract landing — design (#236)

**Date:** 2026-09-13
**Issue:** #236 — a delivered contract has no last metre: pieces are merged and
their signatures checked by hand.

## What exists

- `mnemo dispatch --contract <path>` fans a contract out: one child per piece,
  on `feat/<feature>/<slug>`, in `<repo>-wt-c-<slug>`.
- `mnemo deliver <id>` pushes a piece and opens its PR. `mnemo deliver --review`
  lists every dispatch worktree and whether it is deliverable.
- `core/contracts.py` parses the contract and validates its shape. It
  deliberately does **not** check that an owner exposes the signature a
  consumer names: the two are hand-written and differ cosmetically.

After `deliver`, nothing reads the contract again. The merge — where a
consumed signature becomes real — is done by hand, in an order the maintainer
recovers from the contract by eye, with the suite run by hand after each.

## Shape

One new verb, `mnemo land <contract.md>`, with a read-only default and an
opt-in `--merge`. Not a mode of `deliver`, for the reason `deliver`'s own
docstring gives: its invariant is *naming an id is the approval, and there is
no flag that approves N children*. A contract landing is inherently "every
piece of this contract" — the pieces were each approved when each was
delivered, and what remains is sequencing, not approval — so it needs a verb
whose contract is different, rather than a flag that makes `deliver`'s
docstring false.

### `mnemo land <contract.md>` — read-only

Parses the contract with `contracts.parse_contract` (never re-parsing the
markdown), computes the merge order, and prints one block per piece in that
order:

- the branch (`dispatch.branch_name`) and which ref carries it — local, or
  `origin/`, or gone;
- the PR for that branch and its state (`OPEN`/`MERGED`/`CLOSED`/none), via
  the same `gh pr list --head` join `deliver` uses;
- for every `exposes` entry, whether a definition with that name is present in
  the piece's `files` on that ref — `✓` present, `✗` missing, `?` when the
  signature has no identifier to look for (a CLI shape like
  `` `mnemo dispatch --contract <path>` ``);
- for every `consumes` entry, whether the owner's `exposes` carries the same
  name — the static half of "does the consumed signature match the exposed
  one", which the contract parser refuses to do by literal equality and which
  a name comparison does do.

A merged piece whose branch is gone is inspected on the base branch: its work
is in `master`, and that is where its signatures should be.

Exit 0 when the contract lands cleanly by inspection, 1 when any piece cannot
be landed (no PR, branch gone and not merged, a missing signature, a cycle).
The last line is the command to run next.

**Signature presence** is a name check, not a string check. The name is the
leading identifier of the backticked signature (`load(key) -> R` → `load`,
`Readiness.ready` → `ready`), and "present" means a `def`, `async def`,
`class` or top-level assignment of that name exists in one of the piece's
`files` at that ref. `files` entries may be globs and are expanded against
`git ls-tree`. This is deliberately the granularity the contract module's
docstring argues for: a renamed argument is not a broken boundary; a missing
function is.

**Order** is a stable topological sort by `consumes`: an owner lands before
every consumer, ties keep contract order. A cycle (A consumes from B, B from
A — which `_validate` admits) has no order and is refused by name.

### `mnemo land <contract.md> --merge` — finish it

Two phases, and the irreversible one runs only after the reversible one
passed in full.

1. **Rehearsal.** A temporary detached worktree at the base (`origin/master`
   after a fetch, else local `master`). For each piece in order:
   `git merge --no-ff` its ref (an already-merged piece is skipped),
   then check that piece's `exposes` names are present in its files in the
   merged tree, then check every `consumes` name is present in the *owner's*
   files in the merged tree (the owner landed earlier, so this is the moment
   the consumed signature either exists or does not), then run the suite.
   The first conflict, missing name, or red suite stops the rehearsal with a
   message naming the piece and the step. The worktree is removed on every
   path.
2. **Merge.** Only when every step passed: `gh pr merge <url> --squash` per
   piece, in the same order, stopping at the first `gh` refusal with its
   stderr. A piece whose PR is already merged is skipped, so a landing that
   stopped part-way can be rerun.

Refused before the rehearsal: a cycle; a piece with no PR (not delivered);
a piece with an open PR but no branch reachable.

`--suite CMD` overrides the suite command (default `python -m pytest -q`,
run with `PYTHONPATH=src` prepended when the tree has a `src/` directory,
because an editable install resolves to the checkout it was installed from,
never to a rehearsal tree). `--method squash|merge|rebase` selects the merge
method (default squash, which is what this repo's history is).

Nothing is persisted, as with `deliver`: every fact is re-derived from git
and `gh` on each run.

## Not in scope

- A scheduler. Dispatch stays a flat fan-out; landing is the sequential last
  metre after every piece was delivered.
- Rebasing or updating a piece's branch. A conflict is reported, not resolved.
- Deleting branches or worktrees after landing.

## Files

- `src/mnemo/core/landing.py` — order, signature presence, inspect,
  rehearse, merge.
- `src/mnemo/cli/commands/land.py` — the verb.
- `src/mnemo/cli/parser.py` — the subparser.
- `src/mnemo/core/sessions/delivery.py` — `pr_for` gains a sibling that also
  returns the PR state; `pr_for` is unchanged for its callers.
- `tests/unit/test_landing.py`, `tests/unit/test_land_command.py`.
- `CHANGELOG.md`.
