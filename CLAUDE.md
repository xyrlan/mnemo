# Working in this repo

How work is run here. None of it says how to solve anything — that is yours to
decide from the issue and the code.

## Run the suite with `PYTHONPATH=src`

```sh
PYTHONPATH=src python3 -m pytest -q
```

The editable install's path entry is **absolute and points at the main
checkout**, so a bare `pytest` in a worktree imports `~/github/mnemo/src` —
master's code, not yours. It passes, and it proves nothing about what you
changed. Check which tree you are testing whenever a result surprises you:

```sh
PYTHONPATH=src python3 -c "import mnemo; print(mnemo.__file__)"
```

The default `addopts` already excludes the `recall` and `live_claude` markers,
so that one command *is* the full suite. Both are opt-in and need a real vault
or a real `claude` binary; do not add `-m` to pull them in.

## The package still supports Python 3.8

`requires-python = ">=3.8"`, and CI builds every version down to it. A local
suite on 3.13 says nothing about that floor: `Target = int | str` at module
level passed 2915 tests locally and broke CI on 3.8 and 3.9 (PR #209). In
module-level expressions — assignments, defaults, `TypeVar` bounds — use
`Union[...]`. `from __future__ import annotations` defers *annotations* only,
never the expressions around them.

## Changelog entries go in `changelog.d/`, never in `CHANGELOG.md`

One file per change, `changelog.d/<id>.<section>.md`, assembled at release.
Editing `CHANGELOG.md` between releases makes every parallel PR conflict with
every other one (#246). `changelog.d/README.md` has the shape.

## Commits and PRs are written in English

Whatever language the conversation is in. The repo's history is English and
stays that way.

## A measured claim ships with the thing that measured it

Numbers in a docstring, an issue or a PR body are expected to be reproducible:
the repo keeps its counters in `tools/measure_*.py`, each with unit tests over
synthetic transcripts. Say what you ran, and prefer running the code to
grepping it — two false bug reports in one day came from greps against code
that was correct.
