"""Where does ``claude --bg`` start, and where is it refused as untrusted? (#826)

Usage:
    PYTHONPATH=src python3 tools/measure_bg_trust.py [--dir DIR] [--outside DIR]
        [--checkout DIR] [--model haiku] [--keep] [--json]

On 2.1.321, ``claude --bg`` refuses a workspace nobody accepted the trust
prompt for. This tool makes one ``--bg`` launch per arm, with dispatch's lean
flags and a one-word prompt. A refused launch starts nothing. A launch that
starts is stopped and removed at once: the arms that start cost a few seconds
of a cheap model each. One more arm asks the same with ``claude -p``.

Nothing is written to Claude Code's trust state. ``~/.claude.json`` is only
read, so that ``claude_cli.trusted_by`` can name the entry the
``bg-workspace-trust`` rule says should trust each arm. ``verdict`` lists every
arm where what happened is not what the rule predicts.

The arms. A scratch folder is made under --dir (default: the temp folder),
and a second one inside --checkout (default: the checkout this tool is in):

1. ``fresh_repo``: a fresh ``git init`` repository under --dir.
2. ``fresh_worktree``: a worktree of that repository, beside it. This is the
   tree ``mnemo dispatch`` makes when run from a checkout nobody trusted.
3. ``nested_repo``: a fresh repository inside the checkout's folder.
4. ``plain_dir``: a plain directory under --dir.
5. ``outside_dir``: a plain directory under --outside, only when that is
   given. Pick a folder with no trusted directory above it.
6. ``worktree_outside``: an empty detached worktree of --checkout, under
   --dir. It is outside the checkout's folder, like every dispatch tree.
7. ``worktree_inside``: the same, inside the checkout's folder.
8. ``print_fresh_repo``: ``claude -p`` in the fresh repository.

A started arm also records the ``cwd`` its ``state.json`` gives. Worktrees
are added with ``--detach --no-checkout``, so no branch is created in the
checkout, and they are removed afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

PROMPT = "Reply with exactly the word OK and nothing else. Do not use any tools."
BG_ARMS = ("fresh_repo", "fresh_worktree", "nested_repo", "plain_dir", "outside_dir",
           "worktree_outside", "worktree_inside")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


# --- the verdict ----------------------------------------------------------------

def verdict(report: Dict[str, Any]) -> List[str]:
    """Every arm of *report* that departs from ``bg-workspace-trust``; empty when it holds.

    For each ``--bg`` arm, ``trusted_by`` (what the rule predicts) must agree
    with whether it started. A refusal must be the trust refusal, and a start
    must record its own cwd. The ``-p`` arm must answer: ``-p`` is not checked.
    """
    from mnemo.core import claude_cli

    out = []
    for arm in BG_ARMS:
        seen = report.get(arm)
        if not seen:
            continue
        expected = bool(seen.get("trusted_by"))
        if seen.get("error"):
            out.append(f"{arm}: could not be set up: {seen['error'][:200]}")
        elif seen.get("short_id") and not expected:
            out.append(f"{arm}: started, but no trust entry covers it under the rule")
        elif seen.get("refused") is not None and expected:
            out.append(f"{arm}: refused, though {seen['trusted_by']} trusts it under "
                       f"the rule: {seen['refused'][:200]}")
        elif seen.get("refused") is not None and claude_cli.TRUST_REFUSAL not in seen["refused"]:
            out.append(f"{arm}: refused for another reason: {seen['refused'][:200]}")
        elif seen.get("refused") is not None and seen.get("jobs"):
            out.append(f"{arm}: refused, yet left job entries {seen['jobs']}")
        elif seen.get("short_id") and seen.get("recorded_cwd") is not None and not (
                claude_cli._same(seen["recorded_cwd"], seen["cwd"])):
            out.append(f"{arm}: state.json records cwd {seen['recorded_cwd']!r}, "
                       f"not {seen['cwd']!r}")
        elif not seen.get("short_id") and seen.get("refused") is None:
            out.append(f"{arm}: neither started nor refused")
    printed = report.get("print_fresh_repo")
    if printed and printed.get("refused") is not None:
        out.append(f"print_fresh_repo: `claude -p` refused: {printed['refused'][:200]}")
    return out


# --- the live part --------------------------------------------------------------

def _env() -> Dict[str, str]:
    """This process's environment, minus what would tie a probe to the session
    running the tool: the probes are not its children."""
    drop = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDECODE")
    return {k: v for k, v in os.environ.items() if k not in drop}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   text=True, env={**os.environ, **GIT_ENV})


def _fresh_repo(path: Path) -> Path:
    path.mkdir()
    (path / "README.md").write_text("probe\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        _git(path, *args)
    return path


def _projects() -> Dict[str, Any]:
    from mnemo.core import child_profile

    try:
        data = json.loads(child_profile.user_claude_json_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    return projects if isinstance(projects, dict) else {}


def _recorded_cwd(short: str) -> Optional[str]:
    from mnemo.core.sessions import liveness

    try:
        data = json.loads((liveness.claude_home() / "jobs" / short / "state.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("cwd") if isinstance(data, dict) else None


def _bg(cwd: Path, model: str, started: List[str]) -> Dict[str, Any]:
    """One lean ``--bg`` launch in *cwd*, as dispatch would make it."""
    from mnemo.core import child_profile, claude_cli

    args = ["claude", "--bg", "--model", model, *child_profile.lean_args(cwd), PROMPT]
    run = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=_env())
    if run.returncode != 0:
        return {"refused": run.stderr.strip() or run.stdout.strip(), "exit": run.returncode,
                "jobs": _jobs_for(str(cwd))}
    short = claude_cli.short_id_from(run.stdout)
    if not short:
        return {"refused": None, "exit": 0, "stdout": run.stdout.strip()[:200]}
    started.append(short)
    return {"short_id": short, "recorded_cwd": _recorded_cwd(short)}


def _print(cwd: Path, model: str) -> Dict[str, Any]:
    run = subprocess.run(["claude", "-p", PROMPT, "--model", model], cwd=str(cwd),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=_env(), timeout=300)
    if run.returncode != 0:
        return {"refused": run.stderr.strip() or run.stdout.strip(), "exit": run.returncode}
    return {"answered": run.stdout.strip()[:80]}


def _jobs_for(cwd: str) -> List[str]:
    """The job directories whose ``state.json`` records *cwd*. Matching on a
    fresh scratch path, so a concurrent session on the machine cannot match."""
    from mnemo.core import claude_cli
    from mnemo.core.sessions import liveness

    found = []
    for state in (liveness.claude_home() / "jobs").glob("*/state.json"):
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("cwd"), str) \
                and claude_cli._same(data["cwd"], cwd):
            found.append(state.parent.name)
    return found


def _remove_worktree(checkout: Path, tree: Path) -> Optional[str]:
    """Remove *tree* from *checkout*; what went wrong, or ``None``.

    On Windows a child that was just stopped can still hold the folder. git
    then drops its registration but not the folder, and every retry says
    "is not a working tree": the part that is git's is done, and the folder
    goes with the scratch folder.
    """
    said = ""
    for _ in range(5):
        run = subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                             cwd=str(checkout), capture_output=True, text=True)
        said = run.stderr.strip()
        if run.returncode == 0 or not tree.exists() or "is not a working tree" in said:
            return None
        time.sleep(2)
    return said or "git worktree remove failed"


def measure(base: Path, checkout: Path, outside: Optional[Path], model: str,
            keep: bool) -> Dict[str, Any]:
    from mnemo.core import claude_cli

    scratch = Path(tempfile.mkdtemp(prefix="mnemo-826-", dir=str(base)))
    inside = Path(tempfile.mkdtemp(prefix=".mnemo-826-", dir=str(checkout)))
    far = Path(tempfile.mkdtemp(prefix="mnemo-826-", dir=str(outside))) if outside else None
    projects = _projects()
    report: Dict[str, Any] = {
        "scratch": str(scratch), "inside": str(inside),
        "checkout": str(checkout), "checkout_trusted_by": claude_cli.trusted_by(checkout, projects),
    }
    started: List[str] = []
    trees: List[Tuple[Path, Path]] = []  # (the repository it belongs to, the tree)

    def arm(name: str, make) -> None:
        try:
            cwd = make()
        except (OSError, subprocess.CalledProcessError) as exc:
            report[name] = {"error": str(getattr(exc, "stderr", "") or exc)}
            return
        seen = {"cwd": str(cwd), "trusted_by": claude_cli.trusted_by(cwd, projects),
                "git_root": str(claude_cli.git_root(cwd) or ""),
                "trust_root": str(claude_cli.trust_root(cwd) or "")}
        seen.update(_bg(cwd, model, started))
        report[name] = seen

    def worktree_of(repo: Path, path: Path) -> Path:
        _git(repo, "worktree", "add", "--detach", "--no-checkout", str(path))
        trees.append((repo, path))
        return path

    def mkdir(path: Path) -> Path:
        path.mkdir()
        return path

    try:
        arm("fresh_repo", lambda: _fresh_repo(scratch / "repo"))
        arm("fresh_worktree", lambda: worktree_of(scratch / "repo", scratch / "repo-wt-1"))
        arm("nested_repo", lambda: _fresh_repo(inside / "repo"))
        arm("plain_dir", lambda: mkdir(scratch / "plain"))
        if far is not None:
            arm("outside_dir", lambda: mkdir(far / "plain"))
        arm("worktree_outside", lambda: worktree_of(checkout, scratch / "tree"))
        arm("worktree_inside", lambda: worktree_of(checkout, inside / "tree"))
        if (scratch / "repo").is_dir():
            report["print_fresh_repo"] = _print(scratch / "repo", model)
        return report
    finally:
        for short in started:
            subprocess.run(["claude", "stop", short], capture_output=True, env=_env())
            subprocess.run(["claude", "rm", short], capture_output=True, env=_env())
        if not keep:
            time.sleep(3)
            left = {}
            for owner, tree in trees:
                problem = _remove_worktree(owner, tree)
                if problem:
                    left[str(tree)] = problem
            if left:
                report["worktrees_left"] = left
            for folder in (scratch, inside, far):
                if folder is not None:
                    shutil.rmtree(folder, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path(tempfile.gettempdir()),
                        help="where the plain directory and the fresh repository go")
    parser.add_argument("--outside", type=Path, default=None,
                        help="a folder with no trusted directory above it")
    parser.add_argument("--checkout", type=Path,
                        default=Path(__file__).resolve().parent.parent,
                        help="a checkout Claude Code trusts (default: this one)")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--keep", action="store_true", help="keep the scratch folders")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = measure(args.dir.resolve(), args.checkout.resolve(),
                     args.outside.resolve() if args.outside else None,
                     args.model, args.keep)
    report["verdict"] = verdict(report)
    vault = _vault()
    # Reports get pasted into public PRs: the home directory prints as `~` and
    # a private name as its alias, in the CLI's sentences and the keys alike.
    aliases = _provenance.load_aliases(vault)

    def show(text: str) -> None:
        print(_provenance.redact(text, aliases))

    prov = _provenance.provenance(__file__, argv, vault=vault, blind_spots=[
        "one launch per arm, on this machine's trust entries only",
        "the home-directory and unresolvable-path refusals are not exercised",
        "the inside arms are made in --checkout itself; when that is a linked "
        "worktree, as by default in a dispatch tree, the main checkout's own "
        "folder is not written to or exercised",
    ])
    if args.json:
        show(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        show(f"checkout {report['checkout']}: trusted by {report['checkout_trusted_by']}")
        for key in (*BG_ARMS, "print_fresh_repo"):
            if key in report:
                seen = report[key]
                outcome = ("started " + seen["short_id"] if seen.get("short_id")
                           else f"refused: {seen['refused']}" if seen.get("refused")
                           else "answered" if "answered" in seen else seen)
                show(f"{key}: {outcome}  [rule: {seen.get('trusted_by')}]")
        show("PASS" if not report["verdict"] else "FAIL: " + "; ".join(report["verdict"]))
        show(_provenance.line(prov))
    return 0 if not report["verdict"] else 1


def _vault() -> Optional[Path]:
    """The vault, for its private-name aliases and the provenance; ``None`` without one."""
    try:
        from mnemo.core import config, paths

        return paths.vault_root(config.load_config())
    except Exception:  # noqa: BLE001 — the report goes ahead without aliases
        return None


if __name__ == "__main__":
    sys.exit(main())
