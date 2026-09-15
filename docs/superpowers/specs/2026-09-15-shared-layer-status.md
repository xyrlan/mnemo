# Spec — why `.mnemo-shared` never ran (2026-09-15)

Investigation piece `investigate-shared` of the channels round
(`docs/contracts/channels.md`). Measure first, then recommend one of fix,
remove, or keep and document. No production code was written; every scratch
run below lives under a job tmp dir and touched neither `~/mnemo` nor any
repo under `~/github`.

## Verdict

**Fix one line, then keep.** The layer works end to end on first contact —
publish, commit, clone, SessionStart invitation, import, promote, update,
propose, rewrite review, all correct against a copy of the real vault. It
never ran because it has existed for about 39 hours, in no released version,
with nothing that invites the *publisher* to run it. None of that is a
defect.

One thing *is* a defect, and it would have hit the first real import
silently: **a promoted imported rule is invisible to the reflex.**
`core/reflex/index.py:101` calls `projects_for_rule(source_files)` without
`frontmatter=fm`, so an imported page (`sources: []`, `projects: [<local>]`)
indexes with `projects: []` and `decide.candidates_for_project` never offers
it. The MCP tools and the SessionStart topic list do see it, so nothing looks
broken — the rule just never gets pushed.

## Why it never ran — the four candidates, separated

| Candidate | Finding | Evidence |
|---|---|---|
| Breaks on first contact | **No** for the round trip; **yes** for the last metre (reflex). | Scratch run §1–§3 below |
| Works and was never invoked | **Yes.** Zero non-dry-run invocations, ever. | Transcript scan, §4 |
| Needs a step nobody is told to take | **Partly.** Nothing invites the publisher; the importer's invitation needs a tree to exist first. | §5 |
| Needs a second human | **Only for import**, and one exists on `clubinho` — but a second human cannot run `mnemo import` from PyPI. | §6 |

The shortest true answer: **it is unreleased and 39 hours old.**

- The three share pieces merged 2026-09-13 22:58 (-0300): #256 format, #260
  publish, #259 import. Both commands printed `unknown command` until #262
  registered them at **2026-09-13 23:15**.
- `v1.5.0` was tagged at **2026-09-13 18:30**, before any of it.
  PyPI's latest `mnemo-claude` is **1.5.0** (checked
  `pypi.org/pypi/mnemo-claude/json` on 2026-09-15). `changelog.d/245.added.md`
  is still pending. Anyone who installed from PyPI has neither command.
- The only machine that has them is this one, through the editable install
  (`python3 -c 'import mnemo; print(mnemo.__file__)'` →
  `~/github/mnemo/src/mnemo/__init__.py`).

## 1. End-to-end run, publisher side

Setup: `vaultA` = copy of `~/mnemo/shared`, `~/mnemo/bots` and
`~/mnemo/.mnemo/vault-id` (real rules, real identity, no logs); scratch repo
named `mnemo` (so `resolve_canonical_agent` yields the real project name) with
the real `.gitignore`, pushed to a bare `origin.git`. Every command ran as
`PYTHONPATH=<worktree>/src MNEMO_CONFIG_PATH=<vault>/mnemo.config.json python3 -m mnemo …`.

```
$ mnemo publish --dry-run   → would publish 8 rules (1 universal) → .mnemo-shared: 8 new …   exit 0
$ mnemo publish             → published 8 rules (1 universal) … 8 new, 0 updated, 0 unchanged, 0 pruned
                              commit .mnemo-shared and push; a teammate runs `mnemo import` to review them
$ git status --short        → ?? .mnemo-shared/
```

- 8 files, 11 946 bytes total. The manifest landed at
  `vaultA/.mnemo/share/mnemo.json`. No gitignore warning (the repo's
  `.gitignore` does not cover `.mnemo-shared`).
- Grep of the tree for `/Users`, `xyrlan`, `@`, hostnames: **0 hits**.
  Provenance is `published: {vault: f95dd98e…, project: mnemo, date, source_count}`.
- 8, not the 9 the landing session's dry run printed on 2026-09-14: the
  vault moved, not the code.

## 2. End-to-end run, teammate side

`vaultB` = fresh `install.scaffold.scaffold_vault` (what `mnemo init` lays
down, minus hook installation); `git clone origin.git B/mnemo`.

```
SessionStart notice (_share_import_notice called directly; the hook itself was not run):
  '[mnemo] this repo publishes 8 rule(s) your vault has not imported (.mnemo-shared/) — `mnemo import --dry-run` …'
  second call: ''                                         (digest marker holds)
$ mnemo import --dry-run → 8 staged, 0 proposed, 0 unchanged, 0 yours, 0 refused, 0 not a page   exit 0
$ mnemo import           → 8 staged … review shared/_inbox/ — move keepers to shared/<same type>/  exit 0
$ mnemo import           → 0 staged, 0 proposed, 8 unchanged …                                   exit 0
```

Staged pages carry `confidence: verified-elsewhere`, `origin: imported`,
`projects: [mnemo]`, `sources: []`, `needs-review`, and an `imported:` block —
exactly what `format.py:370-390` promises.

## 3. Promotion, the reflex gap, and the update loop

Moved `measurement-before-design.md` from `_inbox/feedback/` to `feedback/`
(the documented review step) and rebuilt both indexes.

| Consumer | Sees the promoted imported rule? |
|---|---|
| `rule_activation.build_index` | yes — `projects: ['mnemo']` |
| `mcp.tools.get_mnemo_topics(project='mnemo')` | yes — `measurement, optimization, process` |
| `mcp.tools.list_rules_by_topic('measurement', project='mnemo')` | yes |
| SessionStart payload (`_build_injection_payload`) | yes — `local: [measurement, optimization, process]` |
| `reflex.index.build_index` → `decide.candidates_for_project('mnemo')` | **no — `projects: []`, candidates `[]`** |

`decide(prompt="before designing the ranking mechanism, measure baseline
metrics against real data")` returned `silence_reason='index_missing'`.
Rebuilding the reflex index with the single change
`projects_for_rule(source_files, frontmatter=fm)` (a scratch `exec` of the
function source, not a patch) and re-running the same prompt:
`accepted=['measurement-before-design']`, score 2.12.

The share contract anticipated this reader and got it wrong by proxy:
`docs/superpowers/contracts/2026-09-13-share-rules.md:75-77` says every scope
reader "(`export/select.py`, the reflex index, the activation index) goes
through" `projects_for_rule`. The reflex index does call it — without the
argument that enables the fallback. No test builds a reflex index over an
imported-shaped page (`grep -rln "verified-elsewhere\|origin.*imported" tests`
lists only the share and share-notice tests).

**Blast radius today is zero:** over the real `~/mnemo`, 0 of 1888 live pages
are attributed only through the frontmatter fallback. It bites the first
imported rule and nothing else.

Update loop, after appending a paragraph to two rules in `vaultA` *above* the
`<!-- mnemo:graph-section -->` marker (an edit below it lands in `## Sources`,
which the portable form strips by design — my first attempt did that and
correctly published nothing):

```
A$ mnemo status   → Published: 8 rules → .mnemo-shared (2 differ from the vault now, run mnemo publish)
A$ doctor row     → ⚠ Published rules: 2 differ from the vault now (8 rules in .mnemo-shared)
A$ mnemo publish  → 0 new, 2 updated, 6 unchanged, 0 pruned
B$ git pull; notice → 'this repo publishes 2 rule(s) your vault has not imported'   (re-invites on a new digest)
B$ mnemo import   → staged feedback/liveness-… (inbox copy refreshed)
                    proposed feedback/measurement-before-design → …proposed.md (live page exists)
B$ mnemo rewrites → safe to merge (1) — feedback/measurement-before-design  +2 lines
B$ mnemo publish --dry-run
                  → refused .mnemo-shared/feedback/measurement-before-design.md: published by another vault (f95dd98e), left alone
```

The last line is the ownership rule working as designed: B cannot overwrite
A's file by promoting it.

## 4. It was never invoked

Every `Bash` tool call in every transcript under `~/.claude/projects` whose
command names `mnemo publish` or `mnemo import` as a command (69 matches,
almost all `from mnemo import cli`):

| When (UTC) | Where | What |
|---|---|---|
| 2026-09-14 01:26–01:27 | `mnemo-wt-c-publish`, `mnemo-wt-c-import` | piece children smoke-testing against scratch fixtures |
| 2026-09-14 01:59 | landing session, real vault | `publish --dry-run` → `unknown command: publish` |
| 2026-09-14 02:01–02:15 | landing session, real vault | `publish --dry-run` → `would publish 9 rules`; `import --dry-run` → `.mnemo-shared/ is missing` |
| 2026-09-15 01:57–04:02 | `mnemo-desktop-wt-c-marketplace`, `-wt-29` | `--help`, and `import --dry-run` against the desktop fixture tree |

**Zero non-dry-run publishes. Zero imports into a real vault.** Consistent
with the disk: no `.mnemo-shared` under `~/github` or `~/azure` outside the
mnemo-desktop fixture, no `~/mnemo/.mnemo/share/`, and 0 pages with
`origin: imported` anywhere in `~/mnemo/shared`.

One trace did reach the real vault: `~/mnemo/.mnemo/vault-id` is dated
2026-09-13 23:01 -0300 = 02:01 UTC, the landing session's first successful
`publish --dry-run`. `run_publish` calls `fmt.vault_id()` before it looks at
`dry_run` (`core/share/publish.py:359`, and `_plan` at `:244`), and
`vault_id` creates the file on first call (`format.py:240-241`). Harmless —
it is an opaque random id — but a dry run that writes, and `staleness()`
("Read-only", `publish.py:407`) goes through the same `_plan`.

## 5. What tells anyone to run it

- **Importer:** well covered. The SessionStart notice fires on a clone that
  carries unimported rules, re-fires when the digest changes, and survived
  §2–§3 correctly.
- **Publisher:** nothing, until the first publish. `mnemo status`
  (`status.py:252-263`) and the doctor row (`doctor_checks/share.py`) are
  both silent when no manifest exists, by design. The README paragraph
  (`README.md:249-256`) is the only mention; `docs/getting-started.md` does
  not name `publish`.
- So the importer's invitation depends on a tree that only an uninvited
  publisher can create. The publisher surface that does exist is
  mnemo-desktop's marketplace pane — **Publish** and **Open PR** buttons
  (`mnemo-desktop` #30, merged 2026-09-15 01:07, `marketplace.rs:973`
  `publish_with` runs `mnemo publish` in the repo root). It is 13 hours old
  and has not been pressed (§4).

## 6. The second human

Publish needs one person; import needs a second vault. Committers over the
last 90 days (`git log --since=90.days --format=%ae`, noreply excluded) in
each repo this vault holds feedback rules for:

| Project | Live `feedback` pages attributed (dry-run count where run) | Other humans committing, 90 d |
|---|---|---|
| clubinho | 23 — dry run: `23 rules (1 universal)` | **1** — 253 commits |
| mnemo | 8 — dry run: `8 rules (1 universal)` | 0 |
| clearframe | 7 | 0 |
| meunu | 6 | 0 |
| sg-imports, alis2 | 2 each | 0 |
| central-inteligencia-frontend | 1 | 2 |

`clubinho` is the natural first real use: the largest rule set, real
project corrections (`acl-controllers-require-permission-guard`,
`guarda-cancelamento-assinatura-pagamento-vigente`,
`timezone-serialization-bug-date-not-timestamp`, …), and a second active
contributor. Whether that contributor runs Claude Code with mnemo is **not
knowable from this machine**: `clubinho` tracks `CLAUDE.md` (which does not
mention mnemo) and does not track `.claude/`. Even if they do, PyPI 1.5.0 has
no `import`.

## Secondary observations (not blocking, recorded so nobody re-derives them)

1. **`--project` in the wrong repo prunes.** In the repo holding mnemo's 8
   published files, `mnemo publish --project clubinho --dry-run` prints
   `22 new, 1 updated, 0 unchanged, 7 pruned` — ownership is keyed on the
   vault id, so another project's files from the same vault count as "ours".
   A dry run shows it and the tree is in git, so it is recoverable; it only
   matters if someone uses `--project` to publish a second project into one
   repo.
2. **"Your vault paths dropped" is almost true.** `evidence.source` keeps a
   vault-relative path, e.g.
   `bots/mnemo/briefings/sessions/47e14942-….md` (`format.py:303,324`), while
   `README.md:251` says vault paths are dropped. No name, email or absolute
   path leaks; a session uuid and a project directory do.
3. **B cannot re-publish a rule it imported from A** (§3, last line). Correct
   under the ownership rule, and worth knowing before the marketplace pane
   offers Publish on a tree full of a teammate's rules: it will print
   `refused` lines, not fail.

## Recommendation

**Fix, naming what:**

1. `core/reflex/index.py:101` — `projects_for_rule(source_files, frontmatter=fm)`,
   plus a test in `tests/unit/test_reflex_index_build.py` that builds the
   reflex index over a `shared/feedback/` page with `sources: []` and
   `projects: [p]` and asserts `candidates_for_project(index, p)` contains it.
   Without this, the first teammate who reviews and promotes an imported rule
   gets it in the topic menu (pull, 364 calls ever) and never in the reflex
   (push). It must land **before the release that first ships
   `publish`/`import`**, or that release ships the gap.
2. Optional, same PR or later: move `fmt.vault_id()` behind `dry_run` in
   `run_publish`/`_plan` (the id can be computed without being persisted),
   and either drop `evidence.source` from the portable form or change the
   README's "vault paths dropped" to say a vault-relative source is kept.

**Then keep, and document where:**

- Release it. The layer is correct; zero firings are the expected count for
  something unreleased and 39 hours old, not a signal about the design.
- Add one short "Sharing rules with a teammate" section to
  `docs/getting-started.md` (publish → commit → teammate imports → review the
  inbox), since the publisher has no in-product invitation and the README
  paragraph is the only place the flow is written down. No CLI-side publish
  nudge: mnemo-desktop's marketplace pane is the publisher surface
  (`team-layer-direction`, layer 1), and a SessionStart nudge to publish
  would fire on every solo repo — six of the seven repos above.
- Re-measure after the first real use, not on a timer: the question worth
  asking then is how many imported rules a teammate promotes out of
  `_inbox`, which `origin: imported` in `shared/<type>/` answers from disk.

**Remove — rejected.** Cost: ~1 640 lines of source (`core/share/` 1 278,
`cli/commands/publish.py` + `import_rules.py` 240, the doctor row 44, the
SessionStart notice 46, the status row ~30, parser entries), ~2 450 lines of
tests, and it breaks
mnemo-desktop's shipped marketplace pane (`marketplace.rs:841` runs
`mnemo import`, `:976` runs `mnemo publish`) and layer 1 of the agreed team
direction. Nothing measured argues for it: the failure to fire is
explained entirely by availability.

## Reproduce

```sh
W=<this worktree>; T=$(mktemp -d)
mkdir -p $T/vaultA/.mnemo && cp -R ~/mnemo/shared ~/mnemo/bots $T/vaultA/ && cp ~/mnemo/.mnemo/vault-id $T/vaultA/.mnemo/
printf '{"vaultRoot": "%s"}\n' $T/vaultA > $T/vaultA/mnemo.config.json
PYTHONPATH=$W/src python3 -c "from pathlib import Path; from mnemo.install import scaffold; scaffold.scaffold_vault(Path('$T/vaultB'))"
printf '{"vaultRoot": "%s"}\n' $T/vaultB > $T/vaultB/mnemo.config.json
git init -q --bare -b master $T/origin.git
mkdir -p $T/A/mnemo && cd $T/A/mnemo && git init -q -b master && echo x > README && git add -A && git commit -qm init \
  && git remote add origin $T/origin.git && git push -q origin master
PYTHONPATH=$W/src MNEMO_CONFIG_PATH=$T/vaultA/mnemo.config.json python3 -m mnemo publish
git add .mnemo-shared && git commit -qm publish && git push -q origin master
git clone -q $T/origin.git $T/B/mnemo && cd $T/B/mnemo
PYTHONPATH=$W/src MNEMO_CONFIG_PATH=$T/vaultB/mnemo.config.json python3 -m mnemo import
mv $T/vaultB/shared/_inbox/feedback/measurement-before-design.md $T/vaultB/shared/feedback/
PYTHONPATH=$W/src python3 -c "
from pathlib import Path; from mnemo.core.reflex import index as i, decide as d
x = i.build_index(Path('$T/vaultB')); print(x['docs']['measurement-before-design']['projects'], d.candidates_for_project(x, 'mnemo'))"
# → [] []   (expected after the fix: ['mnemo'] ['measurement-before-design'])
```
