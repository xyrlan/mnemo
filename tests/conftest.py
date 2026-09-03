"""Shared pytest fixtures for mnemo tests."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# Captured at import, before any fixture can patch it. This is the one place
# the suite is allowed to know where the developer's real vault lives -- and
# only so it can prove nothing touched it (#117).
_REAL_HOME = Path(os.environ.get("HOME") or os.path.expanduser("~")).resolve()
_REAL_VAULT = _REAL_HOME / "mnemo"


@pytest.fixture(autouse=True)
def _no_real_install_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop any test that runs the SessionStart hook from really backfilling.

    ``session_start.main`` schedules ``mnemo backfill --install-run`` as a
    detached child on a vault that has never had one. The child re-reads the
    *real* config from the *real* environment, so a test that patches
    ``paths.vault_root`` in-process does not contain it: it would sweep the
    developer's actual transcript history and spend real LLM calls doing it.
    The hook swallows spawn failures by design, so this never surfaces as a
    test failure — it just quietly happens.

    Tests that want to observe the spawn re-patch this attribute themselves;
    the ones that exercise the real function bind it at import time.
    """
    monkeypatch.setattr(
        "mnemo.hooks.session_start._spawn_detached_backfill", lambda **_kw: None
    )


@pytest.fixture(autouse=True)
def _no_real_detached_jobs(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test launch a real detached mnemo process.

    ``session_start.main`` runs ``run_due_jobs``, and a fresh tmp vault makes
    every autopilot job "due", so ``triggers.run_detached`` spawned a real
    ``mnemo autopilot tune bm25`` (and doctor/sweep/telemetry/reflex jobs)
    per hook test. Each child re-reads the developer's real config and vault
    and runs a 30–60 minute grid search; 2026-09-02 a test-heavy day left 46
    of them alive and the machine unusable. ``session_end.main`` likewise
    spawns real ``mnemo extract`` / ``mnemo briefing`` children — the latter
    calls the LLM.

    The stubs keep the bookkeeping the callers rely on (``mark_run``) and do
    nothing else. Tests that exercise a spawn function itself opt out with
    ``@pytest.mark.real_spawn`` and patch ``subprocess.Popen`` themselves.
    """
    if request.node.get_closest_marker("real_spawn"):
        return
    from mnemo.autopilot.core import triggers as _triggers

    def _fake_run_detached(*, vault_root, name, argv):  # noqa: ARG001
        _triggers.mark_run(vault_root=vault_root, name=name, success=True)

    monkeypatch.setattr("mnemo.autopilot.core.triggers.run_detached", _fake_run_detached)
    monkeypatch.setattr("mnemo.autopilot.core.scheduler.run_detached", _fake_run_detached, raising=False)
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_extraction", lambda *a, **k: None)
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_briefing", lambda *a, **k: None)


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a minimal vault directory tree and return its root.

    v0.4: no longer pre-creates ``wiki/`` — that dir is dead as of v0.4. Tests
    that specifically exercise legacy cleanup can seed it inline.
    """
    root = tmp_path / "vault"
    (root / "bots").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    return root


@pytest.fixture(autouse=True)
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test runs under a throwaway HOME.

    ``config.load_config`` falls back to ``~/mnemo/mnemo.config.json`` and
    ``paths.vault_root`` expands ``~``, so any hook ``main()`` called without
    an explicit config ran against the developer's vault -- and wrote to its
    ``.errors.log``, tripping the real circuit breaker for an hour after a
    noisy run (#117). Redirecting HOME closes that path for every test.

    ``MNEMO_CONFIG_PATH`` is always pointed at a (non-existent) file under the
    temp home: ``load_config`` returns defaults for a missing path, and those
    defaults resolve the vault under the temp HOME. Autouse fixtures run
    first, so a value already in the environment can only be the developer's
    shell exporting the real config -- exactly what must be overridden. A
    test's own ``monkeypatch.setenv`` runs later and still wins.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(home / "mnemo" / "mnemo.config.json"))
    return home


@pytest.fixture
def real_home() -> Path:
    """The developer's real HOME, as it was before ``tmp_home`` redirected it.

    Only for asserting that isolation holds; never build paths under it.
    """
    return _REAL_HOME


def _vault_fingerprint() -> tuple:
    """``(size, mtime_ns)`` of five real paths (the vault's error log and
    ``shared/``, ``~/.claude/projects``, and the Cursor and Codex config dirs
    a host adapter would write) that a leaky test would hit.

    Directory entries are shallow: a directory's mtime moves when an entry is
    created or removed inside it. Missing paths are ``None``.

    ``~/mnemo/.mnemo/`` is deliberately *not* watched. The developer's own
    Claude Code session keeps firing real hooks while the suite runs, and
    those atomically replace files there on every MCP call and every
    reflex/enrich emission (``core/mcp/session_state.py``) and rename both
    index files on SessionStart -- each bumping the directory's mtime. The
    harm #117 names is ``.errors.log`` (the circuit breaker); watching
    ``.mnemo/`` would only add false positives.
    """
    def st(p: Path):
        try:
            s = p.stat()
            return (s.st_size, s.st_mtime_ns)
        except OSError:
            return None
    return (
        st(_REAL_VAULT / ".errors.log"),
        st(_REAL_VAULT / "shared"),
        st(_REAL_HOME / ".claude" / "projects"),
        st(_REAL_HOME / ".cursor"),
        st(_REAL_HOME / ".codex"),
    )


@pytest.fixture(autouse=True)
def _real_vault_guard(request: pytest.FixtureRequest):
    """Fail any test that changes the real vault, ``~/.claude/projects``,
    ``~/.cursor``, or ``~/.codex``.

    Five ``stat`` calls before and after. Tests marked ``recall`` run against
    the real vault on purpose and are exempt.
    """
    if request.node.get_closest_marker("recall"):
        yield
        return
    before = _vault_fingerprint()
    yield
    after = _vault_fingerprint()
    if before != after:
        pytest.fail(
            f"test touched the real vault, ~/.claude/projects, ~/.cursor, or "
            f"~/.codex at {_REAL_VAULT} "
            f"(before={before}, after={after}); build a tmp vault and set "
            "MNEMO_CONFIG_PATH instead (see tests/unit/test_hook_session_start_backfill.py::_run_hook)"
        )


@pytest.fixture
def tmp_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect tempfile.gettempdir() to an isolated dir.

    Sets env vars AND patches tempfile.tempdir directly — CPython caches
    gettempdir() on first call, so the env vars alone would not affect an
    already-running session.
    """
    td = tmp_path / "tmp"
    td.mkdir()
    monkeypatch.setenv("TMPDIR", str(td))
    monkeypatch.setenv("TEMP", str(td))
    monkeypatch.setenv("TMP", str(td))
    monkeypatch.setattr(tempfile, "tempdir", str(td))
    return td


# --- v0.2 extraction fixtures ---

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def memory_fixture() -> Path:
    """Path to the static memory-file fixtures directory."""
    return FIXTURES_DIR / "memory_files"


@pytest.fixture
def llm_response_fixture() -> Path:
    """Path to the static LLM-response JSON fixtures."""
    return FIXTURES_DIR / "llm_responses"


@pytest.fixture
def populated_vault(tmp_vault: Path, memory_fixture: Path) -> Path:
    """tmp_vault pre-populated with 2 agents and their memory files.

    Layout:
        tmp_vault/bots/agent-a/memory/{feedback_use_yarn.md, MEMORY.md}
        tmp_vault/bots/agent-b/memory/{feedback_no_commits.md,
                                       feedback_no_commit_without_permission.md,
                                       project_china_portal.md,
                                       MEMORY.md}
    """
    import shutil

    a = tmp_vault / "bots" / "agent-a" / "memory"
    a.mkdir(parents=True)
    shutil.copy(memory_fixture / "feedback_use_yarn.md", a / "feedback_use_yarn.md")
    shutil.copy(memory_fixture / "MEMORY.md", a / "MEMORY.md")

    b = tmp_vault / "bots" / "agent-b" / "memory"
    b.mkdir(parents=True)
    shutil.copy(memory_fixture / "feedback_no_commits.md", b / "feedback_no_commits.md")
    shutil.copy(
        memory_fixture / "feedback_no_commit_without_permission.md",
        b / "feedback_no_commit_without_permission.md",
    )
    shutil.copy(memory_fixture / "project_china_portal.md", b / "project_china_portal.md")
    shutil.copy(memory_fixture / "MEMORY.md", b / "MEMORY.md")

    return tmp_vault


class MockCompletedProcess:
    """Stand-in for subprocess.CompletedProcess used by test_llm.py."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def mock_subprocess_run(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch subprocess.run in core.llm to return a queued list of results.

    Usage:
        def test_x(mock_subprocess_run):
            mock_subprocess_run([MockCompletedProcess(stdout='...')])
            ...
    """
    calls: list = []
    results: list = []

    def installer(queue: list):
        results.extend(queue)

    def fake_run(argv, input=None, capture_output=True, text=True, timeout=None, **kwargs):
        calls.append({"argv": argv, "input": input, "timeout": timeout, "kwargs": kwargs})
        if not results:
            raise AssertionError("mock_subprocess_run: result queue exhausted")
        item = results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    from mnemo.core import llm as _llm  # lazy import; llm.py may not exist yet

    monkeypatch.setattr(_llm, "_subprocess_run", fake_run, raising=False)
    installer.calls = calls  # type: ignore[attr-defined]
    return installer


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch):
    """Make time.sleep a no-op during tests so retry backoffs don't slow the suite."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda _s: None)


@pytest.fixture
def synthetic_index():
    """Return a function that seeds a reflex-index.json with one high-signal rule.

    The target rule's single source is under ``bots/mnemo/memory/`` so its only
    project is ``mnemo``. ``universal_threshold=1`` makes it universal — the
    hook test runs from ``tmp_vault`` whose directory name is ``vault`` (no
    ``.git``), so project-name matching would otherwise miss.

    Several low-signal noise rules are added so the vault-wide IDF is not
    degenerate (N=1 collapses IDF to ~0.29, which would keep the top score
    below the default absolute_floor of 2.0 — silencing the confident match).
    """
    def _apply(vault):
        from mnemo.core.reflex.index import build_index, write_index
        feedback = vault / "shared" / "feedback"
        feedback.mkdir(parents=True, exist_ok=True)

        # High-signal target rule.
        (feedback / "use-prisma-mock.md").write_text(
            "---\n"
            "name: use-prisma-mock\n"
            "description: Always use jest-mock-extended to mock Prisma in tests\n"
            "tags:\n"
            "  - prisma\n"
            "  - testing\n"
            "aliases:\n"
            "  - banco\n"
            "  - database\n"
            "sources:\n"
            "  - bots/mnemo/memory/mock.md\n"
            "stability: stable\n"
            "---\n"
            "Mock the Prisma client in tests using jest-mock-extended.\n",
            encoding="utf-8",
        )

        # Noise rules — keep IDF meaningful.
        noise = [
            ("use-yarn", "Prefer yarn over npm for installs", "yarn"),
            ("commit-strategy", "Small atomic commits with clear messages", "git"),
            ("review-etiquette", "Be kind and specific in code reviews", "review"),
            ("python-style", "Follow PEP8 and black formatting", "python"),
            ("docs-style", "Write clear, concise documentation", "docs"),
        ]
        for i, (name, desc, tag) in enumerate(noise):
            (feedback / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n"
                f"tags:\n  - {tag}\n"
                f"sources:\n  - bots/noise{i}/memory/x.md\n"
                f"stability: stable\n---\nBody for {name}.\n",
                encoding="utf-8",
            )

        idx = build_index(vault, universal_threshold=1)
        write_index(vault, idx)
    return _apply
