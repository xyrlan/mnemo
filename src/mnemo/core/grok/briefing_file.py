"""The session briefing for a Grok session, as a rules file Grok loads itself.

Grok drops ``additionalContext`` from SessionStart, so the block mnemo's
SessionStart hands a Claude session (the topic menu and the briefing) has to
reach a Grok session some other way (spec decision 6). Grok builds its first
turn from instruction files it reads *before* any hook runs, so the file is
written ahead of the session that reads it: each Grok SessionStart in a
project, and each Grok session's end once its own briefing exists, rewrites
it for the next session there.

Which file, measured on grok 1.0.60 (2026-10-16) and read from its source
(grok-build ``559751f``, ``xai-grok-agent/src/prompt/agents_md.rs``):

- ``<tree>/.grok/rules/mnemo-briefing.md``. Grok loads every ``*.md`` in a
  project's ``.grok/rules/`` on the cwd-to-git-root chain into the first user
  record, as an ``always_applied_workspace_rule``, and strips its
  frontmatter. Claude Code reads nothing under ``.grok/``, so a Claude
  session is untouched. Home-level rules (``$GROK_HOME/rules``) and
  ``~/.claude/CLAUDE.md`` reach every project, which a project's briefing
  must not.
- **Never committed.** The path goes into the repo's ``info/exclude``, which
  lives in the common git dir and so covers every worktree. Grok's own
  gitignore check reads ``.gitignore`` and the global excludes file but not
  ``info/exclude``, so the file still loads (measured: it did), and ``git
  worktree remove`` still removes a tree holding it (measured).
- **Every worktree.** The chain stops at the tree's own root, so the file is
  written into each tree ``git worktree list`` names. A tree created after
  the last write has none until its first Grok session starts; that session
  gets the briefing on its first tool call instead
  (:func:`mnemo.core.grok.hooks.stash`), as does any session whose
  ``prompt_context.json`` shows the file was not loaded.
- **Folder trust.** Grok drops project files from a folder its trust store
  does not trust when the session is headless (measured: the same file, the
  same repo, no codename with trust on). Trust is keyed by the main
  checkout, so one grant covers every worktree. mnemo grants none: trusting
  a folder lets its hooks and MCP servers run, which is the maintainer's
  call (``grok`` asks on the first interactive start). Until then the
  briefing goes out on the first tool call.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Mapping, Optional

RULES_DIR = (".grok", "rules")
FILE_NAME = "mnemo-briefing.md"
#: The line ``info/exclude`` gets, anchored at the tree's root.
EXCLUDE_LINE = "/" + "/".join(RULES_DIR + (FILE_NAME,))
#: Opens the file's body; the frontmatter above it never reaches the model.
MARKER = "[mnemo:briefing via=rules-file"
#: The frontmatter line that says the file is mnemo's to rewrite or remove.
OWNER_LINE = "written_by: mnemo"
PROMPT_CONTEXT = "prompt_context.json"

_GIT_TIMEOUT_S = 5


def rule_path(tree: Path) -> Path:
    return Path(tree).joinpath(*RULES_DIR) / FILE_NAME


def render(vault: Path, cfg: Mapping[str, Any], project: str) -> str:
    """The block a Claude SessionStart in ``project`` would inject, or ``""``.

    Built by the hook's own builder, with nothing logged as read: writing a
    file is not a session reading it. Off when ``injection.enabled`` is,
    like the Claude block. The per-session blocks (offers, notices) are left
    out: a file is read by whichever session starts next.
    """
    if not (cfg.get("injection") or {}).get("enabled", False):
        return ""
    from mnemo.hooks import session_start

    mode = session_start._briefing_mode(dict(cfg), "startup")
    return session_start._build_injection_payload(
        Path(vault),
        current_project=project,
        inject_briefing=(mode != "none"),
        session_id=None,
        source="grok-file",
        blocks=(),
        briefing_mode=mode,
        record=False,
    )


def file_text(text: str, project: str) -> str:
    """What the file holds for ``text``: mnemo's frontmatter, the marker, ``text``."""
    return (
        "---\n"
        f"description: mnemo's session briefing for {project}, rewritten by mnemo's hooks; never commit it\n"
        f"{OWNER_LINE}\n"
        "---\n"
        f"{MARKER} project={project}]\n"
        f"{text.rstrip()}\n"
    )


def _git(cwd: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd)] + list(args),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def trees(cwd: Path) -> List[Path]:
    """Every worktree of the repository holding ``cwd`` that is on disk.

    ``[]`` outside a git repository: with no root, Grok's chain would be the
    bare cwd, and a cwd of ``$HOME`` would make the file a home rule that
    every project loads.
    """
    out = _git(Path(cwd), "worktree", "list", "--porcelain")
    if out is None:
        return []
    found: List[Path] = []
    current: Optional[Path] = None
    skip = False
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            current, skip = Path(line[len("worktree "):]), False
        elif line == "bare" or line.startswith("prunable"):
            skip = True
        elif not line.strip():
            if current is not None and not skip and current.is_dir():
                found.append(current)
            current, skip = None, False
    return found


def _common_dir(tree: Path) -> Optional[Path]:
    out = _git(tree, "rev-parse", "--git-common-dir")
    if not out or not out.strip():
        return None
    common = Path(out.strip())
    return common if common.is_absolute() else (Path(tree) / common)


def ensure_excluded(tree: Path) -> bool:
    """``info/exclude`` of ``tree``'s repository lists the file. True when it does."""
    common = _common_dir(tree)
    if common is None:
        return False
    exclude = common / "info" / "exclude"
    try:
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    except OSError:
        return False
    if EXCLUDE_LINE in current.splitlines():
        return True
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        sep = "" if not current or current.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"{sep}# mnemo: the Grok session briefing, rewritten per session\n{EXCLUDE_LINE}\n")
    except OSError:
        return False
    return True


def _unsafe(tree: Path) -> bool:
    """A tree whose ``.grok/rules`` is a home rules dir: ``$HOME`` or a Grok home."""
    try:
        here = os.path.normcase(os.path.realpath(str(tree)))
        home = os.path.normcase(os.path.realpath(str(Path.home())))
    except OSError:
        return True
    if here == home:
        return True
    from mnemo.core.grok import paths

    for grok_home in [paths.grok_home()] + paths.grok_homes():
        if here == os.path.normcase(os.path.realpath(str(grok_home))):
            return True
    return False


def _ours(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:600]
    except OSError:
        return False
    return OWNER_LINE in head.splitlines()


def write(tree: Path, text: str, project: str, *, excluded: bool = False) -> Optional[Path]:
    """Put ``text`` in ``tree``'s briefing file; return the path when it holds it.

    An empty ``text`` removes a file mnemo wrote and nothing else. Nothing is
    written unless the path is excluded from commits first, nor into a tree
    whose rules are home rules, nor over a file someone else wrote there.
    ``excluded``: the caller already made sure of ``info/exclude`` for this
    tree's repository.
    """
    tree = Path(tree)
    if _unsafe(tree):
        return None
    path = rule_path(tree)
    if not text:
        if path.exists() and _ours(path):
            try:
                path.unlink()
            except OSError:
                pass
        return None
    if path.exists() and not _ours(path):
        return None
    if not excluded and not ensure_excluded(tree):
        return None
    body = file_text(text, project)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == body:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".mnemo-briefing.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(body)
            os.chmod(tmp, 0o644)
            os.replace(tmp, str(path))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return None
    except OSError:
        return None
    return path


def refresh(vault: Path, cfg: Mapping[str, Any], cwd: Path, project: str,
            text: Optional[str] = None) -> List[Path]:
    """Rewrite the file in every worktree of ``cwd``'s repository.

    ``text`` is what to write when the caller already rendered it; otherwise
    it is rendered here. Returns the files that now hold it.
    """
    if text is None:
        text = render(vault, cfg, project)
    written: List[Path] = []
    found = trees(Path(cwd))
    # Every tree shares one common git dir, so one exclude entry covers all.
    excluded = bool(found) and bool(text) and ensure_excluded(found[0])
    for tree in found:
        path = write(tree, text, project, excluded=excluded)
        if path is not None:
            written.append(path)
    return written


def loaded(session_dir: Path) -> Optional[bool]:
    """Whether the session in ``session_dir`` started with the briefing file.

    Read from ``prompt_context.json``, which Grok writes with the instruction
    files it loaded before it runs the SessionStart hooks (measured: present
    from inside the hook, and ``agents_md_files`` empty for an untrusted
    folder). ``None`` when it cannot be read.
    """
    try:
        data = json.loads((Path(session_dir) / PROMPT_CONTEXT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = data.get("agents_md_files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return None
    for entry in files:
        if not isinstance(entry, dict):
            continue
        parts = Path(str(entry.get("file_path") or "")).parts
        if tuple(parts[-3:]) == RULES_DIR + (FILE_NAME,):
            return True
    return False


def session_dir(session_id: str, cwd: str, env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The directory of Grok session ``session_id``: under the hook's home, else any home."""
    from mnemo.core.grok import paths

    found = paths.session_dir(cwd, session_id, paths.grok_home(env))
    if found is not None:
        return found
    for home in paths.grok_homes(env):
        found = paths.find_session_dir(session_id, home)
        if found is not None:
            return found
    return None
