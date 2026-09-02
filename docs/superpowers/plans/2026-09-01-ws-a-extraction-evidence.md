# WS-A — Extraction with Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feedback rules reach `shared/feedback/` only when they carry a verbatim user quote that verifies against the session transcript; extraction reinforces existing rules instead of minting duplicates; prompt-echo and PII never reach the vault; a one-time `mnemo reclassify` grades the existing vault under the same rules.

**Architecture:** The briefing LLM call (one per session, already exists) gets the user's verbatim turns as input and must emit a `## Corrections` section whose quotes are checked mechanically against those turns. Feedback extraction must cite one of those quotes as `evidence`; `core/extract/evidence.py` verifies it against the briefing and demotes anything unverified to a staged `reference` page. A weighted-Jaccard similarity pass in `inbox/dedup.py` redirects new pages onto existing slugs so `source_count` accrues. Guards (`guards.py`, `redact.py`) run between parsing and apply. `core/reclassify.py` reuses the same verification to grade the legacy vault, with a manifest for byte-exact undo.

**Tech Stack:** Python 3.8+ stdlib only (project rule: zero third-party deps), pytest, existing `mnemo.core.llm.call` for Haiku via the `claude` CLI.

**Spec:** `docs/superpowers/specs/2026-09-01-corrections-layer-design.md` §A. One deviation, recorded here: the briefing's verified corrections are stored as the `## Corrections` markdown section (source of truth) plus a `corrections: N` count in frontmatter — not as a frontmatter list of dicts, because `scanner._parse_frontmatter` is a flat `key: value` reader and `filters.parse_frontmatter` supports only one nesting level. `core/corrections.py` owns the section format so nothing else re-parses prose.

**Conventions in this repo you must follow:**
- Branch is `feat/ws-a-extraction-evidence` (already exists, spec committed there). Commit after every task with the trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013VXyVCP49gfghb87UHLCyD`.
- Run tests with `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider <path>`. Full suite: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider` (≈60 s, expect `2047 passed, 2 skipped` before this plan; the count grows as you add tests).
- LLM calls are stubbed in tests via `monkeypatch.setattr(llm_mod, "call", fake)`; see `tests/unit/test_briefing.py` for the `stub_llm` fixture shape and `_fake_llm_response`.
- Frontmatter written by `inbox/rendering._render_page` must stay parseable by `filters.parse_frontmatter` (one nesting level, 2-space subkeys). Use `_render_nested_block` for dict fields.
- Never hand-edit generated files (`.claude-plugin/*.json`, `npm/*`); this plan does not touch them.

---

## File map

| File | Responsibility | Change |
|---|---|---|
| `src/mnemo/core/transcript.py` | flatten jsonl → text | add `user_turns()`, move `_SYNTHETIC` + `plain_user_text()` here |
| `src/mnemo/core/mcp/recall_sessions.py` | recall harness | import the moved helpers |
| `src/mnemo/core/corrections.py` | **new** — parse/verify/render the `## Corrections` section | new |
| `src/mnemo/core/extract/prompts/templates/briefing.py` | briefing system prompt | eighth section |
| `src/mnemo/core/extract/prompts/render.py` | prompt builders | `build_briefing_prompt(transcript, user_turns=)`, existing-rules fragment in `build_consolidation_prompt` |
| `src/mnemo/core/briefing.py` | briefing generation | pass user turns, verify corrections, log rejects, `corrections:` count |
| `src/mnemo/core/extract/inbox/types.py` | `ExtractedPage` | `evidence`, `confidence`, `unverified_feedback` |
| `src/mnemo/core/extract/__init__.py` | orchestration | parse `evidence`, run verify → guards → redact, new summary counters |
| `src/mnemo/core/extract/inbox/rendering.py` | page frontmatter | write `confidence:`, `evidence:`, `demoted_from:` |
| `src/mnemo/core/extract/evidence.py` | **new** — verify a page's quote against its source briefing | new |
| `src/mnemo/core/extract/inbox/paths.py` | routing | unverified feedback → `_inbox/reference/` |
| `src/mnemo/core/extract/inbox/apply.py` | dispatch | exclude unverified from universal promotion; similarity redirect |
| `src/mnemo/core/extract/inbox/dedup.py` | drift guards | `SimilarityIndex` + `_detect_similar_existing` |
| `src/mnemo/core/extract/prompts/existing_rules.py` | **new** — "Existing rules" fragment | new |
| `src/mnemo/core/extract/prompts/templates/system_feedback.py` | feedback system prompt | evidence + slug-reuse paragraphs |
| `src/mnemo/core/extract/prompts/templates/schema.py` | JSON schema example | `evidence` field |
| `src/mnemo/core/extract/prompts/templates/few_shot_feedback.py` | few-shot | Example 1 becomes a briefing-with-Corrections → page-with-evidence |
| `src/mnemo/core/extract/guards.py` | **new** — prompt-echo rejection | new |
| `src/mnemo/core/redact.py` | **new** — PII redaction | new |
| `src/mnemo/core/reflex/index.py`, `bm25.py`, `core/config.py` | retrieval | `evidence` field, weight 2.5 |
| `src/mnemo/core/reclassify.py` | **new** — legacy vault grading with manifest/undo | new |
| `src/mnemo/cli/commands/reclassify.py`, `cli/parser.py`, `cli/commands/__init__.py` | CLI | `mnemo reclassify` |
| `docs/configuration.md`, `CHANGELOG.md` | docs | new keys, unreleased notes |

---

### Task 1: `transcript.user_turns` (shared machine-turn filter)

**Files:**
- Modify: `src/mnemo/core/transcript.py`
- Modify: `src/mnemo/core/mcp/recall_sessions.py:47-60,190-208`
- Test: `tests/unit/test_transcript_user_turns.py`

- [ ] **Step 1: Write the failing test**

```python
"""core/transcript.user_turns — the user's own words, nothing the harness wrote."""
from __future__ import annotations

from mnemo.core import transcript


def _user(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


def _assistant(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def test_user_turns_keeps_only_human_text_in_order():
    events = [
        _user("add a retry helper"),
        _assistant("done"),
        _user([{"type": "text", "text": "no — never retry on 4xx, only on 5xx"}]),
        _user([{"type": "tool_result", "content": "ok"}]),          # plumbing
        _user("<task-notification>build finished</task-notification>"),  # harness
        _user("<local-command-stdout>foo</local-command-stdout>"),        # slash cmd
        {"type": "user", "message": "not-a-dict"},                        # malformed
        "garbage",
    ]
    assert transcript.user_turns(events) == [
        "add a retry helper",
        "no — never retry on 4xx, only on 5xx",
    ]


def test_user_turns_empty_when_no_events():
    assert transcript.user_turns([]) == []


def test_recall_sessions_reuses_transcript_filter():
    from mnemo.core.mcp import recall_sessions
    assert recall_sessions._SYNTHETIC is transcript.SYNTHETIC_TURN
    assert recall_sessions._plain_text is transcript.plain_user_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_transcript_user_turns.py`
Expected: FAIL with `AttributeError: module 'mnemo.core.transcript' has no attribute 'user_turns'`

- [ ] **Step 3: Move the filter into `transcript.py` and add `user_turns`**

Append to `src/mnemo/core/transcript.py` (after the imports add `import re` and `from typing import Any, Optional`):

```python
# `user` turns that nobody typed. Slash-command output, hook context and
# background-task notifications are all replayed into the transcript as user
# messages, and a tool_result block is the transcript's own plumbing. Both the
# recall harness and the briefing's corrections pass must ignore them, so the
# pattern lives here and both import it.
SYNTHETIC_TURN = re.compile(
    r"<(local-command-stdout|local-command-caveat|command-name|command-message"
    r"|command-args|system-reminder|task-notification|task-id|tool-use-id"
    r"|output-file|user-prompt-submit-hook)>"
    r"|^Caveat: The messages below were generated by the user",
    re.I | re.M,
)


def plain_user_text(content: Any) -> Optional[str]:
    """Human-typed text out of a message body, or None when there is none.

    A list body carrying ``tool_result`` blocks is a transcript mechanism
    wearing a user turn, so it yields nothing rather than its text.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            return None  # tool_result and friends: not a prompt at all
        parts.append(str(block.get("text") or ""))
    text = " ".join(parts).strip()
    return text or None


def user_turns(events: list) -> list[str]:
    """The user's own turns, verbatim and in order.

    Everything the harness replays as a ``user`` message (task notifications,
    hook context, slash-command output, tool results) is excluded — a
    correction can only be evidenced by words the person actually typed.
    """
    out: list[str] = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "user":
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        text = plain_user_text(msg.get("content"))
        if text is None or SYNTHETIC_TURN.search(text):
            continue
        out.append(text)
    return out
```

In `src/mnemo/core/mcp/recall_sessions.py` delete the `_SYNTHETIC = re.compile(...)` block (lines 47-60) and the `_plain_text` function (lines 190-208), and add near the other imports:

```python
from mnemo.core.transcript import SYNTHETIC_TURN as _SYNTHETIC
from mnemo.core.transcript import plain_user_text as _plain_text
```

Keep the `import re` only if something else in the file still uses it (`grep -n "re\." src/mnemo/core/mcp/recall_sessions.py`); remove it otherwise.

- [ ] **Step 4: Run the new test and the recall-sessions tests**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_transcript_user_turns.py tests/unit/test_recall_sessions.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/transcript.py src/mnemo/core/mcp/recall_sessions.py tests/unit/test_transcript_user_turns.py
git commit -m "feat(transcript): user_turns() with the shared machine-turn filter"
```

---

### Task 2: `core/corrections.py` — parse, verify, render the Corrections section

**Files:**
- Create: `src/mnemo/core/corrections.py`
- Test: `tests/unit/test_corrections.py`

- [ ] **Step 1: Write the failing test**

```python
"""core/corrections — the ## Corrections section is only ever the user's words."""
from __future__ import annotations

from mnemo.core import corrections as C

BODY = """## TL;DR
Did stuff.

## Decisions made
- Used axios. **Why:** already a dependency.

## Corrections
- "never retry on 4xx, only on 5xx" → Retry only 5xx responses
- "use   YARN  not npm" → Use yarn for package management
- "this quote was invented" → Invented rule
- not a quoted item at all

## Dead ends
- tried fetch.
"""

TURNS = [
    "add a retry helper",
    "no — never retry on 4xx, only on 5xx. and use yarn not npm",
]


def test_parse_section_reads_quoted_items_only():
    items = C.parse_section(BODY)
    assert [i.quote for i in items] == [
        "never retry on 4xx, only on 5xx",
        "use   YARN  not npm",
        "this quote was invented",
    ]
    assert items[0].rule == "Retry only 5xx responses"


def test_parse_section_absent_returns_empty():
    assert C.parse_section("## TL;DR\nnothing\n") == []


def test_verify_keeps_substring_matches_case_and_space_insensitive():
    kept, rejected = C.verify(C.parse_section(BODY), TURNS)
    assert [k.quote for k in kept] == [
        "never retry on 4xx, only on 5xx",
        "use   YARN  not npm",
    ]
    assert [r.quote for r in rejected] == ["this quote was invented"]


def test_verify_rejects_quotes_too_short_to_mean_anything():
    items = [C.Correction(quote="ok", rule="Say ok")]
    kept, rejected = C.verify(items, ["ok then"])
    assert kept == [] and rejected == items


def test_quote_matches_turn_normalises_curly_quotes_and_whitespace():
    assert C.quote_matches_turn("“Use  yarn”", "use yarn not npm")
    assert not C.quote_matches_turn("use pnpm", "use yarn not npm")


def test_replace_section_rewrites_only_verified_items_after_decisions():
    kept, _ = C.verify(C.parse_section(BODY), TURNS)
    out = C.replace_section(BODY, kept)
    assert "this quote was invented" not in out
    assert out.index("## Decisions made") < out.index("## Corrections") < out.index("## Dead ends")
    assert '- "never retry on 4xx, only on 5xx" → Retry only 5xx responses' in out


def test_replace_section_with_no_items_removes_the_section():
    out = C.replace_section(BODY, [])
    assert "## Corrections" not in out
    assert "## Dead ends" in out


def test_replace_section_appends_when_no_decisions_header():
    body = "## TL;DR\nx\n"
    out = C.replace_section(body, [C.Correction(quote="use yarn not npm", rule="Use yarn")])
    assert out.rstrip().endswith('- "use yarn not npm" → Use yarn')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_corrections.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.corrections'`

- [ ] **Step 3: Write the module**

Create `src/mnemo/core/corrections.py`:

```python
"""The ``## Corrections`` section of a session briefing.

A correction is the user telling Claude to stop, change, prefer, or
never/always do something. The briefing LLM proposes items as
``- "<verbatim quote>" → <rule>``; this module is the only reader and writer of
that format, and :func:`verify` is the mechanical check that the quote really
is a substring of something the user typed. A fabricated quote never reaches
disk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_HEADER = "## Corrections"
ARROW = "→"
# Shorter than this and a quote matches by accident ("ok", "yes", "no").
MIN_QUOTE_CHARS = 12

_HEADER_RE = re.compile(r"^## ", re.M)
_ITEM_RE = re.compile(
    r"""^\s*[-*]\s+["“](?P<quote>.+?)["”]\s*(?:→|->)\s*(?P<rule>.+?)\s*$"""
)
_QUOTE_CHARS = "\"'“”‘’"


@dataclass(frozen=True)
class Correction:
    quote: str
    rule: str


def normalize(text: str) -> str:
    """Whitespace-collapsed, dequoted, lower-cased form used for matching."""
    return re.sub(r"\s+", " ", text).strip().strip(_QUOTE_CHARS).strip().lower()


def quote_matches_turn(quote: str, turn: str) -> bool:
    q = normalize(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(turn)


def _section_span(markdown: str) -> tuple[int, int] | None:
    start = markdown.find(SECTION_HEADER)
    if start == -1:
        return None
    # Section runs until the next "## " header or end of text.
    nxt = _HEADER_RE.search(markdown, start + len(SECTION_HEADER))
    end = nxt.start() if nxt else len(markdown)
    return start, end


def parse_section(markdown: str) -> list[Correction]:
    span = _section_span(markdown)
    if span is None:
        return []
    out: list[Correction] = []
    for line in markdown[span[0]:span[1]].splitlines():
        m = _ITEM_RE.match(line)
        if m:
            out.append(Correction(quote=m.group("quote").strip(), rule=m.group("rule").strip()))
    return out


def verify(
    items: list[Correction], user_turns: list[str],
) -> tuple[list[Correction], list[Correction]]:
    """Split items into (kept, rejected) by whether the quote was really typed."""
    kept: list[Correction] = []
    rejected: list[Correction] = []
    for item in items:
        if any(quote_matches_turn(item.quote, t) for t in user_turns):
            kept.append(item)
        else:
            rejected.append(item)
    return kept, rejected


def render_section(items: list[Correction]) -> str:
    lines = [SECTION_HEADER]
    for it in items:
        lines.append(f'- "{it.quote}" {ARROW} {it.rule}')
    return "\n".join(lines) + "\n"


def strip_section(markdown: str) -> str:
    span = _section_span(markdown)
    if span is None:
        return markdown
    return (markdown[:span[0]].rstrip("\n") + "\n\n" + markdown[span[1]:].lstrip("\n")).strip("\n") + "\n"


def replace_section(markdown: str, items: list[Correction]) -> str:
    """Rewrite the section with exactly *items*; remove it when empty.

    Placed right after the ``## Decisions made`` section when present,
    otherwise appended at the end.
    """
    base = strip_section(markdown)
    if not items:
        return base
    block = render_section(items)
    anchor = base.find("## Decisions made")
    if anchor == -1:
        return base.rstrip("\n") + "\n\n" + block
    nxt = _HEADER_RE.search(base, anchor + 1)
    insert_at = nxt.start() if nxt else len(base)
    head = base[:insert_at].rstrip("\n") + "\n\n"
    tail = base[insert_at:]
    return head + block + ("\n" + tail if tail else "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_corrections.py`
Expected: 8 passed. If `test_replace_section_rewrites_only_verified_items_after_decisions` fails on ordering, print `out` and adjust the `head`/`tail` join — the required shape is `...Decisions made section\n\n## Corrections\n- ...\n\n## Dead ends...`.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/corrections.py tests/unit/test_corrections.py
git commit -m "feat(corrections): parse, verify and render the briefing Corrections section"
```

---

### Task 3: Briefing emits verified corrections

**Files:**
- Modify: `src/mnemo/core/extract/prompts/templates/briefing.py`
- Modify: `src/mnemo/core/extract/prompts/render.py:109-128` (`build_briefing_prompt`)
- Modify: `src/mnemo/core/briefing.py:96-190`
- Test: `tests/unit/test_briefing_corrections.py`

- [ ] **Step 1: Write the failing test**

```python
"""Briefing pipeline: user turns go in, only verified corrections come out."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import briefing as briefing_mod
from mnemo.core import llm as llm_mod
from mnemo.core.extract import prompts
from mnemo.core.extract.prompts.templates.briefing import BRIEFING_SYSTEM_PROMPT


def _events():
    return [
        {"type": "user", "timestamp": "2026-09-01T10:00:00.000Z",
         "message": {"role": "user", "content": "add a retry helper"}},
        {"type": "assistant", "timestamp": "2026-09-01T10:01:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write", "input": {"file_path": "retry.py"}}]}},
        {"type": "user", "timestamp": "2026-09-01T10:02:00.000Z",
         "message": {"role": "user", "content": "no — never retry on 4xx, only on 5xx"}},
        {"type": "user", "timestamp": "2026-09-01T10:03:00.000Z",
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
    ]


LLM_BODY = (
    "## TL;DR\nRetry helper.\n\n"
    "## Decisions made\n- axios interceptor. **Why:** exists.\n\n"
    "## Corrections\n"
    '- "never retry on 4xx, only on 5xx" → Retry only on 5xx\n'
    '- "always use tabs" → Use tabs\n\n'
    "## Dead ends\n- none\n"
)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    return root


def _write_jsonl(path: Path) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in _events()) + "\n")
    return path


def test_system_prompt_defines_corrections_section():
    assert "## Corrections" in BRIEFING_SYSTEM_PROMPT
    assert "verbatim" in BRIEFING_SYSTEM_PROMPT.lower()


def test_prompt_lists_numbered_user_turns_without_machine_turns():
    text = prompts.build_briefing_prompt("[user] hi", user_turns=["add a retry helper", "no — never retry"])
    assert "=== USER TURNS" in text
    assert "[1] add a retry helper" in text
    assert "[2] no — never retry" in text


def test_prompt_truncates_long_turns_to_600_chars():
    text = prompts.build_briefing_prompt("x", user_turns=["a" * 700])
    assert "a" * 600 + "…" in text
    assert "a" * 601 not in text


def test_briefing_keeps_verified_and_drops_fabricated(vault: Path, tmp_path: Path, monkeypatch):
    captured = {}

    def fake_call(prompt, *, system, model, timeout):
        captured["prompt"] = prompt
        return llm_mod.LLMResponse(text=LLM_BODY, total_cost_usd=0.0, input_tokens=1,
                                   output_tokens=1, api_key_source="none", raw={})
    monkeypatch.setattr(llm_mod, "call", fake_call)
    logged = []
    monkeypatch.setattr("mnemo.core.errors.log_error", lambda root, where, exc: logged.append((where, str(exc))))

    jsonl = _write_jsonl(tmp_path / "sess1.jsonl")
    out = briefing_mod.generate_session_briefing(jsonl, "proj", {"extraction": {}})
    text = out.read_text()

    assert "[1] add a retry helper" in captured["prompt"]
    assert "task-notification" not in captured["prompt"].split("=== USER TURNS")[1]
    assert '"never retry on 4xx, only on 5xx" → Retry only on 5xx' in text
    assert "always use tabs" not in text
    assert "corrections: 1\n" in text.split("---")[1]
    assert logged and logged[0][0] == "briefing.corrections_rejected"


def test_briefing_without_corrections_has_no_section_and_zero_count(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: llm_mod.LLMResponse(
        text="## TL;DR\nx\n", total_cost_usd=0.0, input_tokens=1, output_tokens=1,
        api_key_source="none", raw={}))
    jsonl = _write_jsonl(tmp_path / "sess2.jsonl")
    text = briefing_mod.generate_session_briefing(jsonl, "proj", {"extraction": {}}).read_text()
    assert "## Corrections" not in text
    assert "corrections: 0\n" in text.split("---")[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_briefing_corrections.py`
Expected: FAIL — `test_system_prompt_defines_corrections_section` (no header), `build_briefing_prompt() got an unexpected keyword argument 'user_turns'`.

- [ ] **Step 3: Extend the system prompt**

In `src/mnemo/core/extract/prompts/templates/briefing.py` change the header list and its guidance. Replace the block from `"Use EXACTLY these seven section headers"` through the end of the `What goes under each header` list with:

```python
    "Use EXACTLY these eight section headers, in this order, each on its "
    "own line, copied byte-for-byte. Do NOT append any description, dash, "
    "or instruction text to the header line itself — write the header, "
    "then newline, then the content underneath.\n\n"
    "Required headers (copy these literal lines into your output):\n"
    "## TL;DR\n"
    "## What I did\n"
    "## Decisions made\n"
    "## Corrections\n"
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
    "- Corrections: every place the USER told you to stop, change, prefer, "
    "or never/always do something. One bullet per correction, in this exact "
    "shape: `- \"<verbatim quote from a USER TURN>\" → <one-line rule it "
    "establishes>`. The quote must be copied character-for-character from "
    "the numbered USER TURNS block in the user message — never paraphrase, "
    "never quote your own words. Things you explained, decided alone, or "
    "inferred are NOT corrections. Omit the section (header and all) when "
    "the user corrected nothing.\n"
    "- Dead ends: what was tried and didn't work, and why.\n"
    "- Open questions: unresolved items.\n"
    "- State at end of session: branch, uncommitted files, test status, "
    "and a **Resume at:** line with a `path:line` pointer and the next action.\n"
    "- Context I'd forget otherwise: things held in working memory that "
    "aren't visible in the code.\n\n"
```

- [ ] **Step 4: Extend `build_briefing_prompt`**

In `src/mnemo/core/extract/prompts/render.py` replace `build_briefing_prompt`:

```python
_USER_TURN_MAX_CHARS = 600


def build_briefing_prompt(transcript: str, *, user_turns: list[str] | None = None) -> str:
    """Render a briefing prompt from a pre-flattened transcript string.

    ``user_turns`` are the person's own messages, numbered, so the model can
    quote a correction verbatim and the caller can verify it did.
    """
    turns_block = ""
    if user_turns:
        lines = []
        for i, turn in enumerate(user_turns, 1):
            t = turn if len(turn) <= _USER_TURN_MAX_CHARS else turn[:_USER_TURN_MAX_CHARS] + "…"
            lines.append(f"[{i}] {t}")
        turns_block = (
            "=== USER TURNS (verbatim, numbered — quote these for Corrections) ===\n"
            + "\n".join(lines)
            + "\n=== END USER TURNS ===\n\n"
        )
    return (
        "Task: write the shift handoff briefing markdown body for the "
        "following Claude Code session transcript. Follow the section "
        "structure from the system prompt exactly. Output markdown only, "
        "no frontmatter, no code fences.\n\n"
        f"{turns_block}"
        "=== TRANSCRIPT ===\n"
        f"{transcript}\n"
        "=== END TRANSCRIPT ===\n"
    )
```

Check that `prompts/__init__.py` re-exports `build_briefing_prompt` from `render` (it does today: `grep -n build_briefing_prompt src/mnemo/core/extract/prompts/__init__.py`).

- [ ] **Step 5: Wire verification into `generate_session_briefing`**

In `src/mnemo/core/briefing.py`:

Add imports at top: `from mnemo.core import corrections as corrections_mod`, `from mnemo.core import errors as errors_mod`, and `from mnemo.core.transcript import flatten_transcript_events, user_turns` (the first is already imported — extend that line).

Change `_render_briefing` signature to add `corrections: int` and write it in frontmatter after `duration_minutes`:

```python
        f"duration_minutes: {duration_minutes}\n"
        f"corrections: {corrections}\n"
        "---\n\n"
```

In `generate_session_briefing`, replace

```python
    transcript = flatten_transcript_events(events)
    prompt_text = prompts.build_briefing_prompt(transcript)
```

with

```python
    transcript = flatten_transcript_events(events)
    turns = user_turns(events)
    prompt_text = prompts.build_briefing_prompt(transcript, user_turns=turns)
```

and after `body = (response.text or "").strip() or "*(empty briefing — LLM returned no content)*"` add:

```python
    vault_root = paths.vault_root(cfg)
    proposed = corrections_mod.parse_section(body)
    kept, rejected = corrections_mod.verify(proposed, turns)
    body = corrections_mod.replace_section(body, kept)
    if rejected:
        errors_mod.log_error(
            vault_root,
            "briefing.corrections_rejected",
            ValueError(f"{len(rejected)} correction quote(s) not found in user turns; dropped"),
        )
```

Delete the later `vault_root = paths.vault_root(cfg)` line (it is now assigned earlier) and pass `corrections=len(kept)` to `_render_briefing`.

- [ ] **Step 6: Run the new test plus every briefing test**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_briefing_corrections.py tests/unit/test_briefing.py tests/unit/test_briefing_logs_telemetry.py tests/unit/test_briefing_picker.py tests/unit/test_session_end_briefing_canonical.py tests/unit/test_extract_prompts.py`
Expected: all PASS. If an existing briefing test asserts the exact old frontmatter, update its expectation to include `corrections: 0`.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/briefing.py src/mnemo/core/extract/prompts/render.py src/mnemo/core/extract/prompts/templates/briefing.py tests/unit/test_briefing_corrections.py tests/unit/test_briefing.py
git commit -m "feat(briefing): verified Corrections section from the user's verbatim turns"
```

---

### Task 4: `ExtractedPage.evidence` / `confidence` — parse and render

**Files:**
- Modify: `src/mnemo/core/extract/inbox/types.py`
- Modify: `src/mnemo/core/extract/__init__.py:175-226` (`_parse_pages_from_response`) and add `_sanitize_llm_evidence`
- Modify: `src/mnemo/core/extract/inbox/rendering.py:_render_page`
- Test: `tests/unit/test_extract_evidence_fields.py`

- [ ] **Step 1: Write the failing test**

```python
"""ExtractedPage.evidence/confidence round-trip: LLM JSON → page → frontmatter → parse."""
from __future__ import annotations

import json

from mnemo.core.extract import _parse_pages_from_response
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.filters import parse_frontmatter


def _page_json(**extra):
    base = {"slug": "retry-5xx-only", "name": "Retry only on 5xx", "description": "d",
            "type": "feedback", "body": "Retry only 5xx.\n\n**Why:** x\n\n**How to apply:** y",
            "source_files": ["bots/proj/briefings/sessions/s1.md"]}
    base.update(extra)
    return json.dumps({"pages": [base]})


def test_parse_reads_evidence_dict_and_defaults_confidence_inferred():
    pages = _parse_pages_from_response(_page_json(
        evidence={"quote": "never retry on 4xx, only on 5xx",
                  "source": "bots/proj/briefings/sessions/s1.md"}), "feedback")
    assert pages[0].evidence == {"quote": "never retry on 4xx, only on 5xx",
                                 "source": "bots/proj/briefings/sessions/s1.md"}
    assert pages[0].confidence == "inferred"
    assert pages[0].unverified_feedback is False


def test_parse_drops_malformed_evidence():
    assert _parse_pages_from_response(_page_json(evidence="a string"), "feedback")[0].evidence is None
    assert _parse_pages_from_response(_page_json(evidence={"quote": ""}), "feedback")[0].evidence is None
    assert _parse_pages_from_response(_page_json(evidence={"quote": "q", "source": 3}), "feedback")[0].evidence is None
    assert _parse_pages_from_response(_page_json(), "feedback")[0].evidence is None


def test_render_writes_confidence_and_nested_evidence_that_parse_back():
    page = ExtractedPage(slug="s", type="feedback", name="N", description="D", body="B",
                         source_files=["bots/p/briefings/sessions/x.md"], source_hash="h",
                         evidence={"quote": 'use "yarn": not npm', "source": "bots/p/briefings/sessions/x.md"},
                         confidence="verified")
    fm = parse_frontmatter(_render_page(page, run_id="r1", auto_promoted=True))
    assert fm["confidence"] == "verified"
    assert fm["evidence"]["quote"] == 'use "yarn": not npm'
    assert fm["evidence"]["source"] == "bots/p/briefings/sessions/x.md"
    assert "demoted_from" not in fm


def test_render_marks_demoted_pages():
    page = ExtractedPage(slug="s", type="reference", name="N", description="D", body="B",
                         source_files=["a.md"], source_hash="h", confidence="inferred",
                         unverified_feedback=True)
    fm = parse_frontmatter(_render_page(page, run_id="r1"))
    assert fm["confidence"] == "inferred"
    assert fm["demoted_from"] == "feedback"
    assert "evidence" not in fm
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_evidence_fields.py`
Expected: FAIL with `AttributeError: 'ExtractedPage' object has no attribute 'evidence'`

- [ ] **Step 3: Add the fields**

In `src/mnemo/core/extract/inbox/types.py`, after `origin_backfill: bool = False` inside `ExtractedPage`:

```python
    # The user quote this rule was built from, as ``{"quote": str, "source":
    # "<vault-relative briefing path>"}``. Only feedback pages carry one.
    # ``extract/evidence.verify_page`` checks it against the source briefing's
    # ``## Corrections`` section and sets ``confidence``.
    evidence: dict | None = None
    # "verified" when the evidence quote was found in the cited briefing;
    # "inferred" otherwise (the default, and the only value for non-feedback).
    confidence: str = "inferred"
    # True when a feedback page failed verification and was coerced to
    # ``type: reference`` — such pages always stage in ``_inbox`` and never
    # universally promote (see inbox/paths.py and inbox/apply.py).
    unverified_feedback: bool = False
```

- [ ] **Step 4: Parse `evidence` from the LLM payload**

In `src/mnemo/core/extract/__init__.py` add after `_sanitize_llm_activates_on`:

```python
def _sanitize_llm_evidence(raw: object) -> dict | None:
    """Accept ``{"quote": non-empty str, "source": non-empty str}``; else None."""
    if not isinstance(raw, dict):
        return None
    quote = raw.get("quote")
    source = raw.get("source")
    if not isinstance(quote, str) or not quote.strip():
        return None
    if not isinstance(source, str) or not source.strip():
        return None
    return {"quote": quote.strip(), "source": source.strip()}
```

In `_parse_pages_from_response`, after `activates_on = _sanitize_llm_activates_on(rp.get("activates_on"))` add `evidence = _sanitize_llm_evidence(rp.get("evidence"))`, and pass `evidence=evidence,` into the `inbox.ExtractedPage(...)` constructor.

- [ ] **Step 5: Render the fields**

In `src/mnemo/core/extract/inbox/rendering.py::_render_page`, after the `activates_on_block` computation add:

```python
    confidence = getattr(page, "confidence", None) or "inferred"
    evidence_block = ""
    if isinstance(page.evidence, dict) and page.evidence.get("quote"):
        evidence_block = _render_nested_block("evidence", {
            "quote": str(page.evidence.get("quote") or ""),
            "source": str(page.evidence.get("source") or ""),
        })
    demoted_line = "demoted_from: feedback\n" if getattr(page, "unverified_feedback", False) else ""
```

and in the returned f-string, after `f"stability: {stability}\n"`:

```python
        f"confidence: {confidence}\n"
        f"{demoted_line}"
```

and after `f"{activates_on_block}"`:

```python
        f"{evidence_block}"
```

- [ ] **Step 6: Run the test, then the inbox/rendering suites**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_evidence_fields.py tests/unit/test_extract_inbox.py tests/unit/test_extract_inbox_v0_3.py tests/unit/test_extract_inbox_strip_enforce.py tests/unit/test_prompts_few_shot_schema.py`
Expected: all PASS. If a test compares a rendered page byte-for-byte, update the golden text to include `confidence: inferred`.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/extract/inbox/types.py src/mnemo/core/extract/__init__.py src/mnemo/core/extract/inbox/rendering.py tests/unit/test_extract_evidence_fields.py
git commit -m "feat(extract): evidence and confidence fields on extracted pages"
```

---

### Task 5: Evidence verification gate and routing

**Files:**
- Create: `src/mnemo/core/extract/evidence.py`
- Modify: `src/mnemo/core/extract/inbox/paths.py:_target_path_for_page`
- Modify: `src/mnemo/core/extract/inbox/apply.py:_is_universal_promotion`
- Modify: `src/mnemo/core/extract/__init__.py:_run_extraction_body` (after `_parse_pages_from_response`) and `_reconcile_universal_promotions`
- Test: `tests/unit/test_extract_evidence_gate.py`

- [ ] **Step 1: Write the failing test**

```python
"""Feedback reaches shared/feedback/ only with a quote that verifies against its briefing."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import evidence
from mnemo.core.extract.inbox import apply_pages
from mnemo.core.extract.inbox.paths import _target_path_for_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState

BRIEFING = """---
type: briefing
agent: proj
session_id: s1
corrections: 1
---

# Briefing — proj — s1

## Decisions made
- x

## Corrections
- "never retry on 4xx, only on 5xx" → Retry only on 5xx
"""


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    b = root / "bots" / "proj" / "briefings" / "sessions" / "s1.md"
    b.parent.mkdir(parents=True)
    b.write_text(BRIEFING)
    (root / "shared").mkdir()
    return root


def _page(**kw):
    base = dict(slug="retry-5xx-only", type="feedback", name="Retry only on 5xx", description="d",
                body="Retry only 5xx.", source_files=["bots/proj/briefings/sessions/s1.md"],
                source_hash="h1")
    base.update(kw)
    return ExtractedPage(**base)


def test_verified_when_quote_is_in_source_corrections(tmp_path):
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(evidence={"quote": "Never retry on 4xx,  only on 5xx",
                                             "source": "bots/proj/briefings/sessions/s1.md"}), root)
    assert p.type == "feedback" and p.confidence == "verified" and not p.unverified_feedback


def test_unverified_when_quote_missing_or_source_absent(tmp_path):
    root = _vault(tmp_path)
    for ev in (None,
               {"quote": "invented words here", "source": "bots/proj/briefings/sessions/s1.md"},
               {"quote": "never retry on 4xx, only on 5xx", "source": "bots/proj/briefings/sessions/nope.md"},
               {"quote": "never retry on 4xx, only on 5xx", "source": "../../etc/passwd"}):
        p = evidence.verify_page(_page(evidence=ev), root)
        assert p.type == "reference" and p.confidence == "inferred" and p.unverified_feedback
        assert p.evidence is None


def test_non_feedback_types_pass_through_untouched(tmp_path):
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(type="reference"), root)
    assert p.type == "reference" and not p.unverified_feedback


def test_unverified_page_routes_to_inbox_even_single_source(tmp_path):
    root = _vault(tmp_path)
    p = evidence.verify_page(_page(evidence=None), root)
    assert _target_path_for_page(p, root) == root / "shared" / "_inbox" / "reference" / "retry-5xx-only.md"


def test_unverified_page_never_universally_promotes(tmp_path):
    root = _vault(tmp_path)
    state = ExtractionState(last_run=None)
    p = evidence.verify_page(_page(evidence=None, source_files=[
        "bots/proj/briefings/sessions/s1.md", "bots/other/briefings/sessions/s2.md"]), root)
    apply_pages([p], state, root, run_id="r1")
    assert (root / "shared" / "_inbox" / "reference" / "retry-5xx-only.md").exists()
    assert not (root / "shared" / "reference" / "retry-5xx-only.md").exists()
    assert state.entries["reference/retry-5xx-only"].status == "inbox"


def test_verified_single_source_page_auto_promotes(tmp_path):
    root = _vault(tmp_path)
    state = ExtractionState(last_run=None)
    p = evidence.verify_page(_page(evidence={"quote": "never retry on 4xx, only on 5xx",
                                             "source": "bots/proj/briefings/sessions/s1.md"}), root)
    apply_pages([p], state, root, run_id="r1")
    out = root / "shared" / "feedback" / "retry-5xx-only.md"
    assert out.exists()
    assert "confidence: verified" in out.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_evidence_gate.py`
Expected: FAIL with `ImportError: cannot import name 'evidence'`

- [ ] **Step 3: Write `evidence.py`**

Create `src/mnemo/core/extract/evidence.py`:

```python
"""The promotion gate for feedback pages.

A feedback rule may only enter ``shared/feedback/`` when it cites a user quote
that the source briefing's ``## Corrections`` section actually carries. That
section is itself verified against the transcript when the briefing is
written (core/corrections.py), so a verified page traces back to words the
person typed. Anything else is real-but-inferred knowledge and is staged as a
``reference`` page for review.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mnemo.core import corrections
from mnemo.core.extract.inbox.rendering import _extract_body
from mnemo.core.extract.inbox.types import ExtractedPage


def _source_path(vault_root: Path, rel: str) -> Path | None:
    """Resolve a vault-relative source; refuse anything escaping the vault."""
    candidate = (vault_root / rel).resolve()
    try:
        candidate.relative_to(vault_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def quote_verified(evidence: dict | None, vault_root: Path) -> bool:
    if not isinstance(evidence, dict):
        return False
    quote = str(evidence.get("quote") or "")
    src = _source_path(vault_root, str(evidence.get("source") or ""))
    if src is None or not quote.strip():
        return False
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    items = corrections.parse_section(_extract_body(text))
    return any(corrections.quote_matches_turn(quote, it.quote) for it in items)


def verify_page(page: ExtractedPage, vault_root: Path) -> ExtractedPage:
    """Return the page marked verified, or demoted to a staged reference page."""
    if page.type != "feedback":
        return page
    if quote_verified(page.evidence, vault_root):
        return replace(page, confidence="verified", unverified_feedback=False)
    return replace(
        page,
        type="reference",
        confidence="inferred",
        unverified_feedback=True,
        evidence=None,
    )
```

- [ ] **Step 4: Route and guard**

In `src/mnemo/core/extract/inbox/paths.py::_target_path_for_page`, before `if is_backfill_page(page):` add:

```python
    if getattr(page, "unverified_feedback", False):
        return _inbox_path(vault_root, page)
```

and extend the docstring with: "Feedback pages that failed evidence verification stage as reference pages, whatever their source count."

In `src/mnemo/core/extract/inbox/apply.py::_is_universal_promotion`, after `if is_backfill_page(page): return False` add:

```python
    if getattr(page, "unverified_feedback", False):
        return False
```

In `src/mnemo/core/extract/__init__.py::_run_extraction_body`, right after `all_pages.extend(pages)` becomes:

```python
            pages = [evidence.verify_page(p, vault_root) for p in pages]
            all_pages.extend(pages)
```

with `from mnemo.core.extract import evidence` added to the module imports (place it with the other `from mnemo.core.extract import ...` lines; if none exist, add it under the existing `from mnemo.core import ...` block).

In `_reconcile_universal_promotions`, the page synthesized from an inbox file must keep the demotion mark. After `fm, body = parse_frontmatter(text)` add:

```python
        if str(fm.get("demoted_from") or "") == "feedback":
            # Demoted feedback stays staged until a person reviews it.
            continue
```

- [ ] **Step 5: Run the gate test and the full extract/inbox suites**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_evidence_gate.py tests/unit/ -k "extract or inbox or promote"`
Expected: new tests PASS. Existing orchestrator tests that feed feedback pages through `_run_extraction_body` with stubbed LLM output will now see those pages land in `_inbox/reference/` instead of `shared/feedback/`. For each such failure, do one of two things and nothing else: (a) if the test is about routing/promotion of feedback, add a briefing fixture with a `## Corrections` line and an `evidence` block to the stubbed JSON so the page verifies; (b) if the test is about something unrelated (telemetry, state hashing, backfill origin), change its expectation to the `_inbox/reference/` path. Record in the commit message which tests were adjusted and why.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/extract/evidence.py src/mnemo/core/extract/inbox/paths.py src/mnemo/core/extract/inbox/apply.py src/mnemo/core/extract/__init__.py tests/unit/
git commit -m "feat(extract): evidence gate — unverified feedback stages as reference"
```

---

### Task 6: Prompts ask for evidence and slug reuse; existing-rules fragment

**Files:**
- Create: `src/mnemo/core/extract/prompts/existing_rules.py`
- Modify: `src/mnemo/core/extract/prompts/render.py:build_consolidation_prompt`
- Modify: `src/mnemo/core/extract/prompts/templates/system_feedback.py`
- Modify: `src/mnemo/core/extract/prompts/templates/schema.py`
- Modify: `src/mnemo/core/extract/prompts/templates/few_shot_feedback.py` (Example 1)
- Test: `tests/unit/test_extract_prompts_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
"""Consolidation prompts: evidence requirement, existing-rules list, few-shot round-trip."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import _parse_pages_from_response
from mnemo.core.extract.prompts import build_consolidation_prompt
from mnemo.core.extract.prompts.existing_rules import existing_rules_fragment
from mnemo.core.extract.prompts.templates.few_shot_feedback import _FEW_SHOT_FEEDBACK
from mnemo.core.extract.prompts.templates.schema import _SCHEMA_EXAMPLE
from mnemo.core.extract.prompts.templates.system_feedback import FEEDBACK_SYSTEM_PROMPT
from mnemo.core.extract.scanner import MemoryFile


def _rule(root: Path, kind: str, slug: str, name: str, sources: list[str], inbox=False):
    d = root / "shared" / ("_inbox/" + kind if inbox else kind)
    d.mkdir(parents=True, exist_ok=True)
    src = "\n".join(f"  - {s}" for s in sources)
    (d / f"{slug}.md").write_text(f"---\nname: {name}\ntype: {kind}\nsources:\n{src}\ntags:\n  - x\n---\nbody\n")


def _mf(agent: str) -> MemoryFile:
    return MemoryFile(path=Path(f"/v/bots/{agent}/briefings/sessions/s.md"), agent=agent,
                      type="feedback", slug="briefing-s", frontmatter={"type": "briefing"},
                      body="## Corrections\n- \"use yarn not npm\" → Use yarn\n", source_hash="h")


def test_system_prompt_requires_evidence_and_slug_reuse():
    p = FEEDBACK_SYSTEM_PROMPT
    assert "evidence" in p and "## Corrections" in p
    assert "type: reference" in p or '"type": "reference"' in p
    assert "reuse" in p.lower() and "slug" in p.lower()


def test_schema_example_documents_evidence():
    assert '"evidence"' in _SCHEMA_EXAMPLE and '"quote"' in _SCHEMA_EXAMPLE


def test_existing_rules_fragment_lists_same_project_rules_by_source_count(tmp_path):
    _rule(tmp_path, "feedback", "use-yarn", "Use yarn", ["bots/a/briefings/sessions/1.md", "bots/b/briefings/sessions/2.md"])
    _rule(tmp_path, "feedback", "no-any", "No any", ["bots/a/memory/f.md"])
    _rule(tmp_path, "feedback", "other-proj", "Other", ["bots/zzz/memory/f.md"])
    _rule(tmp_path, "feedback", "staged", "Staged", ["bots/a/memory/g.md"], inbox=True)
    _rule(tmp_path, "reference", "ref", "Ref", ["bots/a/memory/r.md"])
    frag = existing_rules_fragment(tmp_path, "feedback", agents={"a"})
    lines = [l for l in frag.splitlines() if l.startswith("- ")]
    assert lines[0].startswith("- use-yarn — Use yarn")
    assert any(l.startswith("- no-any") for l in lines)
    assert any(l.startswith("- staged") for l in lines)
    assert not any("other-proj" in l or "- ref" in l for l in lines)


def test_existing_rules_fragment_caps_at_80(tmp_path):
    for i in range(90):
        _rule(tmp_path, "feedback", f"r{i:03d}", f"R{i}", ["bots/a/memory/f.md"])
    frag = existing_rules_fragment(tmp_path, "feedback", agents={"a"})
    assert sum(1 for l in frag.splitlines() if l.startswith("- ")) == 80


def test_existing_rules_fragment_empty_on_fresh_vault(tmp_path):
    assert existing_rules_fragment(tmp_path, "feedback", agents={"a"}) == ""


def test_consolidation_prompt_includes_fragment_for_chunk_agents(tmp_path):
    _rule(tmp_path, "feedback", "use-yarn", "Use yarn", ["bots/a/memory/f.md"])
    text = build_consolidation_prompt("feedback", [_mf("a")], vault_root=tmp_path)
    assert "Existing rules" in text and "- use-yarn — Use yarn" in text


def test_few_shot_example_1_round_trips_with_evidence():
    blob = _FEW_SHOT_FEEDBACK.split("Output (ONE merged page")[1].split("\n", 1)[1].split("\n\nExample 2")[0].strip()
    pages = _parse_pages_from_response(blob, "feedback")
    assert pages and pages[0].evidence and pages[0].evidence["quote"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_prompts_evidence.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.extract.prompts.existing_rules'`

- [ ] **Step 3: Write the existing-rules fragment**

Create `src/mnemo/core/extract/prompts/existing_rules.py`:

```python
"""The "Existing rules" hint shown in the consolidation user message.

Each extraction used to mint a fresh slug for a rule the vault already held,
so ``source_count`` never accrued and near-duplicate families grew. Listing the
live and staged slugs for the chunk's projects lets the model reinforce an
existing rule instead; ``inbox/dedup._detect_similar_existing`` is the
mechanical backstop when it does not.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import derive_rule_slug, parse_frontmatter
from mnemo.core.rule_activation import is_universal, projects_for_rule

MAX_ENTRIES = 80


def _collect(vault_root: Path, kind: str) -> list[tuple[str, str, list[str], int]]:
    out = []
    for d in (vault_root / "shared" / kind, vault_root / "shared" / "_inbox" / kind):
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if md.name.endswith(".proposed.md") or md.name.endswith(".update-proposed.md"):
                continue
            try:
                fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            sources = fm.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            sources = [s for s in sources if isinstance(s, str)]
            out.append((derive_rule_slug(fm, md.stem), str(fm.get("name") or md.stem),
                        projects_for_rule(sources), len(sources)))
    return out


def existing_rules_fragment(vault_root: Path | None, kind: str, *, agents: set[str]) -> str:
    if vault_root is None:
        return ""
    rows = []
    for slug, name, projects, count in _collect(vault_root, kind):
        if agents and projects and not (set(projects) & agents) and not is_universal(projects, 2):
            continue
        rows.append((count, slug, name))
    if not rows:
        return ""
    rows.sort(key=lambda r: (-r[0], r[1]))
    lines = [f"- {slug} — {name}" for _, slug, name in rows[:MAX_ENTRIES]]
    return (
        f"Existing rules for {kind} (REUSE the slug when your page states the same "
        f"rule — only mint a new slug for a genuinely new rule):\n"
        + "\n".join(lines)
        + "\n\n"
    )
```

- [ ] **Step 4: Wire the fragment into the consolidation prompt**

In `src/mnemo/core/extract/prompts/render.py` add `from mnemo.core.extract.prompts.existing_rules import existing_rules_fragment` and in `build_consolidation_prompt` change the return to:

```python
    agents = {f.agent for f in files if getattr(f, "agent", None)}
    return (
        f"Task: consolidate these {label} memory files into canonical Tier 2 "
        f"pages. {cluster_clause}\n\n"
        f"{_existing_tags_fragment(vault_root, kind)}"
        f"{existing_rules_fragment(vault_root, kind, agents=agents)}"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{few_shot}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )
```

- [ ] **Step 5: Extend the system prompt, schema and few-shot**

In `system_feedback.py` insert, right after the "Input mixing" paragraph (the one ending `so the state machine can track them.\n\n"`):

```python
    "## Evidence (required for type: feedback)\n\n"
    "A feedback page is a rule the USER established by correcting or "
    "instructing the assistant. Briefings carry a `## Corrections` section "
    "whose bullets quote the user verbatim. Every feedback page you emit MUST "
    "carry `evidence`: `{\"quote\": \"<one quote copied exactly from a "
    "## Corrections bullet>\", \"source\": \"<the briefing path that carries "
    "it>\"}`. Copy the quote character-for-character; a quote that does not "
    "appear in the cited briefing is discarded by a mechanical check and the "
    "page is demoted. If no input file carries a user quote that supports the "
    "rule, do NOT emit it as feedback: emit it with `type: reference` and "
    "`evidence: null` — it is knowledge, not a correction.\n\n"
    "## Slug reuse\n\n"
    "The user message lists 'Existing rules' for this vault. When your page "
    "states the same rule as one of them, emit THAT slug so the existing page "
    "is reinforced (its sources accumulate) instead of a duplicate being "
    "created. Mint a new slug only for a genuinely new rule.\n\n"
```

In `schema.py` add to the JSON example after the `"activates_on"` line:

```
      "evidence": {"quote": "verbatim user quote from a ## Corrections bullet", "source": "bots/<agent>/briefings/sessions/<id>.md"} | null
```

and append after the `aliases` paragraph:

```
`evidence` is REQUIRED for `type: feedback` and must be null for other types.
The quote must be copied exactly from a `## Corrections` bullet of the cited
source briefing; it is verified mechanically.
```

In `few_shot_feedback.py` rewrite Example 1's input to be one briefing with a Corrections bullet plus one memory file, and add `evidence` to the output. Replace the whole `Example 1` block (from `Example 1 — POSITIVE` up to but not including `Example 2 — NEGATIVE`) with:

```
Example 1 — POSITIVE: a briefing correction and a memory note state the same rule → ONE page with evidence.
(The briefing's ## Corrections bullet is the user's own words; the memory
file paraphrases the same rule. Merge them, cite the briefing quote as
evidence, keep both files in source_files.)

Input:
[FILE: bots/clubinho/briefings/sessions/9f1c.md]
---
type: briefing
agent: clubinho
session_id: 9f1c
corrections: 1
---
## Decisions made
- Added a pre-commit hook. **Why:** lint drift.

## Corrections
- "do not commit on my behalf, I stage and commit myself" → Never run git commit unless explicitly asked
[END]
[FILE: bots/central-inteligencia-frontend/memory/feedback_no_commits.md]
---
name: No commits
type: feedback
---
Never run `git commit` on my behalf. Only edit files.
**Why:** I review and stage commits myself.
**How to apply:** edit files but stop before committing.
[END]

Output (ONE merged page, both files listed in source_files, evidence quoting the briefing):
{"pages":[{"slug":"no-commits-without-permission","name":"Never commit without explicit permission","description":"Do not create git commits unless the user explicitly asks","type":"feedback","body":"Never run `git commit` unless the user explicitly asks you to commit.\\n\\n**Why:** the user reviews and owns their commit history; autonomous commits bypass that review.\\n\\n**How to apply:** edit and stage files freely, but stop before running `git commit`. Wait for explicit phrasing like \\"commit this\\" before proceeding.","source_files":["bots/clubinho/briefings/sessions/9f1c.md","bots/central-inteligencia-frontend/memory/feedback_no_commits.md"],"stability":"stable","tags":["git","workflow"],"evidence":{"quote":"do not commit on my behalf, I stage and commit myself","source":"bots/clubinho/briefings/sessions/9f1c.md"}}]}

```

- [ ] **Step 6: Run the prompt tests**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_prompts_evidence.py tests/unit/test_extract_prompts.py tests/unit/test_extract_prompts_aliases.py tests/unit/test_extract_prompts_enforce_guidance.py tests/unit/test_prompts_few_shot_schema.py`
Expected: all PASS. If `test_prompts_few_shot_schema.py` slices Example 1 by a literal marker that changed, update the marker to `"Output (ONE merged page"`.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/extract/prompts/ tests/unit/test_extract_prompts_evidence.py tests/unit/test_prompts_few_shot_schema.py
git commit -m "feat(prompts): require evidence for feedback pages and list existing rules for slug reuse"
```

---

### Task 7: Similarity redirect — reinforce existing rules

**Files:**
- Modify: `src/mnemo/core/extract/inbox/dedup.py`
- Modify: `src/mnemo/core/extract/inbox/apply.py:apply_pages`
- Test: `tests/unit/test_extract_similar_existing.py`

- [ ] **Step 1: Write the failing test**

```python
"""A new page that says what an existing rule says reinforces it instead of minting a slug."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox import apply_pages, dedup
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry

BODY = ("Apply negative keywords early in a campaign launch to filter irrelevant traffic.\n\n"
        "**Why:** broad match wastes budget on unrelated queries.\n\n"
        "**How to apply:** add the negative list before enabling broad match.")


def _seed(root: Path, state: ExtractionState, slug: str, name: str, body: str = BODY):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    path.write_text(f"---\nname: {name}\ndescription: {name}\ntype: feedback\nsources:\n  - bots/a/briefings/sessions/1.md\ntags:\n  - ads\n---\n{body}\n")
    # written_hash must match the file on disk: the auto-promoted branch treats a
    # mismatch as "user edited this page" and bounces to a .proposed.md sibling
    # without touching the entry, which is not the path under test here.
    state.entries[f"feedback/{slug}"] = StateEntry(source_files=["bots/a/briefings/sessions/1.md"],
                                                   source_hash="h0", written_hash=content_hash(path),
                                                   written_at="r0", status="auto_promoted")


def _page(slug: str, name: str, body: str = BODY, sources=None):
    return ExtractedPage(slug=slug, type="feedback", name=name, description=name, body=body,
                         source_files=sources or ["bots/b/briefings/sessions/2.md"], source_hash="h1",
                         confidence="verified",
                         evidence={"quote": "q", "source": "bots/b/briefings/sessions/2.md"})


def test_similar_page_redirects_to_existing_slug_and_accrues_sources(tmp_path):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    page = _page("google-ads-negative-keyword-strategy", "Negative keyword strategy for launches")
    apply_pages([page], state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" not in state.entries
    entry = state.entries["feedback/negative-keywords-early-launch"]
    assert entry.source_files == ["bots/a/briefings/sessions/1.md", "bots/b/briefings/sessions/2.md"]
    assert not (tmp_path / "shared" / "feedback" / "google-ads-negative-keyword-strategy.md").exists()


def test_distinct_rule_keeps_its_own_slug(tmp_path):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    page = _page("use-yarn", "Use yarn for package management",
                 body="Always use yarn.\n\n**Why:** yarn.lock is canonical.\n\n**How to apply:** yarn add.")
    apply_pages([page], state, tmp_path, run_id="r1")
    assert "feedback/use-yarn" in state.entries


def test_threshold_is_load_bearing(tmp_path, monkeypatch):
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "negative-keywords-early-launch", "Add negative keywords at launch")
    monkeypatch.setattr(dedup, "SIMILARITY_THRESHOLD", 1.01)
    apply_pages([_page("google-ads-negative-keyword-strategy", "Negative keyword strategy for launches")],
                state, tmp_path, run_id="r1")
    assert "feedback/google-ads-negative-keyword-strategy" in state.entries


def test_similarity_index_skips_other_types_and_missing_files(tmp_path):
    state = ExtractionState(last_run=None)
    state.entries["reference/ghost"] = StateEntry(source_files=[], source_hash="", written_hash="",
                                                  written_at="", status="auto_promoted")
    idx = dedup.SimilarityIndex(state, tmp_path, "feedback")
    assert idx.find(_page("x", "Anything at all")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_similar_existing.py`
Expected: FAIL — first test: `AssertionError` (the new slug exists in state); last test: `AttributeError: module ... has no attribute 'SimilarityIndex'`

- [ ] **Step 3: Add the similarity index**

Append to `src/mnemo/core/extract/inbox/dedup.py`:

```python
# ---------------------------------------------------------------------------
# Third-layer guardrail: content similarity against EVERY existing page of the
# same type, no source-set requirement. Stem collision and drift both require
# the slugs or sources to line up; a rule re-learned in another project under
# fresh wording matches neither, which is how families of 5-10 near-duplicates
# accumulated and why source_count never grew past 1.
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD = 0.5
_NAME_WEIGHT, _DESC_WEIGHT, _BODY_WEIGHT = 3, 2, 1


def _weighted_profile(name: str, description: str, body: str) -> dict[str, int]:
    from mnemo.core.reflex.tokenizer import tokenize

    profile: dict[str, int] = {}
    for text, weight in ((name, _NAME_WEIGHT), (description, _DESC_WEIGHT), (body, _BODY_WEIGHT)):
        for tok in tokenize(text or ""):
            stem = _stem_word(tok)
            profile[stem] = profile.get(stem, 0) + weight
    return profile


def weighted_jaccard(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    inter = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
    union = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
    return inter / union if union else 0.0


class SimilarityIndex:
    """Profiles of every on-disk page of one type, built once per apply run."""

    def __init__(self, state: ExtractionState, vault_root: Path, page_type: str) -> None:
        from mnemo.core.filters import parse_frontmatter

        self._profiles: dict[str, dict[str, int]] = {}
        for key in list(state.entries):
            if not key.startswith(f"{page_type}/"):
                continue
            slug = key.split("/", 1)[1]
            target = _existing_target(vault_root, page_type, slug)
            if target is None:
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            self._profiles[slug] = _weighted_profile(
                str(fm.get("name") or slug),
                str(fm.get("description") or ""),
                _extract_body(text),
            )

    def find(self, page: ExtractedPage, threshold: float | None = None) -> str | None:
        """Return the most similar existing slug at or above the threshold."""
        cutoff = SIMILARITY_THRESHOLD if threshold is None else threshold
        probe = _weighted_profile(page.name, page.description, page.body)
        best_slug, best = None, 0.0
        for slug, profile in self._profiles.items():
            if slug == page.slug:
                continue
            score = weighted_jaccard(probe, profile)
            if score > best:
                best_slug, best = slug, score
        return best_slug if best_slug is not None and best >= cutoff else None


def _detect_similar_existing(
    page: ExtractedPage, index: SimilarityIndex,
) -> str | None:
    return index.find(page)
```

- [ ] **Step 4: Wire into `apply_pages`**

In `src/mnemo/core/extract/inbox/apply.py` import `SimilarityIndex, _detect_similar_existing` from `dedup`, and in `apply_pages` replace the drift/stem block with:

```python
    sim_indexes: dict[str, SimilarityIndex] = {}
    for page in pages:
        drift_target = _detect_drift_slug(page, state, vault_root)
        if drift_target is not None:
            page.slug = drift_target
        else:
            stem_target = _detect_stem_collision(page, state, vault_root)
            if stem_target is not None:
                page.slug = stem_target
            else:
                if page.type not in sim_indexes:
                    sim_indexes[page.type] = SimilarityIndex(state, vault_root, page.type)
                similar = _detect_similar_existing(page, sim_indexes[page.type])
                if similar is not None:
                    page.slug = similar
```

Everything after (`key = ...`) stays as is. Note the existing `auto_promoted` branch already unions sources through `union_with_prior_sources`, which is what makes `source_count` accrue.

- [ ] **Step 5: Run the similarity tests and the whole inbox suite**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_extract_similar_existing.py tests/unit/ -k "inbox or extract or dedup"`
Expected: all PASS. If an existing test seeds two pages with near-identical placeholder bodies ("body", "x") and expects separate slugs, the Jaccard on such short bodies will be 1.0 — give those fixtures distinct names/bodies rather than lowering the threshold.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/extract/inbox/dedup.py src/mnemo/core/extract/inbox/apply.py tests/unit/
git commit -m "feat(extract): similarity redirect so re-learned rules reinforce the existing slug"
```

---

### Task 8: Prompt-echo guard and PII redaction

**Files:**
- Create: `src/mnemo/core/extract/guards.py`
- Create: `src/mnemo/core/redact.py`
- Modify: `src/mnemo/core/extract/__init__.py` (`ExtractionSummary`, `_run_extraction_body`)
- Test: `tests/unit/test_extract_guards.py`, `tests/unit/test_redact.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_redact.py`:

```python
from mnemo.core.redact import redact


def test_redacts_emails_tokens_and_long_hex():
    text = ("mail me at ana.silva@example.com, key sk-abc123DEF456ghi789jkl012, "
            "gh ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789, slack xoxb-1234-5678-abcd, "
            "aws AKIAIOSFODNN7EXAMPLE, id 0123456789abcdef0123456789abcdef")
    out, n = redact(text)
    assert n == 6
    assert "example.com" not in out and "sk-abc" not in out and "ghp_" not in out
    assert "xoxb" not in out and "AKIA" not in out and "0123456789abcdef0123456789abcdef" not in out
    assert out.count("[redacted]") == 6


def test_leaves_clean_text_alone():
    assert redact("use yarn, run `git status`, commit a1b2c3d") == ("use yarn, run `git status`, commit a1b2c3d", 0)
```

`tests/unit/test_extract_guards.py`:

```python
from mnemo.core.extract.guards import is_prompt_echo
from mnemo.core.extract.inbox.types import ExtractedPage


def _p(name="N", description="D", body="B"):
    return ExtractedPage(slug="s", type="feedback", name=name, description=description, body=body,
                         source_files=["a.md"], source_hash="h")


def test_rejects_pages_that_echo_extractor_guidance():
    assert is_prompt_echo(_p(body="Only emit enforce blocks when the rule has explicit blocking intent."))
    assert is_prompt_echo(_p(name="Stability field must be stable or evolving"))
    assert is_prompt_echo(_p(description="Prefer Existing vault tags over inventing new ones"))


def test_accepts_ordinary_rules():
    assert not is_prompt_echo(_p(body="Never retry on 4xx.\n\n**Why:** x\n\n**How to apply:** y"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_redact.py tests/unit/test_extract_guards.py`
Expected: FAIL with `ModuleNotFoundError` for both modules.

- [ ] **Step 3: Write the modules**

`src/mnemo/core/redact.py`:

```python
"""Strip the PII shapes that showed up in extracted rules: e-mails, API tokens, long hex ids."""
from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),          # e-mail
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                                # OpenAI/Anthropic-style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{8,}\b"),                            # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                     # AWS access key
    re.compile(r"\b[0-9a-f]{32,}\b", re.I),                                  # long hex ids
)
REPLACEMENT = "[redacted]"


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, replacements)."""
    total = 0
    for pat in _PATTERNS:
        text, n = pat.subn(REPLACEMENT, text)
        total += n
    return text, total
```

`src/mnemo/core/extract/guards.py`:

```python
"""Reject pages that are the extractor's own instructions echoed back.

The vault's highest-source_count rule turned out to be a paraphrase of the
enforce-block guidance in the feedback system prompt. These phrases only ever
appear in mnemo's prompts, never in a real correction.
"""
from __future__ import annotations

from mnemo.core.extract.inbox.types import ExtractedPage

ECHO_PHRASES = (
    "blocking intent",
    "enforce block",
    "stability field",
    "tier 2 page",
    "aliases field",
    "activates_on",
    "deny_pattern",
    "sacred directory",
    "existing vault tags",
)


def is_prompt_echo(page: ExtractedPage) -> bool:
    haystack = " ".join((page.name or "", page.description or "", page.body or "")).lower()
    return any(phrase in haystack for phrase in ECHO_PHRASES)
```

- [ ] **Step 4: Wire both into extraction**

In `src/mnemo/core/extract/__init__.py`:

Add to `ExtractionSummary`: `echo_rejected: int = 0` and `redactions: int = 0`.

Add imports: `from mnemo.core.extract.guards import is_prompt_echo` and `from mnemo.core.redact import redact`.

In `_run_extraction_body`, replace the two lines from Task 5

```python
            pages = [evidence.verify_page(p, vault_root) for p in pages]
            all_pages.extend(pages)
```

with

```python
            kept: list[inbox.ExtractedPage] = []
            for p in pages:
                if is_prompt_echo(p):
                    summary.echo_rejected += 1
                    errors.log_error(vault_root, "extract.prompt_echo",
                                     ValueError(f"rejected prompt-echo page {p.type}/{p.slug}"))
                    continue
                p = evidence.verify_page(p, vault_root)
                p.body, n_body = redact(p.body)
                p.description, n_desc = redact(p.description)
                summary.redactions += n_body + n_desc
                kept.append(p)
            all_pages.extend(kept)
```

(Redaction runs after verification on purpose: the evidence quote is left untouched so it still matches the briefing; a quote containing PII is the user's own words and stays as they typed it.)

Add a test to `tests/unit/test_extract_guards.py` that drives `_run_extraction_body` through a stubbed `llm.call` returning one echo page and one clean page with an e-mail in the body, and asserts `summary.echo_rejected == 1`, `summary.redactions == 1`, and the written page contains `[redacted]`. Model it on `tests/unit/test_extract_orchestrator.py` (copy its vault/state/llm fixtures).

- [ ] **Step 5: Run the tests**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_redact.py tests/unit/test_extract_guards.py tests/unit/test_extract_orchestrator.py`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/redact.py src/mnemo/core/extract/guards.py src/mnemo/core/extract/__init__.py tests/unit/test_redact.py tests/unit/test_extract_guards.py
git commit -m "feat(extract): reject prompt-echo pages and redact PII before writing"
```

---

### Task 9: Reflex indexes the evidence quote

**Files:**
- Modify: `src/mnemo/core/reflex/index.py:_FIELD_NAMES,_field_tokens`
- Modify: `src/mnemo/core/reflex/bm25.py:DEFAULT_WEIGHTS`
- Modify: `src/mnemo/core/config.py` (`reflex.bm25f.fieldWeights`)
- Test: `tests/unit/test_reflex_index_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from mnemo.core.config import DEFAULTS
from mnemo.core.reflex import bm25
from mnemo.core.reflex.index import build_index


def _rule(root: Path, slug: str, quote: str | None):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    ev = f"evidence:\n  quote: '{quote}'\n  source: bots/a/briefings/sessions/1.md\n" if quote else ""
    (d / f"{slug}.md").write_text(
        f"---\nname: Rule {slug}\ndescription: d\ntype: feedback\nconfidence: verified\n{ev}"
        f"sources:\n  - bots/a/briefings/sessions/1.md\ntags:\n  - x\n---\nbody text\n")


def test_evidence_quote_is_a_scored_field(tmp_path):
    _rule(tmp_path, "with-quote", "never retry on 4xx only on 5xx")
    _rule(tmp_path, "without", None)
    idx = build_index(tmp_path)
    assert "evidence" in idx["avg_field_length"]
    assert idx["docs"]["with-quote"]["field_length"]["evidence"] > 0
    assert idx["docs"]["without"]["field_length"]["evidence"] == 0
    scores = dict(bm25.score_docs(idx, query_tokens=["retry", "4xx"], candidate_slugs=["with-quote", "without"]))
    assert scores.get("with-quote", 0) > scores.get("without", 0)


def test_default_weights_agree_between_bm25_and_config():
    assert bm25.DEFAULT_WEIGHTS["evidence"] == 2.5
    assert DEFAULTS["reflex"]["bm25f"]["fieldWeights"]["evidence"] == 2.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_reflex_index_evidence.py`
Expected: FAIL with `KeyError: 'evidence'`

- [ ] **Step 3: Implement**

`src/mnemo/core/reflex/index.py`: `_FIELD_NAMES = ("name", "topic_tags", "aliases", "description", "body", "evidence")`; in `_field_tokens` add

```python
    evidence = fm.get("evidence")
    quote = str(evidence.get("quote") or "") if isinstance(evidence, dict) else ""
```

and `"evidence": tokenize(quote),` to the returned dict. Update the module docstring's schema comment to list `evidence`.

`src/mnemo/core/reflex/bm25.py`: add `"evidence": 2.5,` to `DEFAULT_WEIGHTS`.

`src/mnemo/core/config.py`: add `"evidence": 2.5,` to `reflex.bm25f.fieldWeights`.

Schema version stays 1: the field is additive, `score_docs` uses `.get(field, 0)` for missing tf/lengths, and `session_start` rebuilds the index every session.

- [ ] **Step 4: Run reflex suites**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_reflex_index_evidence.py tests/unit/ -k reflex`
Expected: all PASS (a test asserting the exact `_FIELD_NAMES` tuple or `avg_field_length` keys needs the new field added).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/reflex/index.py src/mnemo/core/reflex/bm25.py src/mnemo/core/config.py tests/unit/
git commit -m "feat(reflex): score the evidence quote as its own BM25F field"
```

---

### Task 10: `mnemo reclassify` — grade the legacy vault, with undo

**Files:**
- Create: `src/mnemo/core/reclassify.py`
- Create: `src/mnemo/cli/commands/reclassify.py`
- Modify: `src/mnemo/cli/parser.py` (ADVANCED_COMMANDS + subparser), `src/mnemo/cli/commands/__init__.py`
- Test: `tests/unit/test_reclassify.py`, `tests/unit/test_cli_reclassify.py`

Design (module API):

```python
@dataclass(frozen=True) class RuleDoc: path: Path; slug: str; name: str; fm: dict; body: str; sources: list[str]
@dataclass class Verdict: slug: str; verdict: str  # keep|demote|merge|archive
                          target: str | None = None; quote: str | None = None; source: str | None = None; reason: str = ""
def collect_rules(vault_root) -> list[RuleDoc]                       # live shared/feedback/*.md, skipping *.proposed.md
def transcript_turns(vault_root, briefing_rel, projects_root) -> list[str]   # session_id = briefing stem → ~/.claude/projects/*/<id>.jsonl → transcript.user_turns, cap 40 × 300 chars
def context_for(rule, vault_root, projects_root) -> str              # per cited briefing: "## Decisions made" (≤1500 chars) + Corrections section + numbered user turns
def build_prompt(batch: list[RuleDoc], contexts: dict[str,str], known_slugs: list[str]) -> str
RECLASSIFY_SYSTEM_PROMPT: str
def parse_verdicts(text) -> list[Verdict]
def validate(verdicts, rules_by_slug, vault_root, projects_root) -> list[Verdict]   # merge target must exist (else demote); keep must verify quote against briefing Corrections OR transcript turns (else demote); unknown verdict → archive
def plan(vault_root, *, model, timeout, batch_size=10, limit=None, projects_root=None, call=llm.call) -> Plan   # Plan(run_id, verdicts, llm_calls)
def save_plan(vault_root, plan) / load_plan(vault_root) -> Plan|None  # .mnemo/reclassify-plan.json
def apply(vault_root, plan) -> ApplyReport    # moves + manifest under shared/_archive/reclassify-<run_id>/ with originals/ copies; updates .mnemo/extraction-state.json entries; rebuilds indexes
def undo(vault_root, run_id) -> int          # restores every touched file from originals/, removes files the run created, restores state file from manifest copy
```

Verdict semantics on apply:
- `keep`: rewrite frontmatter in place adding `confidence: verified` and an `evidence:` block (quote + source); nothing moves.
- `demote`: move to `shared/reference/<slug>.md`, rewriting `type: reference`, `confidence: inferred`, `demoted_from: feedback`.
- `merge`: append this rule's `sources:` entries to the target page (dedup), then move this file to `<archive>/merged/<slug>.md`.
- `archive`: move to `<archive>/archived/<slug>.md`.

Manifest (`manifest.json`): `{"run_id", "created_at", "moves": [{"slug", "verdict", "from", "to", "target", "target_original": "<archive>/originals/<target>.md"|null}], "state_backup": "<archive>/originals/extraction-state.json"}`. Every file that is modified or moved is first copied byte-for-byte into `<archive>/originals/`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_reclassify.py`:

```python
"""core/reclassify: verdict validation, apply with manifest, byte-exact undo."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import reclassify as R

BRIEF = """---
type: briefing
agent: proj
session_id: s1
---
## Decisions made
- used yarn

## Corrections
- "use yarn not npm in this repo" → Use yarn
"""


def _rule(root: Path, slug: str, name: str, body: str = "Body.\n\n**Why:** w\n\n**How to apply:** h"):
    d = root / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(f"---\nname: {name}\ndescription: {name}\ntype: feedback\nstability: stable\n"
                 f"sources:\n  - bots/proj/briefings/sessions/s1.md\ntags:\n  - auto-promoted\n  - x\n---\n{body}\n")
    return p


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    b = root / "bots" / "proj" / "briefings" / "sessions" / "s1.md"
    b.parent.mkdir(parents=True)
    b.write_text(BRIEF)
    (root / ".mnemo").mkdir()
    (root / ".mnemo" / "extraction-state.json").write_text(json.dumps({
        "schema_version": 3, "last_run": None, "entries": {
            "feedback/use-yarn": {"source_files": ["bots/proj/briefings/sessions/s1.md"], "source_hash": "a",
                                   "written_hash": "b", "written_at": "r", "status": "auto_promoted"}}}))
    return root


def test_collect_rules_reads_live_feedback_only(vault):
    _rule(vault, "use-yarn", "Use yarn")
    (vault / "shared" / "feedback" / "x.proposed.md").write_text("---\nname: p\n---\n")
    assert [r.slug for r in R.collect_rules(vault)] == ["use-yarn"]


def test_validate_downgrades_bad_merge_and_unverifiable_keep(vault, tmp_path):
    _rule(vault, "use-yarn", "Use yarn")
    _rule(vault, "generic", "Generic tip")
    rules = {r.slug: r for r in R.collect_rules(vault)}
    verdicts = [
        R.Verdict(slug="use-yarn", verdict="keep", quote="use yarn not npm in this repo",
                  source="bots/proj/briefings/sessions/s1.md"),
        R.Verdict(slug="generic", verdict="keep", quote="words nobody typed here"),
        R.Verdict(slug="generic", verdict="merge", target="does-not-exist"),
        R.Verdict(slug="generic", verdict="banana"),
    ]
    out = R.validate(verdicts, rules, vault, projects_root=tmp_path / "none")
    assert [(v.slug, v.verdict) for v in out] == [
        ("use-yarn", "keep"), ("generic", "demote"), ("generic", "demote"), ("generic", "archive")]


def test_apply_moves_files_writes_manifest_and_undo_restores_bytes(vault, tmp_path):
    keep = _rule(vault, "use-yarn", "Use yarn")
    demote = _rule(vault, "project-fact", "Deploy needs VPN")
    dup = _rule(vault, "use-yarn-dup", "Use yarn (dup)")
    junk = _rule(vault, "generic", "Generic tip")
    originals = {p: p.read_bytes() for p in (keep, demote, dup, junk)}
    state_before = (vault / ".mnemo" / "extraction-state.json").read_bytes()

    plan = R.Plan(run_id="20260901T000000", llm_calls=0, verdicts=[
        R.Verdict(slug="use-yarn", verdict="keep", quote="use yarn not npm in this repo",
                  source="bots/proj/briefings/sessions/s1.md"),
        R.Verdict(slug="project-fact", verdict="demote"),
        R.Verdict(slug="use-yarn-dup", verdict="merge", target="use-yarn"),
        R.Verdict(slug="generic", verdict="archive"),
    ])
    report = R.apply(vault, plan, rebuild_indexes=False)

    kept_text = keep.read_text()
    assert "confidence: verified" in kept_text and "use yarn not npm in this repo" in kept_text
    assert not demote.exists()
    demoted = vault / "shared" / "reference" / "project-fact.md"
    assert demoted.exists() and "type: reference" in demoted.read_text() and "demoted_from: feedback" in demoted.read_text()
    assert not dup.exists() and not junk.exists()
    arch = vault / "shared" / "_archive" / "reclassify-20260901T000000"
    assert (arch / "merged" / "use-yarn-dup.md").exists() and (arch / "archived" / "generic.md").exists()
    manifest = json.loads((arch / "manifest.json").read_text())
    assert {m["verdict"] for m in manifest["moves"]} == {"keep", "demote", "merge", "archive"}
    assert report.kept == 1 and report.demoted == 1 and report.merged == 1 and report.archived == 1
    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text())
    assert "reference/project-fact" in state["entries"]
    assert state["entries"]["feedback/use-yarn-dup"]["status"] == "dismissed"

    restored = R.undo(vault, "20260901T000000")
    assert restored >= 4
    for p, data in originals.items():
        assert p.read_bytes() == data
    assert not demoted.exists()
    assert (vault / ".mnemo" / "extraction-state.json").read_bytes() == state_before


def test_plan_batches_and_parses_llm_verdicts(vault, tmp_path):
    for i in range(12):
        _rule(vault, f"r{i}", f"Rule {i}")
    calls = []

    def fake_call(prompt, *, system, model, timeout):
        calls.append(prompt)
        slugs = [l.split(":", 1)[0].strip("- ") for l in prompt.splitlines() if l.startswith("- r")]
        payload = {"verdicts": [{"slug": s, "verdict": "archive", "reason": "generic"} for s in slugs]}
        from mnemo.core.llm import LLMResponse
        return LLMResponse(text=json.dumps(payload), total_cost_usd=0.0, input_tokens=1,
                           output_tokens=1, api_key_source="none", raw={})

    plan = R.plan(vault, model="m", timeout=5, batch_size=10, projects_root=tmp_path / "none", call=fake_call)
    assert plan.llm_calls == 2 and len(plan.verdicts) == 12
    assert all(v.verdict == "archive" for v in plan.verdicts)
    R.save_plan(vault, plan)
    assert R.load_plan(vault).run_id == plan.run_id


def test_transcript_turns_found_by_session_id(vault, tmp_path):
    projects = tmp_path / "projects" / "-Users-x-proj"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "use yarn not npm in this repo"}}) + "\n")
    turns = R.transcript_turns(vault, "bots/proj/briefings/sessions/s1.md", projects_root=tmp_path / "projects")
    assert turns == ["use yarn not npm in this repo"]
```

`tests/unit/test_cli_reclassify.py`:

```python
from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser


def test_reclassify_registered_as_advanced_command():
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "reclassify" in COMMANDS and "reclassify" in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["reclassify", "--apply"])
    assert ns.command == "reclassify" and ns.apply is True
    ns = _build_parser().parse_args(["reclassify", "--undo", "20260901T000000"])
    assert ns.undo == "20260901T000000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_reclassify.py tests/unit/test_cli_reclassify.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.reclassify'`

- [ ] **Step 3: Write `core/reclassify.py`**

Create `src/mnemo/core/reclassify.py` implementing the API above. Concrete requirements the tests pin down:

```python
"""One-time grading of the legacy feedback vault under the evidence rules.

Every live ``shared/feedback/*.md`` page gets one of four verdicts from a Haiku
batch call that sees the rule plus what its source briefings and transcripts
actually contain. ``apply`` records every byte it touches under
``shared/_archive/reclassify-<run_id>/`` so ``undo`` is exact.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from mnemo.core import corrections, llm
from mnemo.core.extract.inbox.rendering import _extract_body
from mnemo.core.filters import derive_rule_slug, parse_frontmatter
from mnemo.core.transcript import user_turns

VERDICTS = ("keep", "demote", "merge", "archive")
PLAN_FILENAME = "reclassify-plan.json"
_TURN_CAP, _TURN_CHARS, _DECISIONS_CHARS = 40, 300, 1500

RECLASSIFY_SYSTEM_PROMPT = (
    "You grade rules that were auto-extracted from coding sessions. For each rule "
    "decide ONE verdict:\n"
    "- keep: a USER QUOTE in the provided context supports this rule as something the "
    "user told the assistant to do or not do. You MUST copy that quote verbatim into "
    "`quote` and name its briefing path in `source`.\n"
    "- demote: real, reusable project knowledge (a config value, a deploy step, an API "
    "gotcha) but no user quote establishes it as a correction.\n"
    "- merge: states the same rule as another slug in this batch or in the known-slugs "
    "list; put that slug in `target`.\n"
    "- archive: generic best practice any engineer knows, session narrative, a one-off "
    "decision, or text that reads like tool instructions rather than a rule.\n"
    "Output JSON only: {\"verdicts\": [{\"slug\": ..., \"verdict\": ..., \"target\": ..., "
    "\"quote\": ..., \"source\": ..., \"reason\": ...}]} with one entry per input slug."
)


@dataclass(frozen=True)
class RuleDoc:
    path: Path
    slug: str
    name: str
    fm: dict
    body: str
    sources: list


@dataclass
class Verdict:
    slug: str
    verdict: str
    target: Optional[str] = None
    quote: Optional[str] = None
    source: Optional[str] = None
    reason: str = ""


@dataclass
class Plan:
    run_id: str
    llm_calls: int
    verdicts: list


@dataclass
class ApplyReport:
    kept: int = 0
    demoted: int = 0
    merged: int = 0
    archived: int = 0
    archive_dir: Optional[Path] = None
```

Then implement, in this order, each as a plain function: `collect_rules`, `transcript_turns` (glob `projects_root/*/<session_id>.jsonl`, parse jsonl lines tolerant of bad JSON, `user_turns`, cap), `context_for`, `build_prompt` (rules rendered as `- <slug>: <name>\n  body: <first 500 chars>\n  context: ...`, then `Known slugs: ...`), `parse_verdicts` (`llm._parse_llm_json`, tolerate missing keys, lower-case verdict), `validate` (rules per the test: unknown verdict → archive; merge with target not in `rules_by_slug` → demote; keep whose quote does not `corrections.quote_matches_turn` any Corrections item of the cited briefing NOR any transcript turn of any cited briefing → demote), `plan` (batches of `batch_size`, `run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")`, counts calls, a failed call marks its batch's rules as `demote` with reason `llm-error` and continues), `save_plan`/`load_plan` (JSON via `asdict`), `apply` (signature `apply(vault_root, plan, *, rebuild_indexes=True)`), and `undo`.

`apply` details:
1. `arch = vault_root/"shared"/"_archive"/f"reclassify-{plan.run_id}"`; create `originals/`, `merged/`, `archived/`.
2. Copy `.mnemo/extraction-state.json` to `originals/extraction-state.json` (if it exists) and load it as a dict.
3. For each verdict, locate `shared/feedback/<slug>.md`; skip silently if missing. Copy it to `originals/<slug>.md` first.
   - keep: rewrite frontmatter: replace/insert `confidence: verified` after the `stability:` line (or before `sources:` if absent) and append an `evidence:` block using `inbox.rendering._render_nested_block("evidence", {"quote":..., "source":...})` just before the closing `---`. Preserve the rest byte-for-byte.
   - demote: new text = original with `type: feedback` → `type: reference`, `confidence: inferred` and `demoted_from: feedback` inserted after `type:`; write to `shared/reference/<slug>.md` (create dir; if the destination already exists, copy it to `originals/reference__<slug>.md` first) and delete the feedback file. State: pop `feedback/<slug>`, set `reference/<slug>` = `{"source_files": <from fm sources>, "source_hash": <old or "">, "written_hash": <sha256 of new content, "sha256:" prefix>, "written_at": run_id, "status": "auto_promoted", "last_sync": run_id}`.
   - merge: target = `shared/feedback/<target>.md`; copy target to `originals/<target>.md` if not already copied; append missing `  - <src>` lines to the target's `sources:` block; move this file to `merged/<slug>.md`. State: `feedback/<slug>.status = "dismissed"` (create the entry from the page's frontmatter `sources` when the state has none — a dismissed entry is what stops the next extraction from re-creating the page); extend `feedback/<target>.source_files` (create likewise).
   - archive: move to `archived/<slug>.md`; state `feedback/<slug>.status = "dismissed"` (create if absent, as above).
4. Write `manifest.json` with every move (`from`, `to`, `target`, `target_original`).
5. Write the state file back (`json.dumps(indent=2)`), then if `rebuild_indexes`: `rule_activation.write_index(vault_root, rule_activation.build_index(vault_root))` and the reflex equivalent, both inside try/except logging via `errors.log_error`.

`undo(vault_root, run_id)`: read the manifest; for each move restore `originals/<slug>.md` to `from`; delete `to` when it lies outside the archive dir and was created by the run (demote); restore `originals/<target>.md` over the target for merges; restore `originals/extraction-state.json`; return the number of files restored. Leave the archive dir in place (it is the audit trail).

- [ ] **Step 4: Write the CLI command**

Create `src/mnemo/cli/commands/reclassify.py`:

```python
"""`mnemo reclassify` — grade the legacy feedback vault under the evidence rules.

Plan (default): calls the LLM, prints the verdict table, saves the plan to
.mnemo/reclassify-plan.json. `--apply` executes the saved plan with no LLM
calls. `--undo RUN_ID` restores a previous run byte-for-byte.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("reclassify")
def cmd_reclassify(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core import config as config_mod
    from mnemo.core import reclassify as R

    vault = cli._resolve_vault()
    cfg = config_mod.load_config()

    if getattr(args, "undo", None):
        n = R.undo(vault, args.undo)
        print(f"restored {n} file(s) from reclassify-{args.undo}")
        return 0

    if getattr(args, "apply", False):
        plan = R.load_plan(vault)
        if plan is None:
            print("no saved plan — run `mnemo reclassify` first")
            return 1
        report = R.apply(vault, plan)
        print(f"kept {report.kept} · demoted {report.demoted} · merged {report.merged} · archived {report.archived}")
        print(f"undo with: mnemo reclassify --undo {plan.run_id}")
        return 0

    rules = R.collect_rules(vault)
    est = (len(rules) + 9) // 10
    limit = getattr(args, "limit", None)
    if limit:
        est = (min(limit, len(rules)) + 9) // 10
    print(f"{len(rules)} feedback rule(s) · about {est} {cfg['extraction']['model']} call(s)")
    if not getattr(args, "yes", False):
        answer = input("continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 2
    plan = R.plan(vault, model=cfg["extraction"]["model"], timeout=cfg["extraction"]["subprocessTimeout"],
                  limit=limit)
    R.save_plan(vault, plan)
    counts = {v: sum(1 for x in plan.verdicts if x.verdict == v) for v in R.VERDICTS}
    for v in plan.verdicts:
        extra = f" → {v.target}" if v.verdict == "merge" else (f' · "{v.quote}"' if v.verdict == "keep" else "")
        print(f"  {v.verdict:8} {v.slug}{extra}")
    print(f"\n{counts} · {plan.llm_calls} LLM call(s)")
    print("plan saved — review, then `mnemo reclassify --apply`")
    return 0
```

Register it: add `reclassify,` to the import list in `src/mnemo/cli/commands/__init__.py` (alphabetical, next to `recall_sessions`), add `"reclassify"` to `ADVANCED_COMMANDS` in `parser.py`, and add the subparser next to `dedup-rules`:

```python
    reclassify = sub.add_parser(
        "reclassify",
        help="grade legacy feedback rules under the evidence rules (plan by default)",
    )
    reclassify.add_argument("--apply", action="store_true", help="execute the saved plan")
    reclassify.add_argument("--undo", metavar="RUN_ID", help="restore a previous run")
    reclassify.add_argument("--limit", type=int, help="grade at most N rules (for a trial)")
    reclassify.add_argument("--yes", "-y", action="store_true", help="skip the cost confirmation")
```

- [ ] **Step 5: Run the tests**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider tests/unit/test_reclassify.py tests/unit/test_cli_reclassify.py tests/cli`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/reclassify.py src/mnemo/cli/commands/reclassify.py src/mnemo/cli/parser.py src/mnemo/cli/commands/__init__.py tests/unit/test_reclassify.py tests/unit/test_cli_reclassify.py
git commit -m "feat(cli): mnemo reclassify — grade legacy feedback rules with a byte-exact undo"
```

---

### Task 11: Docs, changelog, full suite

**Files:**
- Modify: `docs/configuration.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document**

In `docs/configuration.md`, in the reflex `fieldWeights` table add the `evidence` row (2.5, "the verbatim user quote a feedback rule was built from"). Add a section **Rule frontmatter written by extraction** listing `confidence: verified | inferred`, `evidence: {quote, source}`, `demoted_from: feedback`, with one sentence each. Add `mnemo reclassify` to the advanced-commands list with the three modes.

In `CHANGELOG.md` add above `## [1.0.0]`:

```markdown
## [Unreleased]

### Changed

- **Feedback rules now require evidence.** The session briefing carries a
  `## Corrections` section quoting the user verbatim (checked mechanically
  against the transcript; fabricated quotes are dropped). A feedback page
  reaches `shared/feedback/` only when it cites one of those quotes as
  `evidence:` and the quote verifies against the cited briefing
  (`confidence: verified`). Everything else is staged as an inferred
  `reference` page in `shared/_inbox/reference/` for review — including
  feedback-typed pages from Claude Code's own auto-memory, which carry no
  user quote.
- **Extraction reinforces existing rules instead of minting duplicates.** The
  prompt lists the vault's existing slugs, and a similarity pass redirects a
  page onto an existing slug when it states the same rule, so `source_count`
  accrues and universal promotion can fire.
- The reflex scores the evidence quote as its own field (weight 2.5).

### Added

- `mnemo reclassify` — grades the existing feedback vault under the same
  rules (keep / demote / merge / archive) with a saved plan, `--apply`, and a
  byte-exact `--undo`.
- Prompt-echo guard: pages that repeat the extractor's own instructions are
  rejected. E-mails, API tokens and long hex ids are redacted from rule
  bodies before they are written.
```

- [ ] **Step 2: Run the full suite**

Run: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider`
Expected: all pass, 2 skipped, count ≥ 2047 + new tests. Fix anything red before continuing; do not skip tests.

- [ ] **Step 3: Commit and push**

```bash
git add docs/configuration.md CHANGELOG.md
git commit -m "docs: evidence gate, reclassify, and reflex evidence field"
git push -u origin feat/ws-a-extraction-evidence
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(extract): evidence-gated feedback, slug reinforcement, reclassify" --body-file - <<'EOF'
Workstream A of docs/superpowers/specs/2026-09-01-corrections-layer-design.md.

- Briefings emit a verified `## Corrections` section from the user's verbatim turns.
- Feedback pages need `evidence` that verifies against the briefing; unverified pages stage as inferred reference.
- Existing-rules prompt fragment + similarity redirect so re-learned rules reinforce the existing slug.
- Prompt-echo guard and PII redaction.
- Reflex scores the evidence quote (weight 2.5).
- `mnemo reclassify` with saved plan, `--apply`, byte-exact `--undo`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013VXyVCP49gfghb87UHLCyD
EOF
```

---

## After merge (maintainer, not part of the PR)

1. `mnemo reclassify --limit 30` on the live vault; read the 30 verdicts against the audit sample.
2. `mnemo reclassify` (full, ~150 calls) → inspect the table → `mnemo reclassify --apply`.
3. `mnemo doctor`, `mnemo recall`; record the before/after rule counts in memory.
4. Start WS-B.
