"""Prompt builders for v0.2 extraction — one per cluster type."""
from __future__ import annotations

from typing import Iterator

from mnemo.core.extract.scanner import MemoryFile

# --- System prompts ---------------------------------------------------------

FEEDBACK_SYSTEM_PROMPT = (
    "You are helping consolidate feedback/preference memories extracted from "
    "multiple Claude Code agents into canonical Tier 2 pages. Group files that "
    "express the SAME rule or preference across different agents. Produce one "
    "canonical page per conceptual cluster. Preserve the 'Why' and 'How to apply' "
    "structure.\n\n"
    "CRITICAL merge rule: two files from different agents that describe the "
    "same underlying rule or preference MUST be merged into a single page, "
    "even when the wording, filename, or frontmatter name differs. Focus on "
    "the conceptual intent, not surface phrasing. When in doubt, merge — "
    "over-merging conceptually-related rules is preferable to leaving "
    "duplicates in the sacred directory. Only keep files separate when they "
    "address genuinely distinct rules (different domains, different tools, "
    "unrelated behaviors).\n\n"
    "Stability field: every emitted page carries `stability` set to either "
    "\"stable\" or \"evolving\". Emit \"evolving\" only when the source material "
    "shows hesitation, indecision, or active debate — phrases like 'still "
    "deciding', 'not sure', 'tried both', 'changing my mind', 'this might be "
    "wrong', or contradictions between sources. Emit \"stable\" (the default) "
    "for concluded decisions, settled conventions, and factual rules with no "
    "hedging. When in doubt, default to stable.\n\n"
    "Output MUST be valid JSON matching the requested schema. Do not add "
    "prose before or after the JSON."
)

USER_SYSTEM_PROMPT = (
    "You are consolidating user-profile memories across multiple Claude Code "
    "agents into canonical pages. Group files that describe the SAME trait or "
    "role. Produce one page per trait cluster. Preserve the 'Why' / 'How to "
    "apply' structure. Output MUST be valid JSON matching the requested schema."
)

REFERENCE_SYSTEM_PROMPT = (
    "You are consolidating reference memories (pointers to external systems "
    "like Linear, Grafana, Notion) across agents into canonical pages. Group "
    "files that point to the SAME external resource. Produce one page per "
    "resource cluster. Output MUST be valid JSON matching the requested schema."
)

# --- Shared fragments -------------------------------------------------------

_SCHEMA_EXAMPLE = """\
Required JSON output schema:
{
  "pages": [
    {
      "slug": "short-kebab-case-identifier",
      "name": "Human-readable title",
      "description": "One-line summary",
      "type": "feedback|user|reference",
      "body": "Markdown body including **Why:** and **How to apply:** sections",
      "source_files": ["bots/<agent>/memory/<file>.md", ...],
      "stability": "stable"
    }
  ]
}

`stability` must be either "stable" (default, concluded decision) or "evolving"
(source shows indecision / still debating / contradicting itself). Default to
"stable" when in doubt.
"""

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
"""

_FEW_SHOT_USER = """\
Example:
Input:
[FILE: bots/a/memory/user_senior_go.md]
---
name: Senior Go
type: user
---
Senior Go developer.
**Why:** career background.
**How to apply:** frame explanations for someone fluent in Go idioms.
[END]

Output:
{"pages":[{"slug":"senior-go-developer","name":"Senior Go developer","description":"Background: senior Go","type":"user","body":"The user is a senior Go developer.\\n\\n**Why:** established career background.\\n\\n**How to apply:** frame explanations using Go idioms as baseline.","source_files":["bots/a/memory/user_senior_go.md"]}]}
"""

_FEW_SHOT_REFERENCE = """\
Example:
Input:
[FILE: bots/a/memory/reference_linear.md]
---
name: Linear ingest
type: reference
---
Pipeline bugs tracked in Linear project INGEST.
**Why:** triage process.
**How to apply:** file pipeline bugs there.
[END]

Output:
{"pages":[{"slug":"linear-ingest-project","name":"Linear INGEST project","description":"Pipeline bug tracker","type":"reference","body":"Pipeline bugs are tracked in the Linear project INGEST.\\n\\n**Why:** dedicated triage queue for pipeline issues.\\n\\n**How to apply:** file new pipeline bugs against the INGEST project.","source_files":["bots/a/memory/reference_linear.md"]}]}
"""

# --- Chunking ---------------------------------------------------------------


def chunks_for(files: list[MemoryFile], chunk_size: int) -> Iterator[list[MemoryFile]]:
    for i in range(0, len(files), chunk_size):
        yield files[i:i + chunk_size]


# --- File encoding for prompts ----------------------------------------------


def _encode_file(f: MemoryFile) -> str:
    fm_lines = [f"{k}: {v}" for k, v in f.frontmatter.items()]
    fm_block = "\n".join(fm_lines) if fm_lines else f"type: {f.type}"
    return (
        f"<<<FILE: {f.path}>>>\n"
        f"---\n{fm_block}\n---\n"
        f"{f.body}\n"
        f"<<<END>>>\n"
    )


def _render_files(files: list[MemoryFile]) -> str:
    return "\n".join(_encode_file(f) for f in files)


# --- Builders ---------------------------------------------------------------


def build_feedback_prompt(files: list[MemoryFile]) -> str:
    return (
        "Task: consolidate these FEEDBACK memory files into canonical Tier 2 "
        "pages. Cluster files that express the same conceptual rule.\n\n"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{_FEW_SHOT_FEEDBACK}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )


def build_user_prompt(files: list[MemoryFile]) -> str:
    return (
        "Task: consolidate these USER-profile memory files into canonical "
        "Tier 2 pages. Cluster files describing the same user trait.\n\n"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{_FEW_SHOT_USER}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )


def build_reference_prompt(files: list[MemoryFile]) -> str:
    return (
        "Task: consolidate these REFERENCE memory files into canonical Tier 2 "
        "pages. Cluster files that point to the same external resource.\n\n"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{_FEW_SHOT_REFERENCE}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )
