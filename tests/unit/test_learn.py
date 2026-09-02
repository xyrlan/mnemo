"""`mnemo learn`: briefing + extraction on the current session, synchronously."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import learn as learn_mod
from mnemo.core import llm as llm_mod
from mnemo.core.backfill.discover import Transcript


# --- fixtures ---------------------------------------------------------------

SESSION_ID = "sess-abc"
QUOTE = "never retry on 4xx, only on 5xx"


def _events() -> list[dict]:
    """A session with a correction and ZERO file mutations (no tool_use)."""
    return [
        {"type": "user", "timestamp": "2026-09-01T10:00:00.000Z",
         "message": {"role": "user", "content": "add a retry helper"}},
        {"type": "assistant", "timestamp": "2026-09-01T10:01:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "sure, here is a plan"}]}},
        {"type": "user", "timestamp": "2026-09-01T10:02:00.000Z",
         "message": {"role": "user", "content": f"no — {QUOTE}"}},
    ]


BRIEFING_BODY = (
    "## TL;DR\nRetry helper.\n\n"
    "## Decisions made\n- axios interceptor. **Why:** exists.\n\n"
    "## Corrections\n"
    f'- "{QUOTE}" → Retry only on 5xx\n\n'
    "## Dead ends\n- none\n"
)

EMPTY_BRIEFING_BODY = (
    "## TL;DR\nNothing much.\n\n"
    "## Decisions made\n- none\n\n"
    "## Dead ends\n- none\n"
)


def _extraction_json(pages: list[dict]) -> str:
    return json.dumps({"pages": pages})


def _resp(text: str) -> llm_mod.LLMResponse:
    return llm_mod.LLMResponse(
        text=text, total_cost_usd=0.0, input_tokens=1, output_tokens=1,
        api_key_source="none", raw={},
    )


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    (root / "bots").mkdir()
    (root / "shared").mkdir()
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    return root


@pytest.fixture
def cwd_project(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin resolve_canonical_agent to a stable project name."""
    from mnemo.core import agent as agent_mod

    monkeypatch.setattr(
        agent_mod, "resolve_canonical_agent",
        lambda cwd: agent_mod.AgentInfo(name="proj", repo_root="/repo", has_git=True),
    )
    return "proj"


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    path = tmp_path / "transcripts" / f"{SESSION_ID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in _events()) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def found(monkeypatch: pytest.MonkeyPatch, transcript: Path):
    """Patch discover.find_transcripts to return the given paths, newest first."""
    def _apply(paths: list[Path]) -> None:
        ts = [
            Transcript(path=p, agent="proj", cwd="/repo", mtime=float(100 - i))
            for i, p in enumerate(paths)
        ]
        monkeypatch.setattr(
            "mnemo.core.backfill.discover.find_transcripts",
            lambda *, project=None, limit=None: list(ts),
        )
    _apply([transcript])
    return _apply


def _cfg(vault: Path) -> dict:
    return {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "hintThreshold": 5,
            "preferAPI": False,
            "subprocessTimeout": 60,
            "costSoftCap": None,
        },
    }


@pytest.fixture
def two_call_llm(monkeypatch: pytest.MonkeyPatch):
    """Stub llm.call: first call is the briefing, second the extraction."""
    calls: list[dict] = []

    def _apply(briefing_body: str, extraction_pages: list[dict]):
        queue = [_resp(briefing_body), _resp(_extraction_json(extraction_pages))]

        def fake_call(prompt, *, system, model, timeout):
            calls.append({"prompt": prompt, "system": system})
            if not queue:
                raise AssertionError("two_call_llm: queue exhausted")
            return queue.pop(0)

        monkeypatch.setattr(llm_mod, "call", fake_call)

    _apply.calls = calls  # type: ignore[attr-defined]
    return _apply


# --- newest_transcript ------------------------------------------------------

def test_newest_transcript_picks_the_newest_for_this_project(
    tmp_path: Path, cwd_project, found, transcript: Path,
):
    older = tmp_path / "transcripts" / "old.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    found([transcript, older])

    assert learn_mod.newest_transcript("/repo") == transcript


def test_newest_transcript_selects_by_session_id_regardless_of_order(
    tmp_path: Path, cwd_project, found, transcript: Path,
):
    older = tmp_path / "transcripts" / "old.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    found([transcript, older])

    assert learn_mod.newest_transcript("/repo", session_id="old") == older


def test_newest_transcript_returns_none_when_nothing_matches(cwd_project, found):
    found([])
    assert learn_mod.newest_transcript("/repo") is None


# --- learn: end to end ------------------------------------------------------

def test_learn_promotes_a_verified_rule_from_a_zero_mutation_session(
    vault: Path, cwd_project, found, transcript: Path, two_call_llm,
):
    src = f"bots/proj/briefings/sessions/{SESSION_ID}.md"
    two_call_llm(BRIEFING_BODY, [
        {
            "slug": "retry-5xx-only",
            "name": "Retry only on 5xx",
            "description": "d",
            "type": "feedback",
            "body": "Retry only 5xx.",
            "source_files": [src],
            "evidence": {"quote": QUOTE, "source": src},
        },
    ])

    report = learn_mod.learn(_cfg(vault), cwd="/repo")

    assert report.error == ""
    assert report.transcript == transcript
    # The briefing was written even though the session mutated zero files.
    assert report.briefing == vault / src
    assert report.briefing.exists()
    assert report.corrections == 1

    page = vault / "shared" / "feedback" / "retry-5xx-only.md"
    assert page.exists()
    assert "confidence: verified" in page.read_text(encoding="utf-8")

    assert [e["slug"] for e in report.learned] == ["retry-5xx-only"]
    assert report.learned[0]["confidence"] == "verified"
    assert report.learned[0]["quote"] == QUOTE
    assert report.learned[0]["name"] == "Retry only on 5xx"
    assert report.hint == ""

    # The reflex index was rebuilt, so the new rule is retrievable on the very
    # next prompt. Its doc key is the page's ``slug`` — the same identifier
    # ``report.learned`` carries, now that rendered pages write ``slug:``
    # explicitly (#114) — and the evidence quote is indexed as its own field.
    index = json.loads((vault / ".mnemo" / "reflex-index.json").read_text(encoding="utf-8"))
    assert "retry-5xx-only" in index["docs"]
    assert index["docs"]["retry-5xx-only"]["projects"] == ["proj"]
    assert any(
        e["slug"] == "retry-5xx-only" and e["tf"]["evidence"] > 0
        for e in index["postings"].get("retry", [])
    )


def test_learn_with_nothing_to_learn_returns_the_hint(
    vault: Path, cwd_project, found, two_call_llm,
):
    two_call_llm(EMPTY_BRIEFING_BODY, [])

    report = learn_mod.learn(_cfg(vault), cwd="/repo")

    assert report.error == ""
    assert report.learned == []
    assert report.corrections == 0
    assert "nothing new" in report.hint
    assert "mnemo learn" in report.hint


def test_learn_dry_run_makes_no_llm_call_and_writes_nothing(
    vault: Path, cwd_project, found, transcript: Path, monkeypatch,
):
    def boom(*a, **k):
        raise AssertionError("dry_run must not call the LLM")

    monkeypatch.setattr(llm_mod, "call", boom)

    report = learn_mod.learn(_cfg(vault), cwd="/repo", dry_run=True)

    assert report.would_read == transcript
    assert report.briefing is None
    assert report.learned == []
    assert not (vault / "bots" / "proj").exists()
    assert not (vault / "shared" / "feedback").exists()


def test_learn_reports_a_held_lock_and_writes_no_pages(
    vault: Path, cwd_project, found, two_call_llm,
):
    from mnemo.core import locks

    lock_path = vault / ".mnemo" / "extract.lock"
    with locks.try_lock(lock_path) as acquired:
        assert acquired
        two_call_llm(BRIEFING_BODY, [])
        report = learn_mod.learn(_cfg(vault), cwd="/repo")

    # The message names the benign outcome (the running pass takes this
    # briefing) and the one action worth taking, not the internal lock text.
    assert report.error == learn_mod.LOCK_HELD_MESSAGE
    assert "run `mnemo learn` again" in report.error
    assert report.learned == []
    assert not (vault / "shared" / "feedback").exists()


def test_learn_reports_a_failed_briefing_and_touches_nothing(
    vault: Path, cwd_project, found, monkeypatch,
):
    """Stage 1 raises on LLM/IO failure; `learn` never lets it escape."""
    def boom(*a, **k):
        raise llm_mod.LLMSubprocessError("claude exited 1")

    monkeypatch.setattr(llm_mod, "call", boom)

    report = learn_mod.learn(_cfg(vault), cwd="/repo")

    assert report.error.startswith("briefing failed:")
    assert "claude exited 1" in report.error
    assert report.briefing is None
    assert report.learned == []
    # No briefing was written, and the ledger was never touched.
    assert list((vault / "shared").iterdir()) == []


def test_learn_extracts_only_this_sessions_briefing(
    vault: Path, cwd_project, found, transcript: Path, monkeypatch,
):
    """A backlog of other briefings must not be swept into this run.

    Every dirty file in a maintainer's vault would otherwise become an
    unrequested (and billed) consolidation call.
    """
    stale = vault / "bots" / "other" / "briefings" / "sessions" / "sess-old.md"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        "---\ntype: briefing\n---\n\n## Corrections\n- \"always use tabs\" → Tabs\n",
        encoding="utf-8",
    )

    prompts_seen: list[str] = []
    queue = [_resp(BRIEFING_BODY), _resp(_extraction_json([]))]

    def fake_call(prompt, *, system, model, timeout):
        prompts_seen.append(prompt)
        if not queue:
            raise AssertionError("unexpected extra LLM call")
        return queue.pop(0)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    report = learn_mod.learn(_cfg(vault), cwd="/repo")

    assert report.error == ""
    # Exactly two calls: the briefing, then one consolidation prompt — and
    # that prompt carries this session's briefing, not the stale one.
    assert len(prompts_seen) == 2
    consolidation = prompts_seen[1]
    # The prompt embeds the OS-native path; compare in POSIX form so Windows passes.
    assert f"bots/proj/briefings/sessions/{SESSION_ID}.md" in consolidation.replace("\\", "/")
    assert "sess-old" not in consolidation


def test_run_extraction_with_only_skips_project_promotion(
    vault: Path, monkeypatch,
):
    """`only` must not let the zero-LLM project phase rewrite the vault."""
    from mnemo.core import extract as extract_mod

    project = vault / "bots" / "proj" / "memory" / "project_thing.md"
    project.parent.mkdir(parents=True)
    project.write_text("---\ntype: project\n---\nabout the thing\n", encoding="utf-8")

    monkeypatch.setattr(
        llm_mod, "call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM expected")),
    )

    summary = extract_mod.run_extraction(_cfg(vault), only="feedback/nothing-matches")

    assert summary.projects_promoted == 0
    assert not (vault / "shared" / "project").exists()


def test_learn_errors_when_there_is_no_transcript(vault: Path, cwd_project, found):
    found([])
    report = learn_mod.learn(_cfg(vault), cwd="/repo")
    assert "no transcript for this directory yet" in report.error
    assert report.transcript is None


def test_learn_errors_when_the_session_id_is_unknown(vault: Path, cwd_project, found):
    report = learn_mod.learn(_cfg(vault), cwd="/repo", session_id="nope")
    assert "no transcript with session id nope" in report.error


# --- briefing min_mutations -------------------------------------------------

def test_generate_session_briefing_still_skips_zero_mutation_sessions_by_default(
    vault: Path, transcript: Path, monkeypatch,
):
    from mnemo.core import briefing as briefing_mod

    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp(BRIEFING_BODY))

    assert briefing_mod.generate_session_briefing(transcript, "proj", {"extraction": {}}) is None
    assert briefing_mod.generate_session_briefing(
        transcript, "proj", {"extraction": {}}, min_mutations=0
    ) is not None


# --- second learn on the same session ---------------------------------------

def test_second_learn_on_the_same_session_says_already_learned(
    vault: Path, cwd_project, found, transcript: Path, monkeypatch,
):
    """Run twice, unchanged transcript: stage 1 is skipped, the hint says so.

    The first run consolidated the correction. The second must not regenerate
    the briefing (an LLM call whose different-but-equivalent body would
    re-dirty the file), and must not tell the user "nothing new: no
    corrections found" — there *were* corrections, they are already learned.
    """
    src = f"bots/proj/briefings/sessions/{SESSION_ID}.md"
    page = {
        "slug": "retry-5xx-only",
        "name": "Retry only on 5xx",
        "description": "d",
        "type": "feedback",
        "body": "Retry only 5xx.",
        "source_files": [src],
        "evidence": {"quote": QUOTE, "source": src},
    }
    queue = [_resp(BRIEFING_BODY), _resp(_extraction_json([page]))]
    calls: list[str] = []

    def fake_call(prompt, *, system, model, timeout):
        calls.append(system)
        if not queue:
            raise AssertionError("unexpected extra LLM call")
        return queue.pop(0)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    first = learn_mod.learn(_cfg(vault), cwd="/repo")
    assert first.error == ""
    assert [e["slug"] for e in first.learned] == ["retry-5xx-only"]
    assert len(calls) == 2

    second = learn_mod.learn(_cfg(vault), cwd="/repo")

    assert second.error == ""
    assert second.learned == []
    assert second.corrections == 1
    assert second.hint.startswith("already learned:")
    assert "1 correction(s)" in second.hint
    assert "mnemo status" in second.hint
    # Stage 1 was reused, not regenerated: no third LLM call.
    assert len(calls) == 2


def test_changed_transcript_regenerates_the_briefing(
    vault: Path, cwd_project, found, transcript: Path, monkeypatch,
):
    """The reuse is keyed on content: append to the transcript and it rebuilds."""
    from mnemo.core import briefing as briefing_mod

    bodies = [BRIEFING_BODY, BRIEFING_BODY.replace("Retry helper.", "Retry helper v2.")]

    def fake_call(prompt, *, system, model, timeout):
        return _resp(bodies.pop(0) if bodies else BRIEFING_BODY)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    cfg = _cfg(vault)
    out = briefing_mod.generate_session_briefing(
        transcript, "proj", cfg, min_mutations=0, reuse_unchanged=True
    )
    assert out is not None
    first_text = out.read_text(encoding="utf-8")
    assert "transcript_sha256: " in first_text

    # Unchanged: reused byte-for-byte.
    again = briefing_mod.generate_session_briefing(
        transcript, "proj", cfg, min_mutations=0, reuse_unchanged=True
    )
    assert again == out
    assert out.read_text(encoding="utf-8") == first_text

    # Changed: regenerated, and the stamp moves with it.
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "user", "timestamp": "2026-09-01T10:05:00.000Z",
            "message": {"role": "user", "content": "and one more thing"},
        }) + "\n")

    third = briefing_mod.generate_session_briefing(
        transcript, "proj", cfg, min_mutations=0, reuse_unchanged=True
    )
    assert third == out
    new_text = out.read_text(encoding="utf-8")
    assert new_text != first_text
    assert "Retry helper v2." in new_text


# --- scoped runs and the SessionEnd debounce --------------------------------

def test_scoped_run_leaves_last_run_alone(vault: Path, monkeypatch):
    """`only` must not push back the SessionEnd debounce watermark.

    `hooks.session_end._debounce_passes` reads `last_run`; if every `mnemo
    learn` advanced it, the automatic pass would be deferred a full interval
    each time the user used the five-minute loop.
    """
    import json as _json

    from mnemo.core import extract as extract_mod

    state_path = vault / ".mnemo" / "extraction-state.json"
    state_path.write_text(
        _json.dumps({"schema_version": 2, "last_run": "2026-01-01T00:00:00",
                     "entries": {}}),
        encoding="utf-8",
    )

    src = f"bots/proj/briefings/sessions/{SESSION_ID}.md"
    briefing = vault / src
    briefing.parent.mkdir(parents=True)
    briefing.write_text(
        "---\ntype: briefing\n---\n\n## Corrections\n"
        f'- "{QUOTE}" → Retry only on 5xx\n',
        encoding="utf-8",
    )

    from mnemo.core.extract import scanner as scanner_mod

    key = f"feedback/{scanner_mod._normalize_slug('briefing-' + SESSION_ID)}"

    monkeypatch.setattr(
        llm_mod, "call",
        lambda *a, **k: _resp(_extraction_json([{
            "slug": "retry-5xx-only", "name": "Retry only on 5xx",
            "description": "d", "type": "feedback", "body": "b",
            "source_files": [src],
            "evidence": {"quote": QUOTE, "source": src},
        }])),
    )

    extract_mod.run_extraction(_cfg(vault), only=key)

    after = _json.loads(state_path.read_text(encoding="utf-8"))
    assert after["last_run"] == "2026-01-01T00:00:00"
    # The per-file entry still advanced, so the next scan sees it as clean.
    assert key in after["entries"]

    # An unscoped run does advance it.
    extract_mod.run_extraction(_cfg(vault))
    unscoped = _json.loads(state_path.read_text(encoding="utf-8"))
    assert unscoped["last_run"] != "2026-01-01T00:00:00"
