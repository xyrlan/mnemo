---
feature: share-rules
created: 2026-09-13
verdict: parallel
---

Closes #245: a vault is one person's. Read it in full (`gh issue view 245`).
This preamble records the design decisions the issue left to the implementer
and the facts in the code that forced them; each piece below says what it
must deliver and where it may work, never how.

**Design: a tree checked into the repo, not a git remote for `shared/`.**

The unit of sharing is a repo, and the vault is not. The real vault holds
81 feedback rules across six projects, 2 `user` pages that carry a name and
an email, and 1456 reference pages; `mnemo export` already warns about the
user pages before a commit. A remote for `shared/` shares all of that with
everyone; a per-repo tree shares the slice attributed to this repo with the
people who already have the repo. It is also the only shape that meets every
constraint the issue lists at once: no service and no network beyond the
`git push` the user already does; reviewable in a PR like any other file;
a newcomer's clone already carries it; diffable and mergeable because it is
the same one-file-per-rule markdown `shared/` already is.

**The tree lives at `<repo>/.mnemo-shared/<type>/<slug>.md`.** Not under
`.mnemo/` or `.claude/`: `mnemo init --project` appends both to the repo's
`.gitignore` (`cli/commands/init.py:GITIGNORE_ENTRIES`), and gitignore cannot
re-include a path whose parent directory is excluded, so anything published
there would silently never be committed. A dot-directory matches
`.cursor/rules` and `.claude/rules`, which is what the repo's other tooling
looks like. `.mnemo/` (the pattern) does not match `.mnemo-shared/`.

**The on-disk page is the contract.** Same markdown, same frontmatter reader
(`filters.parse_frontmatter`, one nesting level), with three changes from the
vault page it came from:

- **Provenance is kept and vault paths are not.** `sources:` entries are
  `bots/<repo>/briefings/sessions/<uuid>.md` — meaningful only inside the
  publishing vault — and the `<!-- mnemo:graph-section -->` block wikilinks
  the same paths. Both go. What stays is the evidence: the `evidence.quote`
  verbatim and its `source` line, the `confidence` as it stood when published
  (`verified` or `inferred`), and a provenance block naming the publishing
  vault, the publisher's project name, the date, and the source count.
- **Which vault, without PII.** No vault has an identity today (`grep uuid
  src/` finds nothing). One is created on first use at
  `<vault>/.mnemo/vault-id`, an opaque random id. It exists for two reasons
  neither publisher nor importer can do without: a re-publish prunes only
  the files it wrote itself, so two contributors publishing into one tree
  do not delete each other's rules; and an import skips the files that came
  from the importing vault, so publishing and importing in the same repo is
  not a loop. It is never a hostname, a git identity, or an email —
  `core/redact.py` strips emails out of rule text for exactly this reason.
- **A hop preserves provenance.** A rule that was imported, reviewed,
  promoted and then re-published by the second vault still names the first
  vault as its origin. Relabelling on every hop would make every rule look
  like the last publisher's.

**What the importing vault does with it** — these are facts of the code, and
the `import` piece is written against them:

- An imported page is staged under `shared/_inbox/<type>/`, never written to
  `shared/<type>/`. `filters.is_consumer_visible` makes location the
  authority on draft-ness, the reflex index and the MCP tools both gate on
  it, and the issue's "not proposed" list forbids changing what the reflex
  injects from `_inbox/` (nothing). Review is the `mv` backfill users already
  do, and `mnemo rewrites` already reviews `.proposed.md` siblings.
- `confidence` is only ever compared `== "verified"` (`hooks/session_start.py:532`,
  `cli/commands/learn.py:86`, `cli/commands/status.py:218`,
  `core/reflex/replay.py:251`). An imported rule carries
  `confidence: verified-elsewhere` when it was published `verified`, and
  `inferred` otherwise, so every "you said" surface excludes it with no
  further change. `rewrites/merge.py:_LIVE_WINS` lists `confidence`, so a
  staged rewrite can never overwrite a live rule's confidence either way.
- Project attribution comes from `sources:` paths, and an imported page has
  none. `rule_activation/index.py:projects_for_rule` falls back to a
  `projects:` frontmatter list when the sources are empty, and every scope
  reader (`export/select.py`, the reflex index, the activation index) goes
  through it. The importer stamps `projects: [<local canonical project>]` —
  the *local* name from `resolve_canonical_agent(cwd)`, because the
  publisher's clone may be named differently and the published name is
  provenance, not authority.
- The stamp is `origin: imported`, alongside `origin: backfill`
  (`core/backfill/origin.py`) — the existing precedent for "material the
  vault did not observe". `is_backfill_frontmatter` compares to the literal
  `"backfill"`, so the two never collide.
- **#248 is out of scope and every piece must know it exists.** A live page
  in `shared/<type>/` with no `.mnemo/extraction-state.json` entry is
  overwritten unconditionally by the extractor (`branches/auto_promoted.py:_handle_no_entry`);
  reproduced on this checkout, filed as #248. Every hand-promoted page is
  already in that hole and an imported rule promoted with `mv` joins it.
  Import does **not** write state entries to work around this (an entry
  makes `written_hash` mean "the extractor owns this file", which is the
  opposite of the truth) and does not touch the extractor. Fix #248 where
  it lives.

**Not in any piece: `CHANGELOG.md` and `README.md`.** #246 records that every
parallel PR conflicts in `CHANGELOG.md`. This is one feature with one entry
and one README section; the landing writes both, after the merge. Document
your command in its `--help` and its module docstring.

**Shared file: `src/mnemo/cli/parser.py`.** Two pieces add a subparser
there. `publish` adds its block immediately after the `export` block (the
line `export.add_argument("--remove", ...)`, ~line 391) and its name to
nothing else; `import` adds its block immediately after the `backfill` block
(before `rewrites_p = sub.add_parser(`, ~line 319). Add only your own lines,
leave every neighbouring line byte-identical, do not reformat or reorder —
that is the whole of how the three-way merge resolves. Neither command is
advanced or internal, so `ADVANCED_COMMANDS` / `INTERNAL_COMMANDS` are not
touched.

**Landing order.** `format` is a leaf; `publish` and `import` consume only
its signatures. Both are written and tested against those signatures in
worktrees where the module does not yet exist — that is expected, the
example contract says the same — and `mnemo land` checks every consumed name
against the merged tree and runs the merged suite. The one real risk is a
fixture in `publish` or `import` that guesses the portable page's shape and
guesses wrong; keep such fixtures to the keys named above, or build them by
calling the consumed signature and let the merge supply it.

## format

- **files:** src/mnemo/core/share/__init__.py, src/mnemo/core/share/format.py, tests/unit/test_share_format.py
- **exposes:** `SHARE_DIR: str`, `vault_id(vault_root) -> str`, `to_portable(text, *, vault, project, today) -> str | None`, `from_portable(text) -> PortableRule | None`, `to_vault_page(rule, *, project, today) -> str`, `iter_portable(share_root) -> list[tuple[Path, PortableRule | None]]`, `portable_hash(text) -> str`, `is_imported_frontmatter(fm) -> bool`
- **consumes:** nothing

The on-disk contract, both directions, and nothing else (`SHARE_DIR` is the literal `".mnemo-shared"`, the tree's root relative to the repo): no CLI, no
selection, no ledger, no writes to a repo or a vault. Pure text in, text
out, plus the one file `vault_id` owns.

Deliver:

- `to_portable`: a vault rule page → the portable page, or `None` when the
  text is not a rule page (no frontmatter, no slug). Keeps `name`, `slug`,
  `description`, `type`, `stability`, the topic tags
  (`filters.topic_tags` — managed markers such as `auto-promoted` and
  `needs-review` never travel), `confidence`, the `evidence` block verbatim,
  and the body with the graph section removed (`text_utils.strip_graph_section`).
  Drops `sources`, `extracted_at` / `extraction_run` / `last_sync` /
  `promoted_at`, `enforce` and `activates_on` (they name local paths and
  local tools), and any `demoted_from` / `promoted_without_enforce` /
  `runtime` stamps. Adds one provenance block carrying the publishing vault
  id, the publisher's project name, the publish date, and the source count.
  When the page is itself imported (`is_imported_frontmatter` is true) the
  provenance already on it is carried through unchanged — the hop rule
  above. Output is byte-stable for unchanged input and a fixed `today`, and
  the date the block carries is *not* re-stamped on every call — the
  publisher needs a tree that shows no diff when nothing changed, so decide
  what `today` means for an already-published page and say so in the
  docstring.
- `from_portable`: the inverse read. `None` when the text carries no
  provenance block — a stray `.md` in the tree is not a rule and callers
  report it rather than stage it. `PortableRule` is a frozen dataclass with,
  at least: `slug`, `type`, `name`, `description`, `body`, `tags`,
  `confidence` (as published), `quote`, `evidence_source`, `vault`,
  `project`, `published_at`, `source_count`, `hash`.
- `to_vault_page`: a `PortableRule` → the page the importer stages. Written
  in the shape `extract/inbox/rendering._render_page` produces so every
  reader of `shared/` parses it (frontmatter keys in the same order, the same
  `_yaml_scalar` quoting, `needs-review` as the managed tag). Carries
  `origin: imported`, `confidence: verified-elsewhere` or `inferred` as
  described in the preamble, `projects:` with the one local project name,
  `sources: []`, the evidence block verbatim, and an `imported:` block with
  the original provenance plus the import date. Writes no `enforce` and no
  `activates_on`, ever — a rule that can block a tool call is not something
  another vault gets to install.
- `iter_portable`: walk `<share_root>/<type>/*.md` in sorted order; the
  second element is `None` for a file `from_portable` refuses, so the caller
  can report `N files skipped, not mnemo pages` by path.
- `portable_hash`: the identity `publish` writes in its manifest and
  `import` writes in its ledger, over the portable text. Both sides compare
  it, so it has one definition, here.
- `vault_id`: read `<vault>/.mnemo/vault-id`, creating it on first call with
  an opaque random id. Never derived from the machine, the path, or git.
- `is_imported_frontmatter`: the one predicate for `origin: imported`, with
  the same tolerance for the flat and nested spellings that
  `backfill/origin.py` explains at length — read that module's docstring
  before writing this function.

Round-trip is the test: `from_portable(to_portable(page))` names what the
page named, and `to_portable(to_vault_page(from_portable(p)))` still names
the first vault. Use real pages from `tests/unit/_export_fixtures.py:write_rule`
and one copied from the real vault's shape (the issue quotes one), not
hand-typed frontmatter.

## publish

- **files:** src/mnemo/core/share/publish.py, src/mnemo/cli/commands/publish.py, src/mnemo/cli/parser.py, src/mnemo/cli/commands/status.py, src/mnemo/cli/commands/doctor.py, src/mnemo/cli/commands/doctor_checks/__init__.py, src/mnemo/cli/commands/doctor_checks/share.py, tests/unit/test_share_publish.py, tests/unit/test_cli_publish.py, tests/unit/test_cli_status_publish.py, tests/unit/test_doctor_share.py
- **exposes:** `run_publish(vault_root, *, project, repo_root, types, dry_run, today) -> PublishReport`, `staleness(vault_root, *, project, repo_root) -> tuple[int, int] | None`
- **consumes:** `SHARE_DIR: str` from format, `vault_id(vault_root) -> str` from format, `to_portable(text, *, vault, project, today) -> str | None` from format, `iter_portable(share_root) -> list[tuple[Path, PortableRule | None]]` from format, `portable_hash(text) -> str` from format

`mnemo publish`: the rules for the repo you are in, into the repo, in the
form `import` reads back. `mnemo export` is the sibling to read first —
`core/export/__init__.py` and `cli/commands/export.py` — its CLI is the
shape (`--project`, `--types`, `--dry-run`, one line per outcome on stdout,
caveats on stderr, exit 2 for usage and 1 for a write failure), its manifest
under `<vault>/.mnemo/export/<project>.json` is the pattern for yours, and
`status.py:_print_export_status` is the status line to sit beside.

Deliver:

- **Selection** is the reflex's project scope: pages attributed to
  `project` through `projects_for_rule(sources, frontmatter=fm)` plus
  universal ones, consumer-visible only (`iter_shared_pages(include_inbox=False)`
  + `is_consumer_visible`), restricted to `--types`. The default type set
  must not include `user` — those pages carry names and emails and this
  tree gets committed and pushed — and `--types user` prints the same
  caveat `export.print_export_notes` prints. Whether `project` pages
  (the repo's 288 here) are in the default set is your call; argue it from
  what a second contributor needs on day one against what they would have
  to read past. Whether universal rules belong in a per-repo tree is also
  yours; say what you decided.
- **Writes** go to `<repo_root>/<SHARE_DIR>/<type>/<slug>.md` through
  `to_portable`, atomically (`core/atomic.py`). A file whose bytes are
  unchanged is not rewritten; re-running on an unchanged vault leaves `git
  status` clean in the repo.
- **Prune and ownership.** A slug that no longer selects is removed from the
  tree *only* if the file there carries this vault's id. A file carrying
  another vault's id is never removed and never overwritten: a slug
  collision with another publisher is reported by path and left alone.
  Same-slug, same-vault is an update.
- **Manifest** at `<vault>/.mnemo/share/<project>.json`: the repo root it
  was written from, the tree path, when, and `{slug: portable_hash}`.
  `staleness` compares it against what a publish would write now, as
  `export.manifest.staleness` does, and returns `(rules in the tree, rules
  that differ or are new)`, `None` when never published.
- **`mnemo status`** prints one line after the export line, silent when
  never published: the count, the path, and `up to date` or `N differ from
  the vault now, run mnemo publish`. **`mnemo doctor`** gets one row in
  `DOCTOR_CHECKS` (`doctor_checks/share.py`, registered in the package
  `__init__` and the `doctor.py` table) that says the same thing as a
  warning when the tree is behind, and passes silently otherwise. A
  stateless check, not a once-ever notice — `doctor` runs cold every time.
- **`--dry-run`** prints what would be written, updated, pruned and refused,
  and touches neither the tree nor the manifest. **`--remove`** is optional;
  if you add it, it removes only this vault's files and the manifest.
- If the repo's `.gitignore` would ignore `SHARE_DIR`, say so on stderr — a
  published tree nobody can commit is the export-into-`.claude/` mistake
  again. Optional, but cheap (`git check-ignore`).

Judgement calls that are yours: whether `--project` is accepted (export
takes it; the tree still lands in the cwd's repo either way); the exact
wording of every line. Run the full suite before you finish.

## import

- **files:** src/mnemo/core/share/imports.py, src/mnemo/cli/commands/import_rules.py, src/mnemo/cli/parser.py, src/mnemo/core/export/select.py, src/mnemo/core/export/render.py, src/mnemo/hooks/session_start.py, tests/unit/test_share_import.py, tests/unit/test_cli_import.py, tests/unit/test_export_render.py, tests/unit/test_export_select.py, tests/unit/test_hook_session_start_share_notice.py
- **exposes:** `run_import(vault_root, *, share_root, project, dry_run, today) -> ImportReport`
- **consumes:** `SHARE_DIR: str` from format, `vault_id(vault_root) -> str` from format, `from_portable(text) -> PortableRule | None` from format, `iter_portable(share_root) -> list[tuple[Path, PortableRule | None]]` from format, `to_vault_page(rule, *, project, today) -> str` from format, `portable_hash(text) -> str` from format, `is_imported_frontmatter(fm) -> bool` from format

`mnemo import [PATH]`: bring a published tree into this vault, staged for
review, never promoted. `PATH` defaults to `<repo_root>/<SHARE_DIR>` for the
repo the user is standing in and may be any directory in that layout — a
sibling clone, a path someone sent. `cli/commands/backfill.py` is the
sibling to read first: it is the other command that stages material the
vault did not observe, its ledger (`core/backfill/ledger.py`) is the pattern
for "a rerun is a no-op", and its session-start notice is the pattern for
telling the user something is waiting.

Deliver:

- **Only what changed.** A ledger at `<vault>/.mnemo/share/imports.json`
  records, per share root and slug, the `portable_hash` last staged. A rule
  whose hash matches is skipped and counted, not re-staged. A rule whose
  provenance names this vault's own id is skipped as `yours` — importing
  the tree you published is not a loop.
- **Routing, per rule** (the `.proposed.md` machinery is `extract/inbox/paths.py`
  and `rewrites/classify.py:_live_for`; read both, they fix the paths):
  - live `shared/<type>/<slug>.md` exists → stage
    `shared/_inbox/<type>/<slug>.proposed.md`, the shape `mnemo rewrites`
    reviews, unless the live page is already this rule at this hash
    (promoted earlier), in which case skip and count. Never write the live
    file.
  - the slug exists under a **different** type anywhere consumer-visible →
    refuse and report by path; #187's zeroth layer explains why deciding
    which of two same-slug pages is canonical is not a call to make
    unsupervised.
  - `shared/_inbox/<type>/<slug>.md` exists and the ledger says this command
    wrote it *and* its bytes still match what was written → overwrite (an
    unreviewed page of ours, superseded). Exists otherwise → refuse and
    report; it is the extractor's, or edited by hand.
  - else → write `shared/_inbox/<type>/<slug>.md` from `to_vault_page` with
    `project` = the local canonical project name.
  - a file `from_portable` refuses → report by path, never stage.
- **No state entries.** Import writes nothing to
  `.mnemo/extraction-state.json`; the preamble and #248 say why. Put that
  reason in the module docstring so the next reader does not "fix" it.
- **Not the user's own words.** Once an imported rule is promoted, `mnemo
  export` selects it (`projects:` attributes it) and `render.render_entry`
  would print its quote as `> you said: "..."`. Change `select.py` /
  `render.py` so a rule whose frontmatter `is_imported_frontmatter` renders
  the quote attributed to another contributor, not to the reader, and keep
  `entry_hash` stable for every page that is not imported (the manifest
  staleness test in `test_export_manifest.py` pins that). The session-start
  and `learn` "you said" lines already exclude it through `confidence`;
  verify that with a test rather than trusting this sentence.
- **Session-start notice** (optional, argue it): when the repo the session
  starts in has a `SHARE_DIR` tree with rules the ledger has not seen, one
  line inviting `mnemo import`. If you add it, follow the backfill notice's
  per-project discipline (`ledger.notice_shown` / `mark_notice_shown`), not
  a once-ever `should_notify` — once-ever warnings went silent for three
  days on #229. `hooks/session_start.py` is in your files for this alone;
  touch nothing else in it.
- **`--dry-run`** prints every routing decision and writes nothing,
  ledger included. The summary line names the counts: staged, proposed,
  skipped as unchanged, skipped as yours, refused (with why), not a page.

Judgement calls that are yours: whether a refused collision is exit 1 or a
counted line with exit 0 (backfill's `_environmental` split is the
precedent for which failures are the tree's and which are the run's); what
the ledger keys a share root by when the same tree is imported from two
paths. Run the full suite before you finish.
