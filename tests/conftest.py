"""Shared pytest fixtures for mnemo tests."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


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


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME to a temp dir so ~/.claude and ~/mnemo are isolated."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility
    return home


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
