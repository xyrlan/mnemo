"""Few-shot examples for the simpler USER and REFERENCE consolidation prompts.

Single example each — these consolidation modes are calibration-light
compared to the seven-example feedback bank.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations


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
