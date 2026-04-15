"""Prompt builders for v0.2 extraction — one per cluster type."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from mnemo.core.extract.scanner import MemoryFile
from mnemo.core.filters import collect_existing_tags

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
    "Input mixing: some input files are session briefings (path contains "
    "`briefings/sessions/`, frontmatter `type: briefing`). Treat them as dense "
    "source material — mine their 'Decisions made' and 'Dead ends' sections "
    "for durable rules, preferences, and architectural conclusions. A single "
    "briefing may yield multiple feedback pages, or none if it contains only "
    "episodic content. Briefings always list their source path in the emitted "
    "`source_files` so the state machine can track them.\n\n"
    "Stability field: every emitted page carries `stability` set to either "
    "\"stable\" or \"evolving\". Emit \"evolving\" only when the source material "
    "shows hesitation, indecision, or active debate — phrases like 'still "
    "deciding', 'not sure', 'tried both', 'changing my mind', 'this might be "
    "wrong', or contradictions between sources. Emit \"stable\" (the default) "
    "for concluded decisions, settled conventions, and factual rules with no "
    "hedging. When in doubt, default to stable.\n\n"
    "Tags field: every emitted page carries `tags`, a list of short kebab-case "
    "topic identifiers (e.g. `[\"git\", \"workflow\"]`, `[\"react\", \"ui\"]`, "
    "`[\"auth\", \"oauth2\"]`). Tags are the ontology a downstream consumer uses "
    "to navigate the vault — cleaner vocabulary is better than more vocabulary. "
    "The user message includes an 'Existing vault tags' list — PREFER reusing "
    "those exact strings over inventing new ones; only invent a new tag when "
    "none of the existing tags fit the rule. Emit 1-3 tags per page; do NOT "
    "emit system markers like 'auto-promoted' or 'needs-review' — those are "
    "managed by the plugin.\n\n"
    "## Optional activation metadata\n\n"
    "If — and ONLY if — the rule body quotes a literal command in backticks "
    "OR names a clearly-regex-able pattern, you MAY emit an `enforce` object "
    "in the page to declare a hard block at the tool-call boundary. Shape: "
    "`{\"tool\": \"Bash\", \"deny_pattern\": \"<regex>\", \"reason\": \"<short>\"}` "
    "or `{\"tool\": \"Bash\", \"deny_command\": [\"<prefix>\"], \"reason\": \"<short>\"}`. "
    "DO NOT emit `enforce` for advisory, stylistic, or taste-based rules. When "
    "in doubt, omit — a missing enforce block is always safe; a wrong one "
    "blocks real work.\n\n"
    "Separately, if the rule is advisory about code in specific files (e.g. "
    "\"HeroUI modal pattern\", \"React key remount\"), you MAY emit an "
    "`activates_on` object listing the tools and file glob patterns where the "
    "rule becomes relevant. Shape: `{\"tools\": [\"Edit\", \"Write\", \"MultiEdit\"], "
    "\"path_globs\": [\"<glob1>\", \"<glob2>\"]}`. Use concrete, narrow globs — "
    "`src/components/modals/**` not `**/*.tsx`. When the rule is about general "
    "coding style with no natural file boundary (e.g. \"prefer clear names\"), "
    "omit `activates_on`.\n\n"
    "Both fields are optional. Most pages should have neither.\n\n"
    "Output MUST be valid JSON matching the requested schema. Do not add "
    "prose before or after the JSON."
)

_TAGS_GUIDANCE_SHORT = (
    " Each page also carries `tags`, a list of 1-3 short kebab-case topic "
    "identifiers. The user message includes an 'Existing vault tags' list — "
    "prefer reusing those exact strings; only invent new tags when none fit. "
    "Do not emit system markers like 'auto-promoted' or 'needs-review'."
)

USER_SYSTEM_PROMPT = (
    "You are consolidating user-profile memories across multiple Claude Code "
    "agents into canonical pages. Group files that describe the SAME trait or "
    "role. Produce one page per trait cluster. Preserve the 'Why' / 'How to "
    "apply' structure." + _TAGS_GUIDANCE_SHORT + " Output MUST be valid JSON "
    "matching the requested schema."
)

REFERENCE_SYSTEM_PROMPT = (
    "You are consolidating reference memories (pointers to external systems "
    "like Linear, Grafana, Notion) across agents into canonical pages. Group "
    "files that point to the SAME external resource. Produce one page per "
    "resource cluster." + _TAGS_GUIDANCE_SHORT + " Output MUST be valid JSON "
    "matching the requested schema."
)

BRIEFING_SYSTEM_PROMPT = (
    "You are writing a shift handoff briefing from a Claude Code session "
    "transcript. Metaphor: a nurse going off shift writing a short note so "
    "the nurse coming on at the next shift can pick up without losing the "
    "thread. The reader is a tired developer (or another AI agent) resuming "
    "tomorrow — they need both episodic context (where you stopped) and "
    "durable decisions (what you concluded, and why).\n\n"
    "Output MUST be markdown ONLY (no frontmatter — the caller adds it). "
    "Use EXACTLY these seven section headers, in this order, each on its "
    "own line, copied byte-for-byte. Do NOT append any description, dash, "
    "or instruction text to the header line itself — write the header, "
    "then newline, then the content underneath.\n\n"
    "Required headers (copy these literal lines into your output):\n"
    "## TL;DR\n"
    "## What I did\n"
    "## Decisions made\n"
    "## Dead ends\n"
    "## Open questions\n"
    "## State at end of session\n"
    "## Context I'd forget otherwise\n\n"
    "What goes under each header:\n"
    "- TL;DR: 3-5 sentences summarizing the session.\n"
    "- What I did: concrete changes grouped by feature, with file paths.\n"
    "- Decisions made: architectural decisions with a **Why:** rationale, "
    "including rejected alternatives when relevant. This is the durable "
    "content that downstream extraction will mine into Tier 2 pages.\n"
    "- Dead ends: what was tried and didn't work, and why.\n"
    "- Open questions: unresolved items.\n"
    "- State at end of session: branch, uncommitted files, test status, "
    "and a **Resume at:** line with a `path:line` pointer and the next action.\n"
    "- Context I'd forget otherwise: things held in working memory that "
    "aren't visible in the code.\n\n"
    "Be specific. Cite file paths. Prefer bullets over prose. Omit a "
    "section entirely (header AND content) if it genuinely has no content — "
    "do not fabricate decisions or dead ends. Do not wrap the output in "
    "code fences."
)


def build_briefing_prompt(events: list[dict]) -> str:
    """Render a list of Claude Code jsonl events into a flat text transcript
    suitable as the user message for the briefing LLM call."""
    lines: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = str(ev.get("type") or "")
        msg = ev.get("message") or {}
        role = str(msg.get("role") or etype or "?")
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text") or ""))
                elif btype == "tool_use":
                    name = block.get("name") or "tool"
                    parts.append(f"[tool_use: {name}]")
                elif btype == "tool_result":
                    preview = str(block.get("content") or "")
                    if len(preview) > 400:
                        preview = preview[:400] + "…"
                    parts.append(f"[tool_result: {preview}]")
            text = "\n".join(p for p in parts if p)
        if not text.strip():
            continue
        lines.append(f"[{role}] {text}")

    transcript = "\n\n".join(lines)
    return (
        "Task: write the shift handoff briefing markdown body for the "
        "following Claude Code session transcript. Follow the section "
        "structure from the system prompt exactly. Output markdown only, "
        "no frontmatter, no code fences.\n\n"
        "=== TRANSCRIPT ===\n"
        f"{transcript}\n"
        "=== END TRANSCRIPT ===\n"
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
      "stability": "stable",
      "tags": ["topic1", "topic2"],
      "enforce": {"tool": "Bash", "deny_pattern": "...", "reason": "..."} | null,
      "activates_on": {"tools": ["Edit"], "path_globs": ["..."]} | null
    }
  ]
}

`enforce` and `activates_on` are OPTIONAL. Omit them (or emit null) for any
rule that doesn't cleanly fit the shapes documented in the system prompt.

`stability` must be either "stable" (default, concluded decision) or "evolving"
(source shows indecision / still debating / contradicting itself). Default to
"stable" when in doubt.

`tags` is a list of 1-3 short kebab-case topic identifiers (e.g. "git",
"workflow", "react", "auth"). Prefer tags from the "Existing vault tags" list
in the user message; only invent a new tag when none of the existing ones fit.
Never emit "auto-promoted" or "needs-review" — those are system markers the
plugin manages.
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


def _existing_tags_fragment(vault_root: Path | None, page_type: str) -> str:
    """Render the controlled-vocabulary hint shown in the user message.

    Per-page-type scope: feedback prompts see only tags from
    ``shared/feedback/``, etc. Keeps vocab domains separate (v0.4 decision).
    Returns an empty string when no vault_root is provided or no tags exist
    yet (fresh vault), so prompts stay valid in test fixtures and first runs.
    """
    if vault_root is None:
        return ""
    existing = collect_existing_tags(vault_root, page_type)
    if not existing:
        return (
            f"Existing vault tags for {page_type}: (none yet — this is an "
            f"early extraction; invent clean kebab-case topics like "
            f"\"git\", \"workflow\", \"auth\").\n\n"
        )
    return (
        f"Existing vault tags for {page_type}: {existing}. Prefer reusing "
        f"these exact strings; only invent a new tag when none of the above fit.\n\n"
    )


def build_feedback_prompt(
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    return (
        "Task: consolidate these FEEDBACK memory files into canonical Tier 2 "
        "pages. Cluster files that express the same conceptual rule.\n\n"
        f"{_existing_tags_fragment(vault_root, 'feedback')}"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{_FEW_SHOT_FEEDBACK}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )


def build_user_prompt(
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    return (
        "Task: consolidate these USER-profile memory files into canonical "
        "Tier 2 pages. Cluster files describing the same user trait.\n\n"
        f"{_existing_tags_fragment(vault_root, 'user')}"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{_FEW_SHOT_USER}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )


def build_reference_prompt(
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    return (
        "Task: consolidate these REFERENCE memory files into canonical Tier 2 "
        "pages. Cluster files that point to the same external resource.\n\n"
        f"{_existing_tags_fragment(vault_root, 'reference')}"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{_FEW_SHOT_REFERENCE}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )
