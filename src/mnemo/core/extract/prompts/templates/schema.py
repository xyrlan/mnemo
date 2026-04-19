"""Required JSON output schema example shared by all consolidation prompts.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations


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
      "aliases": ["banco", "database"],
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

`aliases` is an OPTIONAL list of 3-8 short lowercase synonym tokens that act
as lexical bridges for the Reflex retrieval system (e.g. PT↔EN pairs or
abbreviations like ["banco", "database", "db"]). Emit aliases when the rule
contains domain terms a developer would naturally search in a different
language or abbreviation; prefer concrete terms (framework names, file types,
commands). Omit the field for generic rules without natural synonyms.
"""
