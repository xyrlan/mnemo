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
    "structure. Output MUST be valid JSON matching the requested schema. Do not "
    "add prose before or after the JSON."
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
      "source_files": ["bots/<agent>/memory/<file>.md", ...]
    }
  ]
}
"""

_FEW_SHOT_FEEDBACK = """\
Example — TWO input files merging into ONE page:

Input:
[FILE: bots/agent-x/memory/feedback_use_yarn.md]
---
name: Use yarn
type: feedback
---
Always use yarn.
**Why:** yarn.lock is canonical.
**How to apply:** Use `yarn add`.
[END]
[FILE: bots/agent-y/memory/feedback_yarn_only.md]
---
name: Yarn only
type: feedback
---
Never npm.
**Why:** mixing causes lockfile drift.
**How to apply:** use yarn.
[END]

Output:
{"pages":[{"slug":"use-yarn","name":"Use yarn, never npm","description":"JS/TS projects use yarn exclusively","type":"feedback","body":"Use yarn for all JS/TS package management.\\n\\n**Why:** yarn.lock is the canonical lockfile; mixing npm and yarn causes drift.\\n\\n**How to apply:** Use `yarn add <pkg>` or `yarn add -D <pkg>`. Never run npm commands.","source_files":["bots/agent-x/memory/feedback_use_yarn.md","bots/agent-y/memory/feedback_yarn_only.md"]}]}

Example — ONE input file passing through unchanged:

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
{"pages":[{"slug":"solo-rule","name":"Solo rule","description":"A rule only agent-z has","type":"feedback","body":"A rule only agent-z has.\\n\\n**Why:** agent-z specific.\\n\\n**How to apply:** only in agent-z.","source_files":["bots/agent-z/memory/feedback_solo.md"]}]}
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
