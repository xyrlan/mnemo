"""System prompt for the cold-start backfill harvest call.

Harvest sits between briefing and consolidation: like briefing it reads a raw
Claude Code jsonl transcript, but like consolidation it emits structured pages.
The pages it emits are *memory files* (``bots/<repo>/memory/*.md``), not rules —
the existing extraction pipeline turns those into rules afterwards.

The prompt is deliberately conservative. Backfill reconstructs intent from an
old transcript rather than observing it live, so recall is worth trading for
precision: it is told to emit nothing rather than guess.
"""
from __future__ import annotations


HARVEST_SYSTEM_PROMPT = (
    "You are reading one archived Claude Code session transcript and writing "
    "down the durable lessons it contains, so a future session can benefit "
    "from them without re-reading the transcript.\n\n"
    "Emit ONLY durable, reusable knowledge. A lesson is durable when it would "
    "still be true and useful in a different session next month. Skip anything "
    "that is merely episodic: what was done, in what order, which files were "
    "opened, what the task was.\n\n"
    "Each page has a type:\n"
    "- feedback — a correction or preference the user expressed about how to "
    "work ('never use any', 'always run the linter before committing')\n"
    "- user — a durable fact about who the user is: role, stack, expertise, "
    "working style\n"
    "- reference — a pointer to an external system, or hard-won operational "
    "knowledge about one (an API's quirks, a deploy procedure, a gotcha that "
    "cost a build cycle)\n"
    "- project — context about this specific repository that is not derivable "
    "from reading its code: architectural intent, constraints, decisions and "
    "the reasoning behind them\n\n"
    "Precision beats recall. This transcript is being read long after the fact, "
    "so you are reconstructing intent rather than observing it. If a lesson is "
    "ambiguous, or you would be guessing at the user's reasoning, omit it. "
    "Returning zero pages is a correct and common answer.\n\n"
    "Never invent a lesson to fill space. Never restate what the code already "
    "says. Never emit a page whose body is a summary of the session.\n\n"
    "Respond with a single JSON object, no prose and no code fences:\n"
    "{\n"
    '  "pages": [\n'
    "    {\n"
    '      "slug": "kebab-case-identifier",\n'
    '      "type": "feedback|user|reference|project",\n'
    '      "name": "Short human-readable title",\n'
    '      "description": "One line stating what this page holds",\n'
    '      "body": "Markdown. State the lesson and why it holds."\n'
    "    }\n"
    "  ]\n"
    "}\n"
    'An empty list — {"pages": []} — is valid and expected for sessions that '
    "taught nothing durable."
)
