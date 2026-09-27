# #273 — a rule with a checkable shape should carry its check

**Verdict: refused as specified.** The field the issue asks for already exists
(`enforce:`), with every one of the four bullets already built. The measured
problem is not a missing mechanism — it is that the mechanism is used by
**1 of 1842 live rules (0.05%)**. Building a second, weaker one next to it
would not raise that number.

What follows is the evidence, then the one real gap and what to do instead.

## 1. `check:` already exists, and is called `enforce:`

Every bullet in "What would close it" is already shipped:

| The issue asks for | Already exists | Where |
| --- | --- | --- |
| A rule declares a pattern in frontmatter | `enforce: {tool, deny_pattern, deny_command, reason}` | `core/rule_activation/parsing.py`, `matching.py` |
| …run against the repo, reported with slug + offender | Runs *pre-flight* on the tool call and **blocks** it; denial logged with slug | `hooks/pre_tool_use.py:66-72`, `rule_activation/activity_log.py:18` |
| A CLI that lists/audits them | `mnemo list-enforced` | `cli/commands/list_enforced.py` |
| `mnemo doctor` reports failures | Two dedicated checks | `doctor_checks/rules.py:167` (`_doctor_check_bare_deny_command`), `:196` (`_doctor_check_stripped_enforce`) |
| Extraction *proposes*, human accepts, never the model | Prompt teaches the shape and defaults to omitting; auto-promotion **strips** `enforce` and stamps `promoted_without_enforce: true` for human review | `core/extract/prompts/templates/system_feedback.py:68-83`; `doctor_checks/rules.py:196` |

There is also `activates_on: {tools, path_globs}` — the advisory, path-scoped
sibling — used by 16 of 47 live feedback rules.

A `check:` field would be a strictly weaker `enforce:`: post-hoc instead of
pre-flight, advisory instead of blocking, and a second schema to validate,
index, export, document, and keep from drifting.

## 2. `mnemo export` deliberately drops it — this is a security boundary, not an oversight

The third bullet ("`mnemo export` carries `check:` so a teammate's CI can run
it") asks to reverse a decision that is load-bearing, stated twice in
`core/share/format.py`:

> `:389` — *never `enforce` and never `activates_on`: a rule that can block a
> tool call is not something another vault gets to install.*

> `:4` — *a vault rule page is one person's: its `enforce:` block can block a
> tool call on that machine…*

A published rule is a *draft* in the receiving vault (`needs-review`,
`confidence: verified-elsewhere`). Shipping an executable pattern across that
boundary means a rule authored elsewhere runs code in your CI on import. If
cross-vault checks are wanted, that is a separate decision about a trust
boundary, and it should be argued on its own — not smuggled in as a field on a
new feature.

## 3. The proposed shape cannot express the prototype it cites

The issue offers #255's scanner as "what one `check:` looks like when written
by hand." It is not expressible as "a grep pattern or an AST predicate over
named globs":

`tests/unit/test_text_io_encoding.py` is 279 lines that must know
receiver modules (`os.open` vs `io.open` vs `Path.open` — `_NOT_TEXT_IO`),
argument position (`Path.open(mode)` has mode first, the builtin has file
first, `:91`), literal-vs-non-literal mode (`_mode_is_binary` returns
*tri-state* — a non-literal mode is flagged until proven binary), an
exemption marker, and a 38-case shape table so the scanner cannot pass
vacuously.

Measured on this clean tree (`git` HEAD `a8ee42b`), where the AST scanner
reports **0** violations:

```
naive grep 'read_text()'                        →  0
grep '\.write_text('   minus lines w/ encoding  →  5   ← all false positives
grep '[^_a-z]open('    minus lines w/ encoding  → 17   ← all false positives
```

**22 false positives on an already-clean tree.** All 22 are either multi-line
calls (the `encoding=` is on the next line — a line-oriented pattern cannot
see it) or binary-mode opens (`open(path, "rb")`, which must *not* be flagged).

A declarative `check:` for the one rule the issue holds up as proof would red
CI on a clean tree. The issue's own stated fear — "a wrong generated test
blocks CI, which is worse than an ignored rule" — is realised by a
hand-written pattern too. The risk was never *generation*; it is that
expressive-enough checks are programs, and programs need tests. #255's
scanner has 38.

## 4. The proposed metric is not obtainable from the reflex log

The issue says "the reflex log already records which rules were injected into
a session that then failed the check." Measured over the real log
(`~/mnemo/.mnemo/reflex-log.jsonl`, 1822 records):

- Fields present: `session_id, project, prompt_hash, prompt_tokens, emitted,
  scores, silence_reason, ts, thresholds, candidates`.
- **No commit, diff, file, or repo-state dimension** — nothing to join an
  injection to a later violation.
- Only **84 of 1822** records injected anything at all.

The denominator for "violated after acquiring a check" does not exist in this
data, and would need a new join key (injection → subsequent commit) that is
not a side effect of adding a frontmatter field.

## 5. The proposed shape does not survive the page writer

Two structural constraints make "a rule may declare `check:` in its
frontmatter" cost more than it reads:

- **`parse_frontmatter` supports exactly one level of nesting**
  (`core/filters.py:244` — *"deeper nesting not supported — drop instead of
  leaking to top level"*). The issue's `check:` with "an AST predicate over
  named globs, with `expect: none`/`expect: some`" is a two-level structure.
  It would have to be flattened into `enforce:`-shaped scalars and lists —
  i.e. into exactly the shape `enforce:` already has.

- **A hand-written `check:` would be silently erased.**
  `extract/inbox/rendering.py:110` `_render_page` re-renders the whole
  frontmatter from the `ExtractedPage` dataclass on every extract run; nothing
  round-trips frontmatter back into that dataclass. The issue's own premise is
  that *the human accepts the pattern, never the model* — but a human-authored
  block on a page the extractor later touches does not survive unless `check:`
  is threaded through four independent places (`types.py:19`,
  `rendering.py:196-220`, the LLM ingest allowlist at
  `extract/__init__.py:251-265` plus a new sanitizer, and
  `prompts/templates/schema.py:9`). That is the same four-point change
  `enforce:` already paid for.

## 6. The real finding: the mechanism is unused, not missing

| Scope | Rules | Carry `enforce:` |
| --- | --- | --- |
| `shared/feedback` | 47 | **0** |
| all live (`feedback` + `reference` + `project`) | 1842 | **1** |
| `shared/_inbox` (staged, awaiting a human) | — | 2 |

The single live user is `yarn-as-canonical-package-manager`. Two more sit in
`_inbox` waiting for a human who has not come.

And the rules themselves mostly have no checkable shape. Reading all 47 live
feedback rules: they are judgment and workflow — *"backup by risk, not
ritual"*, *"deploy after dev validation"*, *"do not revoke trusted access
without explicit request"*, *"measure before designing"*, *"run git commands
yourself"*. The issue's premise that *"several have the same shape ('never X',
'always Y before Z')"* is true only at the level of English phrasing. "Never
revoke access without an explicit request" is a `never`, and no grep or AST
predicate over the repo can evaluate it — the condition is a human intent, not
a token in a file.

Of the 18 rules that name a concrete file extension, nearly all name files in
*other* repos (`image_search.c`, `MainWindow.xaml.cs`, `IndiqueGanheScreen.tsx`)
— a check in this vault has no tree to run against.

## Recommendation

Do not add `check:`. Three smaller moves, in order of evidence:

1. **Close the gap that is real.** `enforce:` only fires for
   `tool == "Bash"` (`hooks/pre_tool_use.py:66`). `Edit`/`Write` get advisory
   enrichment and can never block. The encoding rule is exactly an Edit/Write
   rule — *that* is why #255 needed a test instead of an `enforce:` block. A
   `deny_pattern` over the **content being written** by Edit/Write is the one
   missing capability, and it reuses the whole existing schema, index, doctor
   and audit surface. It is also where the false-positive risk must be faced
   honestly: blocking a write on a line-oriented regex has the same 22/0
   precision problem measured above, so it should warn before it ever blocks.

2. **Raise adoption before adding surface.** 1 of 1842 is a discovery and
   authoring problem, not a schema problem. `mnemo list-enforced` already
   exists; nothing routes a human to it. The two `_inbox` rules with
   `enforce:` blocks are the backlog — drain those first and see whether the
   mechanism earns its keep.

3. **Leave repo-wide invariants as tests.** `test_text_io_encoding.py`,
   `test_public_api_surface.py` and `test_release_workflow.py` are the
   established pattern for this repo, they run in CI on every commit, and
   they can be tested themselves. That a rule *also* exists in the vault is
   not duplication: the vault rule teaches, the test enforces. #255 is the
   example to copy by hand when a class is worth 279 lines — not a shape to
   generalise into frontmatter.
