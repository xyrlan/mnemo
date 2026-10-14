"""The offline gate for #661's pre-PR review checkpoint.

Runs the pre-PR reviewer — the subagent :mod:`mnemo.core.review` defines,
imported, not copied, and handed :data:`mnemo.core.review.PRE_PR_TASK` the way
a child hands it — over past child PRs whose own change later needed a bug
fix, and over controls that needed none. It then prints the bar #661 declared:

    pass  <=>  the reviewer catches the known bug in at least a third of the
               candidates that survive re-verification, AND produces at most
               1 false blocking finding per control PR on average.

A blocking finding is one in the reviewer's ``findings`` list (suggestions
and suggested issues are not blocking). A blocking finding on a control that
a reading confirms as a real bug is a catch, not a false one.

**No hindsight.** Each PR is reviewed in a repository built for it that holds
exactly two commits — the PR's base snapshot and its head snapshot, both
taken with ``git archive`` — and no remote, no other ref, no later object.
A clone would carry the fix (``git log --all`` shows it). The reviewer runs
with ``--restricted``, which confines its file tools to that directory, its
shell to ``git diff/log/show``, no MCP server (the vault's rules today were
partly learned from these very fixes), and auto-memory off. Every tool call it
made is audited for a path outside the snapshot, and the report says so.

**No hooks.** Every ``claude`` this spawns carries ``MNEMO_HOOKS_OFF=1``
(:func:`mnemo.core.hook_guard.disable_hooks`, the switch ``core/llm.py``'s
``_build_env`` uses since #329), so no mnemo hook fires and the real vault is
not written. Thinking is left on: the reviewer is the child's subagent and
runs as the child would.

**Matching is a reading, recorded.** A finding catches a known bug when it
names the same file and the same failure mechanism as the fix. The tool does
not decide that: it prints every finding next to the fix's file and
mechanism, and reads the calls from ``--labels`` (a JSON file the reader
writes), so each call can be re-read.

Usage::

    python3 tools/measure_review_gate.py --out DIR [--labels FILE] [--jobs 4]
        [--model M] [--seed 661] [--dry-run] [--json]

Runs are cached per PR under ``DIR/runs/``; a rerun only reviews what is
missing.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

from mnemo.core import hook_guard, review  # noqa: E402
from mnemo.core.sync.engine import _rmtree  # noqa: E402  (git's read-only objects on Windows)

#: The candidates of #661, re-verified on 2026-10-14 from each fix's issue or
#: PR: the child PR introduced the code the fix changed. ``prs`` are reviewed
#: one by one; the candidate is caught when any of them catches any listed bug.
CANDIDATES: Tuple[Dict[str, Any], ...] = (
    {"id": "199", "prs": [199], "fixes": "#329/#330",
     "file": "src/mnemo/hooks/session_end.py, src/mnemo/core/sessions/unblocks.py",
     "mechanism": "Every SessionEnd spawns `mnemo sessions --consume-unblocks` "
                  "fire-and-forget with no lock and no per-pass bound; N ending "
                  "sessions run N concurrent sweeps, each running `learn` (more "
                  "claude helpers, more SessionEnds): the 09-15 hook storm."},
    {"id": "205", "prs": [205], "fixes": "#211/#214",
     "file": "src/mnemo/core/dispatch.py",
     "mechanism": "spawn_child takes the last whitespace token of `claude --bg` "
                  "stdout as the id; --bg prints a five-line help block ending "
                  "in 'session', so every child's id is 'session'."},
    {"id": "219", "prs": [219], "fixes": "#224/#226, #253, #287/#289",
     "file": "src/mnemo/core/sessions/delivery.py, src/mnemo/cli/commands/deliver.py",
     "mechanism": "`gh pr create --fill` body has no closing keyword, so the PR "
                  "never closes its issue; the PR lookup uses --state all and "
                  "refuses on any PR, so a merged PR blocks re-delivery; BASE = "
                  "'master' hardcoded, so a `main` repo reads 0 commits ahead."},
    {"id": "241", "prs": [241], "fixes": "#305/#322",
     "file": "src/mnemo/core/landing.py",
     "mechanism": "_definition_re matches an assignment only at column zero and "
                  "signature_name cuts `Cls.field` to `field`, so a dataclass "
                  "field (always indented) is never found and land refuses a "
                  "correct tree."},
    {"id": "285", "prs": [285], "fixes": "#303/#325",
     "file": "src/mnemo/install/settings.py, hooks/hooks.json, changelog.d/271.fixed.md",
     "mechanism": "The PreToolUse matcher gains Read, but inject_hooks writes a "
                  "matcher once, so existing installs never get it; the changelog's "
                  "remedy (re-run `mnemo init`) overwrites mnemo.config.json."},
    {"id": "295-296", "prs": [295, 296], "fixes": "#359/#363",
     "file": "src/mnemo/core/mcp/access_log.py, src/mnemo/hooks/session_start.py",
     "mechanism": "The briefing-read row's session_id is the briefing's author "
                  "(from its frontmatter), not the session that read it, so "
                  "readers of one briefing cannot be told apart or counted."},
    {"id": "342", "prs": [342], "fixes": "#348/#364",
     "file": "src/mnemo/core/friction/backfill.py",
     "mechanism": "backfill.plan sweeps every session with a transcript on disk, "
                  "harness probe sessions under ~/.claude/jobs/<id>/tmp included, "
                  "and links their turns to live rules as contradictions."},
    {"id": "445", "prs": [445], "fixes": "#500/#501, #506/#507",
     "file": "src/mnemo/core/sessions/pr_follow.py",
     "mechanism": "A woken child that never stops stays in_childs_hands, is never "
                  "polled, and closes at window end with the events recorded at the "
                  "wake (a green merged PR reported ci-red); _spawn_watcher() "
                  "inherits the hook's cwd, pinning a child's worktree."},
    {"id": "450", "prs": [450], "fixes": "#453/#457",
     "file": "src/mnemo/core/twins.py, src/mnemo/cli/commands/twins.py",
     "mechanism": "A twin that commits nothing on purpose is shown as '(no "
                  "commits — this run delivered nothing)' and its reason, only in "
                  "its closing report, is never shown."},
    {"id": "648", "prs": [648], "fixes": "#655 (open)",
     "file": "src/mnemo/core/sync/engine.py",
     "mechanism": "join moves the branch with update-ref and leaves the index "
                  "empty, so right after join `git status` shows every tracked "
                  "file as a staged deletion plus untracked."},
)

#: Listed in #661 and dropped at re-verification, with the reason.
DROPPED: Tuple[Tuple[str, str], ...] = (
    ("227", "the faulty agent re-resolution #254 removed predates #227 "
            "(git blame: ad81006a, d543afde); #227 changed only the log name"),
    ("405", "reading the key from the environment only was #405's documented "
            "design; #406 is a follow-up feature (secrets file, `mnemo rerank`), "
            "not a fix of wrong code"),
)

#: Every PR number a candidate's fix, or a dropped candidate, involves: never a control.
EXCLUDED = frozenset({
    199, 329, 330, 314, 316, 205, 211, 214, 219, 224, 226, 253, 287, 289,
    227, 247, 254, 241, 305, 322, 285, 303, 325, 339, 295, 296, 359, 363,
    342, 348, 364, 405, 406, 407, 445, 500, 501, 506, 507, 450, 453, 457,
    648, 655,
})

SEED = 661
CONTROLS = 12
WINDOW_DAYS = 30
#: A control must have been watched this long for a follow-up fix.
MIN_DAYS = 14
#: A dispatched issue child's branch; contract pieces are out of scope (#661).
CHILD_BRANCH = re.compile(r"^fix/issue-\d+$")

#: The reviewer's shell, inside the snapshot.
ALLOWED_BASH = ("Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)")


# --- picking the controls ----------------------------------------------------


def _when(value: Any) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def references(pr: Mapping[str, Any]) -> List[int]:
    """The numbers a later PR would cite to follow *pr* up: itself and its issues."""
    out = [int(pr["number"])]
    for ref in pr.get("closingIssuesReferences") or []:
        if isinstance(ref, dict) and ref.get("number"):
            out.append(int(ref["number"]))
    found = re.search(r"issue-(\d+)$", str(pr.get("headRefName") or ""))
    if found:
        out.append(int(found.group(1)))
    return sorted(set(out))


def followed_up(pr: Mapping[str, Any], merged: Sequence[Mapping[str, Any]],
                *, window_days: int = WINDOW_DAYS) -> List[int]:
    """Later merged PRs, within the window, whose title or body cites *pr* or its issue."""
    start = _when(pr.get("mergedAt"))
    if start is None:
        return []
    end = start + _dt.timedelta(days=window_days)
    cites = [re.compile(rf"#{n}\b") for n in references(pr)]
    out = []
    for other in merged:
        when = _when(other.get("mergedAt"))
        if other.get("number") == pr.get("number") or when is None or not (start < when <= end):
            continue
        text = f"{other.get('title') or ''}\n{other.get('body') or ''}"
        if any(c.search(text) for c in cites):
            out.append(int(other["number"]))
    return out


def select_controls(merged: Sequence[Mapping[str, Any]], *, now: _dt.datetime,
                    seed: int = SEED, n: int = CONTROLS,
                    excluded: Iterable[int] = EXCLUDED) -> Tuple[List[int], Dict[str, Any]]:
    """*n* merged issue-child PRs nothing followed up within the window, drawn with *seed*.

    Returns the draw and how the pool was made, for the report.
    """
    excluded = set(excluded)
    pool, dropped = [], {"not_a_child": 0, "excluded": 0, "too_recent": 0, "followed_up": 0}
    for pr in merged:
        number = int(pr["number"])
        when = _when(pr.get("mergedAt"))
        if not CHILD_BRANCH.match(str(pr.get("headRefName") or "")):
            dropped["not_a_child"] += 1
        elif number in excluded:
            dropped["excluded"] += 1
        elif when is None or (now - when).days < MIN_DAYS:
            dropped["too_recent"] += 1
        elif followed_up(pr, merged):
            dropped["followed_up"] += 1
        else:
            pool.append(number)
    pool.sort()
    drawn = sorted(random.Random(seed).sample(pool, min(n, len(pool))))
    return drawn, {"pool": len(pool), "dropped": dropped, "seed": seed}


# --- the no-hindsight repository ---------------------------------------------


def _git(args: Sequence[str], *, cwd: Path, env: Optional[Mapping[str, str]] = None,
         check: bool = True, binary: bool = False) -> subprocess.CompletedProcess:
    kwargs: Dict[str, Any] = {"capture_output": True, "cwd": str(cwd)}
    if not binary:
        kwargs.update(text=True, encoding="utf-8", errors="replace")
    if env is not None:
        kwargs["env"] = dict(env)
    result = subprocess.run(["git", *args], **kwargs)
    if check and result.returncode != 0:
        err = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)}: {err.strip()}")
    return result


_FIXED = {"GIT_AUTHOR_NAME": "gate", "GIT_AUTHOR_EMAIL": "gate@example.invalid",
          "GIT_COMMITTER_NAME": "gate", "GIT_COMMITTER_EMAIL": "gate@example.invalid",
          "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
          "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}


def _extract(source: Path, sha: str, dest: Path) -> None:
    data = _git(["archive", "--format=tar", sha], cwd=source, binary=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError(f"unsafe path in archive: {member.name}")
        tar.extractall(dest)


def snapshot(source: Path, base: str, head: str, dest: Path) -> Tuple[str, str]:
    """A fresh repository at *dest* holding only *base* and *head*'s trees.

    Two commits, ``base`` then ``head`` on one branch, no remote and no other
    ref, so nothing later than *head* is reachable or even present. Returns
    the new ``(base, head)`` commit ids.
    """
    if dest.exists():
        _rmtree(dest)
    dest.mkdir(parents=True)
    env = {**os.environ, **_FIXED}
    _git(["init", "-q", "-b", "main"], cwd=dest)
    shas = []
    for label, sha in (("base", base), ("head", head)):
        for child in dest.iterdir():
            if child.name != ".git":
                _rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        _extract(source, sha, dest)
        _git(["add", "-A"], cwd=dest)
        _git(["commit", "-q", "--allow-empty", "--no-verify", "-m", label], cwd=dest, env=env)
        shas.append(_git(["rev-parse", "HEAD"], cwd=dest).stdout.strip())
    return shas[0], shas[1]


def pr_commits(source: Path, number: int, *, gh: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    """The PR's title and its base and head commits, the head fetched into *source*."""
    data = gh(number)
    _git(["fetch", "-q", "origin", f"pull/{number}/head"], cwd=source)
    head = str(data["headRefOid"])
    base = _git(["merge-base", str(data["baseRefOid"]), head], cwd=source).stdout.strip()
    return {"title": str(data.get("title") or ""), "base": base, "head": head}


def _gh_pr(number: int) -> Dict[str, Any]:
    out = subprocess.run(
        ["gh", "pr", "view", str(number), "--json", "title,baseRefOid,headRefOid"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(out.stdout)


def _gh_merged() -> List[Dict[str, Any]]:
    out = subprocess.run(
        ["gh", "pr", "list", "--state", "merged", "--limit", "1000", "--json",
         "number,title,body,headRefName,mergedAt,closingIssuesReferences"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(out.stdout)


# --- one review --------------------------------------------------------------


def reviewer_argv(agents_file: Path, model: Optional[str]) -> List[str]:
    """The reviewer as a ``claude --print`` session: the child's subagent, no hindsight."""
    argv = [shutil.which("claude") or "claude", "--print",
            "--agents", str(agents_file), "--agent", review.AGENT_NAME,
            "--restricted", "--strict-mcp-config", "--no-session-persistence",
            "--settings", json.dumps({"autoMemoryEnabled": False}),
            "--tools", "Read,Grep,Glob,Bash",
            "--allowedTools", *ALLOWED_BASH,
            "--output-format", "stream-json", "--verbose"]
    if model:
        argv += ["--model", model]
    return argv


def reviewer_env() -> Dict[str, str]:
    """The parent's environment with mnemo's hooks off (#329)."""
    return hook_guard.disable_hooks(os.environ.copy())


def _paths_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield from re.findall(r"(?:(?<=\s)|^|(?<=[\"'=]))((?:/|~/|[A-Za-z]:[\\/])[^\s\"'`]+)", value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _paths_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from _paths_in(v)


def is_escape(path: str, root: Path) -> bool:
    """Whether *path*, named in a tool call, lies outside the snapshot *root*.

    Shell punctuation glued to a path (``cd repo;``) is dropped first, and
    Claude Code's own ``tool-results`` files — where it parks a long command
    output for the model to read back — are not the repository's history.
    """
    path = path.rstrip(";,.:)]}&|")
    if re.search(r"[\\/]projects[\\/][^\\/]+[\\/][^\\/]+[\\/]tool-results[\\/]", path):
        return False
    root_s = os.path.normcase(str(root.resolve()))
    full = os.path.normcase(str(Path(os.path.expanduser(path)).resolve()))
    return not (full == root_s or full.startswith(root_s + os.sep))


def read_stream(text: str, root: Path) -> Dict[str, Any]:
    """The reviewer's answer, its tool calls, any path outside *root*, and its cost."""
    out: Dict[str, Any] = {"result": "", "tools": [], "escaped": [], "cost_usd": None,
                           "output_tokens": None}
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "result":
            out["result"] = str(rec.get("result") or "")
            out["cost_usd"] = rec.get("total_cost_usd")
            out["output_tokens"] = (rec.get("usage") or {}).get("output_tokens")
        if rec.get("type") != "assistant":
            continue
        for block in (rec.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            out["tools"].append(block.get("name"))
            for path in _paths_in(block.get("input")):
                if is_escape(path, root):
                    out["escaped"].append(path)
    return out


def review_pr(source: Path, number: int, work: Path, *, model: Optional[str],
              gh: Callable[..., Dict[str, Any]] = _gh_pr,
              run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Dict[str, Any]:
    """Snapshot PR *number* and run the pre-PR reviewer over it once."""
    info = pr_commits(source, number, gh=gh)
    repo = work / f"pr-{number}" / "repo"
    base, head = snapshot(source, info["base"], info["head"], repo)
    agents_file = work / f"pr-{number}" / "agents.json"
    agents_file.write_text(review.agents_json(), encoding="utf-8")
    task = review.PRE_PR_TASK.format(what=info["title"], base=base)
    proc = run(reviewer_argv(agents_file, model), input=task, capture_output=True,
               text=True, encoding="utf-8", errors="replace", cwd=str(repo),
               env=reviewer_env())
    stream = read_stream(proc.stdout or "", repo)
    answer = review.findings_json(stream["result"])
    return {
        "pr": number, "title": info["title"], "base": info["base"], "head": info["head"],
        "returncode": proc.returncode, "stderr": (proc.stderr or "")[-2000:],
        "result": stream["result"], "parsed": answer is not None,
        "findings": (answer or {}).get("findings") or [],
        "suggestions": (answer or {}).get("suggestions") or [],
        "suggested_issues": (answer or {}).get("suggested_issues") or [],
        "tools": stream["tools"], "escaped": stream["escaped"],
        "cost_usd": stream["cost_usd"], "output_tokens": stream["output_tokens"],
    }


# --- the verdict -------------------------------------------------------------


def verdict(candidates: Sequence[Mapping[str, Any]], controls: Sequence[Mapping[str, Any]],
            labels: Mapping[str, Any]) -> Dict[str, Any]:
    """#661's bar over labelled runs.

    *candidates* are ``{"id", "runs": [run, ...]}``; *controls* are runs.
    ``labels["candidates"][id] = {"caught": bool, "why": str}`` and
    ``labels["controls"][str(pr)] = {"<finding index>": "real"|"false", ...}``.
    An unlabelled candidate or control finding leaves the verdict pending.
    """
    cand_labels = labels.get("candidates") or {}
    ctrl_labels = labels.get("controls") or {}
    unlabelled = []
    caught = 0
    for cand in candidates:
        label = cand_labels.get(cand["id"])
        if not isinstance(label, dict) or "caught" not in label:
            unlabelled.append(f"candidate {cand['id']}")
            continue
        caught += bool(label["caught"])
    false_blocking = real_on_controls = 0
    for run in controls:
        calls = ctrl_labels.get(str(run["pr"])) or {}
        for i, _finding in enumerate(run.get("findings") or []):
            call = calls.get(str(i))
            if call == "real":
                real_on_controls += 1
            elif call == "false":
                false_blocking += 1
            else:
                unlabelled.append(f"control #{run['pr']} finding {i}")
    n_cand, n_ctrl = len(candidates), len(controls)
    catch_ok = n_cand > 0 and caught * 3 >= n_cand
    rate = false_blocking / n_ctrl if n_ctrl else None
    false_ok = rate is not None and rate <= 1.0
    if unlabelled:
        result = "pending"
    else:
        result = "pass" if catch_ok and false_ok else "fail"
    return {"verdict": result, "candidates": n_cand, "caught": caught,
            "needed": -(-n_cand // 3), "controls": n_ctrl,
            "false_blocking": false_blocking, "false_per_control": rate,
            "real_on_controls": real_on_controls, "unlabelled": unlabelled}


def report_lines(cands: Sequence[Mapping[str, Any]], controls: Sequence[Mapping[str, Any]],
                 result: Mapping[str, Any], labels: Mapping[str, Any],
                 selection: Mapping[str, Any]) -> List[str]:
    lines = ["# #661 pre-PR review gate", ""]
    lines.append(f"dropped at re-verification: "
                 + "; ".join(f"#{pid} ({why})" for pid, why in DROPPED))
    lines.append(f"controls: {len(controls)} drawn with seed {selection.get('seed')} "
                 f"from a pool of {selection.get('pool')} ({selection.get('dropped')})")
    lines.append("")
    for cand in cands:
        label = (labels.get("candidates") or {}).get(cand["id"]) or {}
        lines.append(f"## candidate {cand['id']} — fix {cand['fixes']} — "
                     f"caught: {label.get('caught', '?')}")
        lines.append(f"  fix file: {cand['file']}")
        lines.append(f"  fix mechanism: {cand['mechanism']}")
        if label.get("why"):
            lines.append(f"  call: {label['why']}")
        for run in cand["runs"]:
            lines += _run_lines(run)
        lines.append("")
    for run in controls:
        calls = (labels.get("controls") or {}).get(str(run["pr"])) or {}
        lines.append(f"## control #{run['pr']} — {run.get('title', '')}")
        lines += _run_lines(run, calls)
        lines.append("")
    cost = sum(r.get("cost_usd") or 0 for c in cands for r in c["runs"]) + \
        sum(r.get("cost_usd") or 0 for r in controls)
    lines.append(f"caught {result['caught']} of {result['candidates']} candidates "
                 f"(bar: {result['needed']}); false blocking findings "
                 f"{result['false_blocking']} over {result['controls']} controls "
                 f"(bar: at most 1 per control); real bugs found on controls "
                 f"{result['real_on_controls']}; cost ${cost:.2f}")
    if result["unlabelled"]:
        lines.append(f"unlabelled: {', '.join(result['unlabelled'])}")
    lines.append(f"VERDICT: {result['verdict']}")
    return lines


def _run_lines(run: Mapping[str, Any], calls: Optional[Mapping[str, str]] = None) -> List[str]:
    out = [f"  PR #{run['pr']}: {len(run.get('findings') or [])} blocking finding(s), "
           f"{len(run.get('suggestions') or [])} suggestion(s); parsed={run.get('parsed')} "
           f"rc={run.get('returncode')} escaped={run.get('escaped') or []}"]
    for i, f in enumerate(run.get("findings") or []):
        call = f" [{calls.get(str(i), '?')}]" if calls is not None else ""
        out.append(f"    {i}.{call} {f.get('category', '')} {f.get('anchor', '')}: "
                   f"{' '.join(str(f.get('finding', '')).split())}")
    return out


# --- main --------------------------------------------------------------------


def _load(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _account_alias() -> List[Tuple[str, str]]:
    """A Claude account directory's name (``.claude-<handle>``) printed as an alias.

    The reviewer's own scratch paths live under it, and its name carries the
    account's handle.
    """
    name = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "").name
    return [(name, ".claude-<account>")] if name.startswith(".claude-") else []


def _vault() -> Optional[Path]:
    try:
        from mnemo.core import config, paths

        return paths.vault_root(config.load_config())
    except Exception:  # noqa: BLE001 — the report goes ahead without aliases
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="work and cache directory")
    ap.add_argument("--labels", type=Path, help="the reader's calls, JSON")
    ap.add_argument("--repo", type=Path, default=Path.cwd(), help="this repo's checkout")
    ap.add_argument("--model", default=None, help="reviewer model (default: the machine's)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="pick the controls, review nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out = args.out
    (out / "runs").mkdir(parents=True, exist_ok=True)
    merged_path = out / "merged.json"
    merged = _load(merged_path)
    if merged is None:
        merged = _gh_merged()
        merged_path.write_text(json.dumps(merged), encoding="utf-8")
    sel_path = out / "selection.json"
    selection = _load(sel_path)
    if selection is None or selection.get("seed") != args.seed:
        drawn, how = select_controls(merged, now=_dt.datetime.now(_dt.timezone.utc),
                                     seed=args.seed)
        selection = {**how, "controls": drawn}
        sel_path.write_text(json.dumps(selection, indent=1), encoding="utf-8")
    numbers = sorted({n for c in CANDIDATES for n in c["prs"]} | set(selection["controls"]))
    if args.dry_run:
        print(json.dumps({"selection": selection, "reviews": numbers}, indent=1))
        return 0

    def one(number: int) -> Dict[str, Any]:
        cached = out / "runs" / f"{number}.json"
        found = _load(cached)
        if found and found.get("parsed"):
            # Re-read with today's rule, so a cached run is judged as a new one.
            repo = out / "work" / f"pr-{number}" / "repo"
            found["escaped"] = sorted({p for p in found.get("escaped") or []
                                       if is_escape(p, repo)})
            return found
        run = review_pr(args.repo, number, out / "work", model=args.model)
        cached.write_text(json.dumps(run, indent=1), encoding="utf-8")
        return run

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        runs = dict(zip(numbers, pool.map(one, numbers)))

    cands = [{**c, "runs": [runs[n] for n in c["prs"]]} for c in CANDIDATES]
    controls = [runs[n] for n in selection["controls"]]
    labels = (_load(args.labels) if args.labels else None) or {}
    result = verdict(cands, controls, labels)
    blind = ["the reviewer runs without the vault's rules: today's rules were partly "
             "learned from these fixes, so the gate measures it on CLAUDE.md and "
             "repo precedent only",
             f"controls need {MIN_DAYS} days watched, not {WINDOW_DAYS}: dispatch "
             "started on 2026-09-12, and a full window would leave its first days only",
             "a follow-up is a later merged PR citing the PR or its issue; a fix "
             "that cites neither is not seen"]
    vault = _vault()
    prov = _provenance.provenance(__file__, argv, vault=vault, blind_spots=blind)
    # The report goes into a public PR: home paths print as `~` and private
    # names as their alias, as the provenance line does.
    aliases = _provenance.load_aliases(vault) + _account_alias()
    if args.json:
        print(_provenance.redact(json.dumps(_provenance.stamp(
            {"result": result, "selection": selection,
             "candidates": [{k: v for k, v in c.items()} for c in cands],
             "controls": controls}, prov), indent=1), aliases))
    else:
        print(_provenance.redact(
            "\n".join(report_lines(cands, controls, result, labels, selection)), aliases))
        print(_provenance.redact(_provenance.line(prov), _account_alias()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
