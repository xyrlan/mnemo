"""Few-shot examples for the FEEDBACK consolidation prompt.

Seven examples cover: positive merge, negative no-merge, single-input
passthrough, evolving stability, positive enforce, positive activates_on,
and negative both-fields-null. PR F1 added a regression test
(``tests/unit/test_prompts_few_shot_schema.py``) that round-trips every
``Output:`` JSON blob through the production
``_parse_pages_from_response`` filter — drift here will be caught.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations


_FEW_SHOT_FEEDBACK = """\
Example 1 — POSITIVE: two files with different wording, same rule → MERGE into one page.
(This is the canonical case you must get right. Different agents, different
filenames, different `name:` frontmatter, different phrasing — but the
underlying rule is identical: never create git commits without explicit
user permission.)

Input:
[FILE: bots/central-inteligencia-frontend/memory/feedback_no_commits.md]
---
name: No commits
type: feedback
---
Never run `git commit` on my behalf. Only edit files.
**Why:** I review and stage commits myself.
**How to apply:** edit files but stop before committing.
[END]
[FILE: bots/clubinho/memory/feedback_no_commit_without_permission.md]
---
name: Ask before committing
type: feedback
---
Do not create git commits unless I explicitly ask.
**Why:** commit history is my responsibility.
**How to apply:** wait for the user to say "commit this" before running git commit.
[END]

Output (ONE merged page, both files listed in source_files):
{"pages":[{"slug":"no-commits-without-permission","name":"Never commit without explicit permission","description":"Do not create git commits unless the user explicitly asks","type":"feedback","body":"Never run `git commit` unless the user explicitly asks you to commit.\\n\\n**Why:** the user reviews and owns their commit history; autonomous commits bypass that review.\\n\\n**How to apply:** edit and stage files freely, but stop before running `git commit`. Wait for explicit phrasing like \\"commit this\\" or \\"make a commit\\" before proceeding.","source_files":["bots/central-inteligencia-frontend/memory/feedback_no_commits.md","bots/clubinho/memory/feedback_no_commit_without_permission.md"],"stability":"stable"}]}

Example 2 — NEGATIVE: two files about genuinely different rules → DO NOT MERGE.
(Use yarn and no-commits-without-permission are unrelated rules — different
domain, different tool, different behavior. They must stay separate even
though they are both "feedback" type.)

Input:
[FILE: bots/agent-a/memory/feedback_use_yarn.md]
---
name: Use yarn
type: feedback
---
Always use yarn for JS/TS package management.
**Why:** yarn.lock is canonical in this repo.
**How to apply:** `yarn add <pkg>`, never `npm install`.
[END]
[FILE: bots/agent-b/memory/feedback_no_commits.md]
---
name: No autonomous commits
type: feedback
---
Do not run `git commit` without permission.
**Why:** I own commit history.
**How to apply:** stop before committing.
[END]

Output (TWO separate pages — these rules DO NOT merge):
{"pages":[{"slug":"use-yarn","name":"Use yarn for JS/TS","description":"Always use yarn, never npm","type":"feedback","body":"Always use yarn for JS/TS package management.\\n\\n**Why:** yarn.lock is the canonical lockfile in this repo.\\n\\n**How to apply:** `yarn add <pkg>` for runtime deps, `yarn add -D <pkg>` for dev deps. Never run `npm install`.","source_files":["bots/agent-a/memory/feedback_use_yarn.md"],"stability":"stable"},{"slug":"no-autonomous-commits","name":"No autonomous commits","description":"Do not run git commit without explicit permission","type":"feedback","body":"Do not run `git commit` without explicit user permission.\\n\\n**Why:** the user owns their commit history.\\n\\n**How to apply:** edit and stage freely, but stop before committing.","source_files":["bots/agent-b/memory/feedback_no_commits.md"],"stability":"stable"}]}

Example 3 — ONE input file passing through unchanged:

Input:
[FILE: bots/agent-z/memory/feedback_solo.md]
---
name: Solo rule
type: feedback
---
A rule only agent-z has.
**Why:** agent-z specific.
**How to apply:** only in agent-z.
[END]

Output:
{"pages":[{"slug":"solo-rule","name":"Solo rule","description":"A rule only agent-z has","type":"feedback","body":"A rule only agent-z has.\\n\\n**Why:** agent-z specific.\\n\\n**How to apply:** only in agent-z.","source_files":["bots/agent-z/memory/feedback_solo.md"],"stability":"stable"}]}

Example 4 — EVOLVING stability marker.
(Source hedging language — "still deciding", "might change" — triggers
stability:"evolving". This signals to downstream consumers that the rule is
tentative and may be rewritten in the near future.)

Input:
[FILE: bots/agent-q/memory/feedback_zustand_maybe.md]
---
name: Maybe Zustand
type: feedback
---
Leaning toward Zustand over Redux for state management, still deciding.
Tried both this week and might change my mind next sprint.
**Why:** smaller API surface, but Redux has better devtools.
**How to apply:** use Zustand for new stores, keep existing Redux slices.
[END]

Output (stability marked evolving because of "still deciding" / "might change"):
{"pages":[{"slug":"state-management-zustand-vs-redux","name":"Zustand over Redux (evolving)","description":"Leaning toward Zustand for new state, but still deciding","type":"feedback","body":"Leaning toward Zustand over Redux for new state management.\\n\\n**Why:** smaller API surface than Redux, though Redux has better devtools.\\n\\n**How to apply:** use Zustand for brand-new stores; keep existing Redux slices untouched until the decision settles.","source_files":["bots/agent-q/memory/feedback_zustand_maybe.md"],"stability":"evolving"}]}

Example 5 — POSITIVE enforce: rule quotes a literal bash command → emit enforce.
(A rule that says "never add Co-Authored-By trailers" is exactly the shape
that earns an enforce block: the bad behavior maps to a concrete bash pattern
we can block at the tool-call boundary. NO activates_on — the rule is not
scoped to any particular file.)

Input:
[FILE: bots/agent-c/memory/feedback_no_coauthored.md]
---
name: No Co-Authored-By
type: feedback
---
Never add Co-Authored-By trailers in git commits.
**Why:** the user owns attribution on their commits.
**How to apply:** write commit messages without any `Co-Authored-By:` lines.
[END]

Output:
{"pages":[{"slug":"no-co-authored-by-trailers","name":"No Co-Authored-By trailers","description":"Never add Co-Authored-By trailers to git commits","type":"feedback","body":"Never add `Co-Authored-By:` trailers to git commits.\\n\\n**Why:** the user owns attribution on their own commits.\\n\\n**How to apply:** when running `git commit`, never include a `Co-Authored-By:` line in the message body.","source_files":["bots/agent-c/memory/feedback_no_coauthored.md"],"stability":"stable","tags":["git","workflow"],"enforce":{"tool":"Bash","deny_pattern":"git commit.*Co-Authored-By","reason":"No Co-Authored-By trailers in commits"},"activates_on":null}]}

Example 6 — POSITIVE activates_on: rule about code in specific files → emit activates_on.
(A rule like "HeroUI v3 uses the Drawer slot pattern for modals" is advisory
about a concrete set of files. We emit activates_on with narrow globs so the
hook fires only when the agent is editing modal components. NO enforce —
this is a coding-style hint, not a hard block.)

Input:
[FILE: bots/agent-d/memory/briefings/sessions/2026-04-10_heroui.md]
---
type: briefing
---
## Decisions made
- HeroUI v3 uses the Drawer slot pattern for modals, NOT the old Modal component.
  **Why:** the Modal component was removed in v3; Drawer with `placement="center"` replaces it.
[END]

Output:
{"pages":[{"slug":"heroui-v3-drawer-modal-pattern","name":"HeroUI v3 Drawer modal pattern","description":"Use Drawer slot with placement=center for modals in HeroUI v3","type":"feedback","body":"In HeroUI v3 use the Drawer slot pattern for modals instead of the old Modal component.\\n\\n**Why:** the Modal component was removed in v3; Drawer with `placement=\\"center\\"` replaces it.\\n\\n**How to apply:** when building a modal, import Drawer from @heroui/react and set `placement=\\"center\\"`; never reach for a Modal component.","source_files":["bots/agent-d/memory/briefings/sessions/2026-04-10_heroui.md"],"stability":"stable","tags":["heroui","ui"],"enforce":null,"activates_on":{"tools":["Edit","Write","MultiEdit"],"path_globs":["**/components/modals/**","**/*modal*.tsx"]}}]}

Example 7 — NEGATIVE: stylistic rule with no file boundary → BOTH fields null.
("Prefer descriptive variable names" is exactly the "when in doubt, omit"
case. No literal command to block, no natural file boundary. Emit enforce:null
and activates_on:null — advisory rules like this should NOT activate hooks.)

Input:
[FILE: bots/agent-e/memory/feedback_clear_names.md]
---
name: Clear variable names
type: feedback
---
Prefer descriptive variable names over short cryptic ones.
**Why:** readability beats keystrokes saved.
**How to apply:** pick names that spell out the domain concept.
[END]

Output:
{"pages":[{"slug":"descriptive-variable-names","name":"Descriptive variable names","description":"Prefer descriptive names over short cryptic ones","type":"feedback","body":"Prefer descriptive variable names over short cryptic abbreviations.\\n\\n**Why:** readability beats the keystrokes saved; future readers (including you) pay the cost of cryptic names.\\n\\n**How to apply:** pick names that spell out the domain concept instead of abbreviating.","source_files":["bots/agent-e/memory/feedback_clear_names.md"],"stability":"stable","tags":["code-style"],"enforce":null,"activates_on":null}]}
"""
