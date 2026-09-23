# changelog.d/

One file per change, assembled into `CHANGELOG.md` at release. Nothing else
edits `CHANGELOG.md`'s `## [Unreleased]` between releases, so parallel PRs
never conflict there (#246).

## Writing one

`changelog.d/<id>.<section>.md`, where:

- `<id>` is the issue or PR number (`246`), or `<feature>-<slug>` for a
  contract piece (`contract-dispatch-parser`);
- `<section>` is one of `breaking`, `added`, `changed`, `deprecated`,
  `removed`, `fixed`, `security`, `internal` — the `### ` heading it lands
  under, in that order. `internal` is for what no user of mnemo runs or
  sees: a measurement tool under `tools/`, tests, CI. A new `tools/measure_*`
  script is `internal`, not `added`.

The file holds the `- ` bullet(s) exactly as they will appear under the
heading — the same prose an entry carried when it was written into
`CHANGELOG.md` directly. Match the surrounding style: a bold lead, the
reasoning, the issue number in parentheses at the end.

```markdown
- **What changed, in one bold sentence.** Why it matters and what a reader
  should do differently, in the same voice as the entries above it. (#246)
```

## Releasing

```sh
python3 tools/assemble_changelog.py          # folds every fragment in, removes it
python3 tools/assemble_changelog.py --check  # validates, writes nothing
```

Each fragment goes to the top of its `### Section` inside `## [Unreleased]`;
a missing heading is created in canonical order. With no fragments the file is
left byte for byte. Commit the assembled `CHANGELOG.md` with the version bump;
the release workflow refuses to publish while a fragment is still here.
