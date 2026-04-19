"""System prompt for the FEEDBACK consolidation LLM call.

Also defines the two ``_*_SHORT`` fragments shared with USER/REFERENCE
system prompts (see ``system_simple.py``) — those prompts use the short
guidance variants while feedback embeds the long-form rules inline.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations


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
    "Aliases field (v0.8 — optional, strongly encouraged for bilingual/"
    "synonymous rules): every emitted page MAY carry an `aliases` list of "
    "short lowercase tokens that act as synonym bridges for lexical "
    "retrieval. Emit aliases when the rule description or body contains "
    "domain terms that a developer would naturally search in a different "
    "language or abbreviation — e.g. `aliases: [\"banco\", \"database\", "
    "\"db\"]` for a rule about database mocking. Keep aliases to 3-8 tokens "
    "max; prefer concrete terms (framework names, file types, commands) "
    "over vague ones. If the rule is generic and has no natural synonyms, "
    "omit the field.\n\n"
    "Output MUST be valid JSON matching the requested schema. Do not add "
    "prose before or after the JSON."
)

_TAGS_GUIDANCE_SHORT = (
    " Each page also carries `tags`, a list of 1-3 short kebab-case topic "
    "identifiers. The user message includes an 'Existing vault tags' list — "
    "prefer reusing those exact strings; only invent new tags when none fit. "
    "Do not emit system markers like 'auto-promoted' or 'needs-review'."
)

_ALIASES_GUIDANCE_SHORT = (
    " Each page MAY also carry an optional `aliases` list of 3-8 short "
    "lowercase synonym tokens that act as bilingual/abbreviation bridges "
    "for lexical retrieval (e.g. `[\"banco\", \"database\", \"db\"]`). "
    "Emit aliases when the rule contains domain terms a developer would "
    "naturally search in a different language or abbreviation; prefer "
    "concrete terms (framework names, file types, commands). Omit the "
    "field for generic rules without natural synonyms."
)
