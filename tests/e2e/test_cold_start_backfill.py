# tests/e2e/test_cold_start_backfill.py
"""Cold-start backfill, end to end: a transcript on disk must stop at review.

The chain under test is the one a brand-new user actually gets:

    SessionStart hook  →  mnemo backfill --install-run  →  discover  →  ledger
      →  harvest (LLM)  →  bots/<agent>/memory/*.md stamped `origin: backfill`
      →  mnemo extract  →  the origin gate  →  shared/_inbox/<type>/
      →  mnemo doctor

Everything in that chain is the real code. Two things are not:

* ``llm.call`` is stubbed — one stub serving both the harvest prompt and the
  extraction prompts, dispatched on the system prompt it was handed. No
  network, no ``claude`` CLI, so this file runs by default rather than behind
  ``MNEMO_E2E=1`` the way ``test_real_llm.py`` does.
* the *process boundary* in ``_spawn_detached_backfill`` — the hook's real
  scheduling logic (config gates, ``installRunDone``, the spawn lock) runs
  untouched, but instead of ``Popen``-ing a detached child that would re-read
  the developer's own config and sweep their real transcript history, the
  replacement runs ``cli.main(["backfill", "--install-run"])`` in-process from
  the cwd the hook chose. That is the narrowest seam that still exercises the
  hook's decision *and* the CLI it decides to run. (``tests/conftest.py`` has
  an autouse fixture neutralising the same attribute suite-wide; re-patching
  it here overrides that for this test only, which is why the guard stays on
  for everyone else.)

What the assertions are for: seven separate ways of defeating the origin
stamp were found while this feature was built, and every one of them put an
unreviewed LLM reconstruction into ``shared/``. The unit tests cover them one
at a time against ``run_extraction``; these prove they stay shut when the
stamp comes from a real harvest of a real transcript instead of a hand-written
fixture.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.core import llm as llm_mod
from mnemo.core.backfill import ledger
from mnemo.core.extract import prompts
from mnemo.hooks import session_start

# The suite-wide guard binds `_spawn_detached_backfill` at fixture time; this
# file replaces it again per-test, so hold the real one to prove we replaced
# something rather than shadowing a name that no longer exists.
_REAL_SPAWN = session_start._spawn_detached_backfill


# --------------------------------------------------------------------------
# fixtures on disk
# --------------------------------------------------------------------------

def _encode_cwd(cwd: str) -> str:
    """Claude Code's project-dir encoding: every separator becomes a dash.

    Mirrors the encoder inlined in ``session_end._resolve_session_jsonl_path``
    (both separators, so a Windows ``C:\\Users\\me\\repo`` encodes the same way
    it does in production). ``discover.decode_project_dir`` is the inverse and
    is deliberately lossy: ``tmp_path`` always contains dashes
    (``pytest-of-<user>/pytest-N``), so the decode never round-trips here and
    discovery falls through to ``_fallback_agent`` — the last dash-segment of
    the encoded name, i.e. the repo's own directory name. That agrees with
    ``resolve_canonical_agent``'s answer for the same repo (the git root's
    directory name) for any repo whose name has no dash, which is why the
    fixture repos below are called ``repo`` and ``other``.
    """
    encoded = cwd.replace(os.sep, "-")
    if os.altsep:
        encoded = encoded.replace(os.altsep, "-")
    return encoded


def _write_transcript(home: Path, repo: Path, session_id: str) -> Path:
    """Plant one archived session with enough mutations to clear the gate."""
    project_dir = home / ".claude" / "projects" / _encode_cwd(str(repo))
    project_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "user", "message": {"role": "user",
                                     "content": "always run pytest before pushing"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Understood."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}},
        ]}},
    ]
    path = project_dir / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    return path


def _make_repo(home: Path, name: str) -> Path:
    repo = home / name
    (repo / ".git").mkdir(parents=True)
    return repo


# --------------------------------------------------------------------------
# the stubbed LLM
# --------------------------------------------------------------------------

def _response(pages: list[dict]) -> llm_mod.LLMResponse:
    return llm_mod.LLMResponse(
        text=json.dumps({"pages": pages}),
        total_cost_usd=0.0,
        input_tokens=1,
        output_tokens=1,
        api_key_source="subscription",
        raw={},
    )


def _stub_llm(monkeypatch, *, harvest_pages: list[dict], extract_pages: list[dict]):
    """One stub for both callers, dispatched on the system prompt.

    ``harvest`` and the extraction cluster loop both reach ``llm.call`` through
    the same module attribute, so a single patch covers the whole run. Records
    the system prompts seen so a test can prove the harvest leg actually ran
    rather than silently no-op'ing.
    """
    seen: list[str] = []

    def fake_call(prompt, *, system=None, model=None, timeout=None, **kw):
        seen.append(system or "")
        if system == prompts.HARVEST_SYSTEM_PROMPT:
            return _response(harvest_pages)
        return _response(extract_pages)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    return seen


# --------------------------------------------------------------------------
# the hook, with only the process boundary replaced
# --------------------------------------------------------------------------

def _run_session_start(monkeypatch, repo: Path, session_id: str = "S1") -> list[int]:
    """Fire the real SessionStart hook; run its chosen sweep in-process.

    Returns the exit codes the backfill CLI produced. The hook logs and
    swallows every exception out of the scheduling block, so nothing inside
    the replacement may assert — a failed assertion would vanish into
    ``.errors.log`` and the test would pass on a broken feature. Results come
    back through this list and are asserted by the caller.
    """
    assert session_start._spawn_detached_backfill is not _REAL_SPAWN, (
        "conftest's autouse spawn guard is gone — a real sweep could escape"
    )
    codes: list[int] = []

    def fake_spawn(cwd=None):
        prior = os.getcwd()
        os.chdir(cwd or prior)
        try:
            codes.append(cli.main(["backfill", "--install-run"]))
        finally:
            os.chdir(prior)

    monkeypatch.setattr(session_start, "_spawn_detached_backfill", fake_spawn)
    monkeypatch.setattr(
        sys, "stdin",
        io.StringIO(json.dumps(
            {"session_id": session_id, "cwd": str(repo), "source": "startup"}
        )),
    )
    assert session_start.main() == 0
    return codes


def _install(home: Path, monkeypatch) -> Path:
    vault = home / "vault"
    assert cli.main(
        ["init", "--yes", "--vault-root", str(vault), "--no-mirror", "--quiet"]
    ) == 0
    config_path = vault / "mnemo.config.json"
    # ``backfill.autoOnFirstSession`` defaults to False — the first sweep is
    # opt-in. These tests exercise the automatic first-session chain, so they
    # opt in explicitly, the same way the hook's unit tests do.
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg.setdefault("backfill", {})["autoOnFirstSession"] = True
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config_path))
    return vault


def _errors_log(vault: Path) -> str:
    log = vault / ".errors.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


# --------------------------------------------------------------------------
# 1. the whole chain
# --------------------------------------------------------------------------

_HARVEST_PAGES = [
    {"slug": "run-pytest-before-push", "type": "feedback",
     "name": "Run pytest before pushing",
     "description": "Always run the suite before pushing",
     "body": "Run pytest before every push."},
    {"slug": "layout", "type": "project", "name": "Repo layout",
     "description": "Where things live",
     "body": "Source lives in src/, tests in tests/."},
]


def test_a_transcript_becomes_a_staged_page_and_goes_no_further(
    tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """Transcript → memory file → _inbox, for both extraction pipelines.

    ``feedback`` goes through the LLM cluster path (``inbox/paths``' routing
    gate); ``project`` goes through ``extract/promote.py``, a separate 1:1
    pipeline that writes straight to ``shared/project/`` with no ``_inbox``
    hop at all. Project is the type backfill produces most, so a gate that
    only covered the cluster path would leak the bulk of a sweep.
    """
    vault = _install(tmp_home, monkeypatch)
    repo = _make_repo(tmp_home, "repo")
    _write_transcript(tmp_home, repo, "abc-123")
    seen = _stub_llm(
        monkeypatch,
        harvest_pages=_HARVEST_PAGES,
        extract_pages=[{
            "slug": "run-pytest-before-push", "type": "feedback",
            "name": "Run pytest before pushing", "description": "d",
            "body": "Run pytest before every push.",
            "source_files": ["bots/repo/memory/run-pytest-before-push.md"],
        }],
    )

    codes = _run_session_start(monkeypatch, repo)

    # -- the hook scheduled a sweep, and the sweep is the thing that finished
    assert codes == [0], f"backfill did not run cleanly; errors: {_errors_log(vault)}"
    assert prompts.HARVEST_SYSTEM_PROMPT in seen, "harvest never called the LLM"
    assert ledger.load(vault)["installRunDone"] is True
    assert not ledger.spawn_lock_path(vault).exists(), "spawn lock never released"

    # -- harvest wrote memory files, stamped
    memory = vault / "bots" / "repo" / "memory"
    harvested = memory / "run-pytest-before-push.md"
    assert harvested.exists(), sorted(p.name for p in memory.glob("*"))
    assert "origin: backfill" in harvested.read_text(encoding="utf-8")

    # -- extract twice: the end-of-extract reconciler runs on *every* extract,
    #    so a single pass would only prove the apply-time gate.
    assert cli.main(["extract"]) == 0
    assert cli.main(["extract"]) == 0

    shared = vault / "shared"
    # The harvested feedback page cites no ``## Corrections`` quote (its source
    # is a memory file, which cannot carry one), so the evidence gate demotes
    # it to a staged ``reference`` page. Where it stages is what this test is
    # about; that it stages under ``reference`` rather than ``feedback`` is the
    # gate's business, tested in tests/unit/test_extract_evidence_gate.py.
    assert (shared / "_inbox" / "reference" / "run-pytest-before-push.md").exists()
    assert not (shared / "reference" / "run-pytest-before-push.md").exists()
    assert not (shared / "feedback" / "run-pytest-before-push.md").exists()
    # promote.py names project pages "<agent>__<slug>".
    assert (shared / "_inbox" / "project" / "repo__layout.md").exists()
    assert not (shared / "project" / "repo__layout.md").exists()

    # The staged pages carry the stamp forward, so a later extract (or a
    # reader like doctor) can still tell what they are.
    staged = (shared / "_inbox" / "reference" / "run-pytest-before-push.md").read_text(
        encoding="utf-8"
    )
    assert "origin: backfill" in staged
    assert "origin: backfill" in (
        shared / "_inbox" / "project" / "repo__layout.md"
    ).read_text(encoding="utf-8")

    # -- a staged page must not reach the user: neither index sees it, and the
    #    SessionStart injection envelope cannot name it.
    from mnemo.core import rule_activation
    from mnemo.core.reflex import index as reflex_index

    ra = rule_activation.build_index(vault)
    assert "run-pytest-before-push" not in ra["rules"]
    assert "repo__layout" not in ra["rules"]
    assert reflex_index.build_index(vault)["docs"] == {}

    rule_activation.write_index(vault, ra)
    payload = session_start._build_injection_payload(vault, current_project="repo")
    assert "pytest" not in payload and "layout" not in payload

    # -- doctor tells the user there is something to review
    capsys.readouterr()
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "staged in _inbox/ awaiting review" in out
    assert "reference/run-pytest-before-push" in out
    assert "project/repo__layout" in out
    # ...and does not misfile them as a reconciliation backlog.
    assert "cross universalThreshold but remain in _inbox/" not in out


# --------------------------------------------------------------------------
# 2. universal promotion, from a real harvest
# --------------------------------------------------------------------------

def test_a_backfilled_source_gates_a_page_that_a_live_sibling_would_promote(
    tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two projects, one of them reconstructed → still staged.

    A page whose sources span ``scoping.universalThreshold`` projects is moved
    into ``shared/`` with no review by two independent code paths
    (``apply._is_universal_promotion`` and ``extract._reconcile_universal_
    promotions``). Here one source is a live hand-authored memory file and the
    other came out of a real harvest, which is the mixed-origin case: under an
    ``all``-instead-of-``any`` reading the page carries no stamp, spans two
    projects, and walks past both gates.

    Extraction runs twice — the reconciler fires at the tail of every extract,
    and it re-reads the stamp off the staged file rather than off the page it
    was handed, so pass two is where a stamp that failed to survive the
    harvest → scanner → render round-trip would show up.
    """
    vault = _install(tmp_home, monkeypatch)
    repo = _make_repo(tmp_home, "repo")
    _write_transcript(tmp_home, repo, "abc-123")

    # A live, unstamped memory file in a second project, same lesson. Its
    # filename deliberately differs from the harvested one: extraction state is
    # keyed ``<type>/<slug>`` with no project segment, so two projects holding
    # the same memory *filename* share one entry and only one of them stays
    # dirty-tracked afterwards. That collision is real (see the third test
    # below) but it is not what this test is about.
    live = vault / "bots" / "other" / "memory"
    live.mkdir(parents=True)
    (live / "always-run-tests.md").write_text(
        "---\nname: Always run tests\ntype: feedback\n"
        "description: Always run the suite before pushing\n---\n\n"
        "Run pytest before every push.\n",
        encoding="utf-8",
    )

    _stub_llm(
        monkeypatch,
        harvest_pages=[_HARVEST_PAGES[0]],
        extract_pages=[{
            "slug": "run-pytest-before-push", "type": "feedback",
            "name": "Run pytest before pushing", "description": "d",
            "body": "Run pytest before every push.",
            "source_files": [
                "bots/repo/memory/run-pytest-before-push.md",
                "bots/other/memory/always-run-tests.md",
            ],
        }],
    )

    assert _run_session_start(monkeypatch, repo) == [0], _errors_log(vault)
    assert (vault / "bots" / "repo" / "memory" /
            "run-pytest-before-push.md").exists()

    assert cli.main(["extract"]) == 0
    assert cli.main(["extract"]) == 0

    shared = vault / "shared"
    # Unevidenced feedback is demoted to ``reference`` by the evidence gate, so
    # the sacred dir this test guards is ``shared/reference/``; assert on both
    # so the check cannot pass merely because the type changed.
    assert not (shared / "reference" / "run-pytest-before-push.md").exists(), (
        "a mixed-origin page was universally promoted into the sacred dir"
    )
    assert not (shared / "feedback" / "run-pytest-before-push.md").exists()
    assert (shared / "_inbox" / "reference" / "run-pytest-before-push.md").exists()


# --------------------------------------------------------------------------
# 3. REGRESSION — the stamp must survive a run that cannot see it
# --------------------------------------------------------------------------

def test_a_later_extract_must_not_launder_an_already_staged_page(
    tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """An eighth way past the origin stamp, found writing this file — now shut.

    ``_parse_pages_from_response`` computes ``origin_backfill`` by asking
    whether any of the page's ``source_files`` is a memory file *in the chunk
    being processed right now* that carries the stamp. Nothing persists that
    answer: the extraction-state entry has no origin field, and the apply-time
    branches read the flag off the page rather than off the file already
    staged under the same key.

    So the stamp only holds for as long as the backfill source keeps turning
    up in the chunk. It stops turning up as soon as it is no longer dirty,
    which is *the run after the first one*. The sequence below is the ordinary
    life of a vault:

    1. backfill harvests project ``repo``; ``mnemo extract`` stages the page in
       ``shared/_inbox/feedback/`` — correct;
    2. later, a live session in project ``other`` writes a memory file teaching
       the same lesson. It is the only dirty file, so it is the only thing in
       the chunk, and the model — which can only cite what it was shown —
       returns that one slug citing that one source;
    3. the page now has ``origin_backfill=False`` and exactly one source, so
       ``_target_path_for_page`` takes the single-source auto-promote door
       straight into ``shared/feedback/``.

    Note *which* door: this is not the universal-promotion path Task 6b closed,
    it is the first gate Task 6 added, reached with the flag already gone. The
    staged ``_inbox`` copy is left behind, so the vault ends up holding both.

    Closing that one door was not enough, which was checked rather than assumed:
    teaching ``_target_path_for_page`` to read the staged file's stamp left
    this test failing, because the page is then one source short of auto-promote
    but its *merged* sources still span two projects and it left by the
    universal door instead — landing in ``shared/`` with the ``_inbox`` copy
    deleted that time. The fix had to reach every gate at once.

    Task 9b made origin **sticky** rather than derived:
    ``StateEntry.origin_backfill`` persists the answer and
    ``inbox/apply._resolve_sticky_origin`` puts it back onto the page before
    the first gate reads it, so all four routes into ``shared/`` see the same
    answer on every run. Vaults whose state file predates the field recover it
    by reading the staged page, which still carries the stamp. The pieces are
    unit-tested in ``tests/unit/test_extract_origin_sticky.py``; this stays the
    end-to-end proof, from a real transcript.

    The same laundering happens without step 2 being a different lesson at all:
    extraction state is keyed ``<type>/<slug>`` with no project segment, so a
    live memory file sharing the harvested file's *filename* in another project
    collides on one entry, and whichever file loses the collision drops out of
    every later chunk. Stickiness does not repair that collision — it only
    makes it harmless for the origin gate, since both files answer to the one
    sticky entry. Fixing the key is still its own task.
    """
    vault = _install(tmp_home, monkeypatch)
    repo = _make_repo(tmp_home, "repo")
    _write_transcript(tmp_home, repo, "abc-123")

    emitted = {
        "pages": [{
            "slug": "run-pytest-before-push", "type": "feedback",
            "name": "Run pytest before pushing", "description": "d",
            "body": "Run pytest before every push.",
            "source_files": ["bots/repo/memory/run-pytest-before-push.md"],
        }],
    }

    def fake_call(prompt, *, system=None, model=None, timeout=None, **kw):
        if system == prompts.HARVEST_SYSTEM_PROMPT:
            return _response([_HARVEST_PAGES[0]])
        return _response(emitted["pages"])

    monkeypatch.setattr(llm_mod, "call", fake_call)

    assert _run_session_start(monkeypatch, repo) == [0], _errors_log(vault)
    assert cli.main(["extract"]) == 0
    shared = vault / "shared"
    # Demoted to ``reference`` by the evidence gate — see the note in test 1.
    assert (shared / "_inbox" / "reference" / "run-pytest-before-push.md").exists()
    assert not (shared / "reference" / "run-pytest-before-push.md").exists()
    assert not (shared / "feedback" / "run-pytest-before-push.md").exists()

    # Weeks later: a live session in another project teaches the same lesson.
    live = vault / "bots" / "other" / "memory"
    live.mkdir(parents=True)
    (live / "always-run-tests.md").write_text(
        "---\nname: Always run tests\ntype: feedback\ndescription: run them\n"
        "---\n\nRun pytest before every push.\n",
        encoding="utf-8",
    )
    # It is the only dirty file, so it is the whole chunk — and the model can
    # only cite sources it was shown.
    emitted["pages"] = [{
        "slug": "run-pytest-before-push", "type": "feedback",
        "name": "Run pytest before pushing", "description": "d",
        "body": "Run pytest before every push.",
        "source_files": ["bots/other/memory/always-run-tests.md"],
    }]
    assert cli.main(["extract"]) == 0

    assert not (shared / "reference" / "run-pytest-before-push.md").exists(), (
        "a page staged for review was laundered into the sacred dir by a "
        "later extract that no longer saw its backfill source"
    )
    assert not (shared / "feedback" / "run-pytest-before-push.md").exists()
    # Both halves, always: the routing door leaves the staged copy behind
    # while the universal door consumes it, so asserting only that shared/ is
    # clean would pass against a fix that closed just one of them.
    assert (shared / "_inbox" / "reference" / "run-pytest-before-push.md").exists()
