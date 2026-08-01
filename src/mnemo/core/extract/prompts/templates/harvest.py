"""System prompt for the cold-start backfill harvest call.

Harvest sits between briefing and consolidation: like briefing it reads a raw
Claude Code jsonl transcript, but like consolidation it emits structured pages.
The pages it emits are *memory files* (``bots/<repo>/memory/*.md``), not rules —
the existing extraction pipeline turns those into rules afterwards.

The prompt is deliberately conservative. Backfill reconstructs intent from an
old transcript rather than observing it live, so recall is worth trading for
precision: it is told to emit nothing rather than guess.

Unlike the feedback/user/reference consolidation prompts, this type
classification (four-way, plus a durability judgment) has no prior
LLM-facing prompt in the tree to inherit calibration from — so the few-shot
examples below are load-bearing, not decoration. They live in this module
rather than a separate ``few_shot_harvest.py`` because, unlike consolidation,
harvest has no per-call user-message builder to inject them into
(``build_harvest_prompt`` just wraps a raw transcript) — the system prompt is
the only place they can go.
"""
from __future__ import annotations


_FEW_SHOT_HARVEST = """\
Example 1 — durable feedback:
Transcript excerpt:
USER: Never install packages with npm, this repo uses pnpm exclusively.
ASSISTANT: Got it, I'll use pnpm from now on.

Output:
{"pages":[{"slug":"use-pnpm-not-npm","type":"feedback","name":"Use pnpm, not npm","description":"Package manager preference for this repo","body":"Always use pnpm to install packages here, never npm.\\n\\n**Why:** the user stated this repo uses pnpm exclusively."}]}

Example 2 — episodic, not durable → emit nothing:
Transcript excerpt:
USER: run the tests
ASSISTANT: Running pytest... 42 passed.
USER: great, commit that

Output:
{"pages":[]}

Example 3 — reference vs project, side by side in one transcript:
Transcript excerpt:
USER: heads up, Stripe silently drops webhook events that take over 5 seconds to ack — always ack immediately and process async.
ASSISTANT: noted. Separately: our queue consumer has to stay single-threaded, that's a deliberate constraint from the 2024 race-condition incident.

Output:
{"pages":[{"slug":"stripe-webhook-ack-immediately","type":"reference","name":"Stripe webhook 5s ack timeout","description":"Stripe drops slow-acking webhook events","body":"Acknowledge Stripe webhooks immediately and process the payload asynchronously.\\n\\n**Why:** Stripe silently drops events that take over 5 seconds to ack — this is a property of Stripe's platform, not this codebase."},{"slug":"queue-consumer-single-threaded","type":"project","name":"Queue consumer must stay single-threaded","description":"Deliberate concurrency constraint on this service","body":"The queue consumer in this service must remain single-threaded.\\n\\n**Why:** a 2024 incident showed concurrent consumption caused a race condition; this is now a fixed architectural constraint of this codebase."}]}
"""


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
    "knowledge about one (a third-party API's quirks, a vendor's rate limits, "
    "a gotcha that cost a build cycle). About something outside this codebase.\n"
    "- project — context about this specific repository that is not derivable "
    "from reading its code: architectural intent, constraints, decisions and "
    "the reasoning behind them. About this codebase itself.\n\n"
    "reference and project are easy to conflate: reference is about something "
    "outside this codebase (a vendor API, a hosted service, an external tool); "
    "project is about this codebase itself (its architecture, its constraints, "
    "why it was built the way it was). A note about this repo's own deploy "
    "pipeline is project, not reference, even though 'deploy' sounds external.\n\n"
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
    "taught nothing durable.\n\n"
    f"{_FEW_SHOT_HARVEST}"
)
