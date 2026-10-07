"""Would routing skills per dispatched piece ever fire (#508)?

Usage:
    PYTHONPATH=src python3 tools/measure_skill_relevance.py [--last 50]
    PYTHONPATH=src python3 tools/measure_skill_relevance.py --send [--last 50] [--scores PATH]
    PYTHONPATH=src python3 tools/measure_skill_relevance.py [--scores PATH] [--sample 20] [--json]

**Without ``--send`` nothing leaves the machine.** With no scores file the run
counts the pieces and the skills, and estimates tokens and cost. ``--send``
posts each piece's task text and the skill descriptions to TypeSafe's
``/v1/systemone`` endpoint, a third party, with the key ``mnemo rerank
--setup`` stored (or ``TYPESAFE_API_KEY``), and writes the scores to
``--scores``. Every later run without ``--send`` reports from that file, so the
report can be re-read, sampled and hand-checked without sending again.

**The question.** A lean dispatch child (#270) starts without the maintainer's
user profile, and with it without every skill in ``~/.claude/skills``: it sees
Claude Code's built-in skills and the repo's own ``.claude/skills``, nothing
else. #270 dropped the profile on purpose (the ``superpowers`` skills pushed a
child into re-brainstorming a design its issue had already settled). The idea
under test is the middle ground — a contract piece names the skills it needs
and dispatch hands back only those. Before building it: would any piece need a
skill it does not get?

**What a piece is.** One dispatch child, found the way ``mnemo procedures``
finds them (:func:`mnemo.core.procedures.dispatch_transcripts`), minus the
probes that run under ``~/.claude/jobs/`` scratch. A worktree resumed or
respawned keeps one piece (its earliest transcript), and two children handed
the same prompt — twins — are one task. ``--last`` keeps the newest.

- **Task text:** the child's first prompt, the bytes ``claude --bg`` was handed
  — a contract piece's brief, or an issue prompt, which embeds the issue body.
  The first ``TASK_CHARS`` of it.
- **Received:** the child's own ``skill_listing`` attachment, the list Claude
  Code showed that child. Read off the transcript, never assumed from the
  profile flags: a child started with the full profile lists the maintainer's
  skills and those count as received for it.
- **Missing:** every ``<skills-dir>/*/SKILL.md`` the child's listing does not
  name.

**The judge.** Jev, the model and client ``recall.rerank`` ships
(:mod:`mnemo.core.mcp.rerank`), one ``noul`` question per skill, at most
``QUESTIONS_PER_REQUEST`` to a request, the missing skills in the first. Its answer is the probability that the statement is true.

**Pre-registered in #508, before any score existed:**

- a skill is relevant to a piece at ``noul >= CUT`` (0.5, the judge's own
  boundary between true and false — the 0.69 the rule list uses was fitted on
  labelled rules and says nothing about skills);
- **fewer than 20% of pieces** with at least one relevant *missing* skill →
  drop skill routing; **20% or more** → an A/B with vs without the routed
  skill, because relevant is not the same as helps.

The share at 0.4 and 0.69 is printed next to it as sensitivity, never as the
decision, and so is ``--exclude``: the share without skills a hand check
rejected, printed on its own line and labelled post-hoc. The report also
splits by repo and by prompt kind — an issue prompt and a contract piece wrap
the task in different boilerplate, and a judge reading the whole prompt reads
that boilerplate too. ``--sample`` prints seeded missing-skill pairs, half over the cut and
half under, for the hand check #508 asks for: a count from this tool is a
judge's reading until a person has read those.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from mnemo.core.dedup_judge import USD_PER_MTOK
from mnemo.core.filters import parse_frontmatter
from mnemo.core.mcp import rerank
from mnemo.core.procedures import _blocks, _records, dispatch_transcripts, fold_repo
from mnemo.core.reflex.replay import wilson_interval

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: Pre-registered in #508. Not a flag: a cut chosen after reading the scores
#: would decide the question it is supposed to test.
CUT = 0.5
SENSITIVITY = (0.4, 0.69)
DECISION_SHARE = 0.20

TASK_CHARS = 6000
SKILL_CHARS = 800

#: A child started with a plugin-heavy repo profile lists ~60 skills; the
#: recall stage never sends more than 64 questions at once, and neither does
#: this. Batches are the same state asked again, so the answers do not change.
QUESTIONS_PER_REQUEST = 32

RECEIVED = "received"
MISSING = "missing"

#: ``client(state, questions) -> response``; raises on any failure.
Client = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def question(name: str, description: str) -> Dict[str, Any]:
    return {
        "type": "noul",
        "instructions": (
            "An agent about to do the task in the state should load this skill first, "
            "because the skill's procedure applies to how this task gets done. "
            "Skill " + name + ": " + description
        ),
        "criteria": {
            "true": "The skill's procedure applies to this task and would change how the agent does it",
            "false": "The skill is about something else, or too general to change anything in this task",
        },
    }


# --------------------------------------------------------------------------
# reading a child
# --------------------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def parse_listing(attachment: Mapping[str, Any]) -> Dict[str, str]:
    """``{name: description}`` from a ``skill_listing`` attachment.

    ``names`` is the list; ``content`` holds ``- name: description`` entries
    whose descriptions may run over several lines, so each one is cut at the
    start of the next listed name rather than at a newline. A name missing
    from ``content`` keeps an empty description.
    """
    names = [n for n in attachment.get("names") or [] if isinstance(n, str)]
    content = attachment.get("content") or ""
    if not isinstance(content, str):
        content = ""
    starts = sorted((at, name) for name in names
                    for at in [_entry_start(content, name)] if at != -1)
    found: Dict[str, str] = {}
    for i, (at, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(content)
        found[name] = content[at + len("- " + name + ": "):end].strip()
    return {name: found.get(name, "") for name in names}


def _entry_start(content: str, name: str) -> int:
    """Where ``- name: `` opens a line of *content*, or -1."""
    marker = "- " + name + ": "
    if content.startswith(marker):
        return 0
    at = content.find("\n" + marker)
    return at + 1 if at != -1 else -1


def _text_of(record: Mapping[str, Any]) -> Optional[str]:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    texts = [b.get("text") for b in _blocks(dict(record)) if b.get("type") == "text"]
    texts = [t for t in texts if isinstance(t, str)]
    return "\n".join(texts) if texts else None


def read_child(path: str) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    """The child's first prompt and the skills Claude Code listed for it.

    The prompt is the first user message that is not meta and not a local
    command's echo; a tool result is never one. The listing is the first
    ``skill_listing`` attachment. Either is ``None`` when the transcript has
    none.
    """
    task: Optional[str] = None
    listing: Optional[Dict[str, str]] = None
    for record in _records(path):
        kind = record.get("type")
        if listing is None and kind == "attachment":
            attachment = record.get("attachment") or {}
            if isinstance(attachment, dict) and attachment.get("type") == "skill_listing":
                listing = parse_listing(attachment)
        elif task is None and kind == "user" and not record.get("isMeta") \
                and not record.get("isSidechain"):
            text = _text_of(record)
            if text and text.strip() and not text.lstrip().startswith("<"):
                task = text
        if task is not None and listing is not None:
            break
    return task, listing


class Piece(NamedTuple):
    cwd: str
    transcript: str
    started: float
    task: str
    received: Dict[str, str]


def is_scratch(cwd: str) -> bool:
    """A probe under a job's scratch dir, not a child that did work."""
    return "/.claude/jobs/" in cwd.replace("\\", "/")


def pieces_from(children: Iterable[Tuple[str, str]], *, last: int) -> List[Piece]:
    """One piece per worktree and per task, newest first, at most *last*.

    A worktree's earliest transcript is the child's own start; a later one is
    a resume or respawn of the same piece. A child with no prompt or no
    listing cannot be measured and is left out.
    """
    earliest: Dict[str, Tuple[float, str]] = {}
    for path, cwd in children:
        if is_scratch(cwd):
            continue
        started = _first_timestamp(path)
        if started is None:
            try:
                started = os.path.getmtime(path)
            except OSError:
                continue
        if cwd not in earliest or started < earliest[cwd][0]:
            earliest[cwd] = (started, path)
    out: List[Piece] = []
    seen_tasks = set()
    for cwd, (started, path) in sorted(earliest.items(), key=lambda kv: -kv[1][0]):
        task, listing = read_child(path)
        if not task or listing is None:
            continue
        digest = hashlib.sha256(task.encode("utf-8")).hexdigest()
        if digest in seen_tasks:
            continue
        seen_tasks.add(digest)
        out.append(Piece(cwd=cwd, transcript=path, started=started, task=task, received=listing))
        if len(out) >= last:
            break
    return out


def _first_timestamp(path: str) -> Optional[float]:
    """When the transcript's first timestamped record was written, or ``None``."""
    for index, record in enumerate(_records(path)):
        if index > 50:
            return None
        stamp = record.get("timestamp")
        if isinstance(stamp, str):
            try:
                return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def maintainer_skills(directory: Path) -> Dict[str, str]:
    """``{name: description}`` for every ``<directory>/*/SKILL.md``.

    The name is the frontmatter's ``name`` or the directory's; a file with no
    description still counts, with an empty one.
    """
    found: Dict[str, str] = {}
    for skill in sorted(directory.glob("*/SKILL.md")):
        try:
            text = skill.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        front = parse_frontmatter(text)
        name = str(front.get("name") or skill.parent.name)
        found[name] = str(front.get("description") or "")
    return found


def candidates(piece: Piece, maintainer: Mapping[str, str]) -> List[Tuple[str, str, str]]:
    """``(name, description, arm)`` for every skill judged against *piece*,
    the missing ones first: they are what #508 decides on, so they ride in the
    first request."""
    out = [(name, desc, MISSING) for name, desc in maintainer.items()
           if name not in piece.received]
    out += [(name, desc, RECEIVED) for name, desc in piece.received.items()]
    return out


def batches(items: Sequence[Any], size: int) -> List[Sequence[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# --------------------------------------------------------------------------
# judging
# --------------------------------------------------------------------------

def judge_piece(piece: Piece, maintainer: Mapping[str, str], client: Client) -> Dict[str, Any]:
    """One row: the piece, each skill's arm and score. A score of ``None`` is
    "not judged" — a failed request or a skipped question — never "irrelevant"."""
    cands = candidates(piece, maintainer)
    row: Dict[str, Any] = {
        "cwd": piece.cwd, "transcript": piece.transcript,
        "task_chars": len(piece.task), "task_head": _clip(piece.task, 300),
        "skills": {name: {"arm": arm, "score": None} for name, _desc, arm in cands},
    }
    state = {"developer_task": piece.task[:TASK_CHARS]}
    for batch in batches(cands, QUESTIONS_PER_REQUEST):
        questions = {"s%d" % i: question(name, _clip(desc, SKILL_CHARS))
                     for i, (name, desc, _arm) in enumerate(batch)}
        try:
            out = client(state, questions)
        except Exception:  # noqa: BLE001 — one request failing must not lose the rest
            continue
        answers = (out.get("answers") or {}) if isinstance(out, dict) else {}
        for i, (name, _desc, _arm) in enumerate(batch):
            value = (answers.get("s%d" % i) or {}).get("noul")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row["skills"][name]["score"] = float(value)
    return row


def judge(pieces: Sequence[Piece], maintainer: Mapping[str, str], client: Client, *,
          workers: int = 4) -> List[Dict[str, Any]]:
    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(lambda p: judge_piece(p, maintainer, client), pieces))


def estimate(pieces: Sequence[Piece], maintainer: Mapping[str, str]) -> Dict[str, Any]:
    """Requests, questions, tokens and cost, from characters / 4 — the
    arithmetic ``mnemo dedup-rules --judge`` quotes."""
    chars = 0
    asked = 0
    requests = 0
    for piece in pieces:
        cands = candidates(piece, maintainer)
        sent = len(batches(cands, QUESTIONS_PER_REQUEST))
        asked += len(cands)
        requests += sent
        chars += sent * len(piece.task[:TASK_CHARS]) + sum(
            len(_clip(desc, SKILL_CHARS)) + 300 for _name, desc, _arm in cands)
    tokens = chars // 4
    return {"requests": requests, "questions": asked, "tokens": tokens,
            "usd": round(tokens * USD_PER_MTOK / 1e6, 4)}


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def _hits(row: Mapping[str, Any], arm: str, cut: float,
          exclude: Sequence[str] = ()) -> List[str]:
    return sorted(name for name, s in row["skills"].items()
                  if s["arm"] == arm and s["score"] is not None and s["score"] >= cut
                  and name not in exclude)


def _judged(row: Mapping[str, Any]) -> bool:
    return any(s["score"] is not None for s in row["skills"].values())


def share(rows: Sequence[Mapping[str, Any]], arm: str, cut: float,
          exclude: Sequence[str] = ()) -> Dict[str, Any]:
    judged = [r for r in rows if _judged(r)]
    k = sum(1 for r in judged if _hits(r, arm, cut, exclude))
    low, high = wilson_interval(k, len(judged))
    return {"pieces": k, "of": len(judged),
            "share": round(k / len(judged), 4) if judged else None,
            "ci": [round(low, 4), round(high, 4)]}


def per_skill(rows: Sequence[Mapping[str, Any]], arm: str, cut: float) -> List[Tuple[str, int, int]]:
    """``(skill, pieces it is relevant to, pieces it was judged against)``, most hits first."""
    hits: Dict[str, int] = {}
    asked: Dict[str, int] = {}
    for row in rows:
        for name, s in row["skills"].items():
            if s["arm"] != arm or s["score"] is None:
                continue
            asked[name] = asked.get(name, 0) + 1
            if s["score"] >= cut:
                hits[name] = hits.get(name, 0) + 1
    return sorted(((n, hits.get(n, 0), asked[n]) for n in asked), key=lambda t: (-t[1], t[0]))


def repo_of(row: Mapping[str, Any]) -> str:
    return fold_repo(os.path.basename(str(row["cwd"]).replace("\\", "/").rstrip("/")))


def per_repo(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The missing-skill share inside each repo: a sample that is mostly one
    repo's children answers mostly for that repo."""
    repos = sorted({repo_of(r) for r in rows})
    return {repo: share([r for r in rows if repo_of(r) == repo], MISSING, CUT) for repo in repos}


def prompt_kind(row: Mapping[str, Any]) -> str:
    """Which dispatch template the child was handed. The two carry different
    boilerplate around the task, and a judge reading the whole prompt reads
    that too — on the first run every hit came from one of them."""
    head = str(row.get("task_head") or "")
    if head.startswith("You are building one piece"):
        return "contract piece"
    if head.startswith(("Work on issue", "Investigate issue")):
        return "issue"
    return "other"


def per_kind(rows: Sequence[Mapping[str, Any]], exclude: Sequence[str] = ()) -> Dict[str, Dict[str, Any]]:
    kinds = sorted({prompt_kind(r) for r in rows})
    return {kind: share([r for r in rows if prompt_kind(r) == kind], MISSING, CUT, exclude)
            for kind in kinds}


def summarize(rows: Sequence[Mapping[str, Any]], exclude: Sequence[str] = ()) -> Dict[str, Any]:
    """The pre-registered numbers, and — only when *exclude* names skills a
    hand check rejected — the same share without them, kept apart: the
    decision is always read off the registered share, never the post-hoc one."""
    missing = share(rows, MISSING, CUT)
    decision = None
    if missing["share"] is not None:
        decision = "a/b" if missing["share"] >= DECISION_SHARE else "drop"
    return {
        "pieces": len(rows),
        "unjudged": sum(1 for r in rows if not _judged(r)),
        "cut": CUT,
        "missing": missing,
        "received": share(rows, RECEIVED, CUT),
        "sensitivity": {str(cut): share(rows, MISSING, cut) for cut in SENSITIVITY},
        "per_repo": per_repo(rows),
        "per_kind": per_kind(rows),
        "excluded": sorted(exclude),
        "missing_excluding": share(rows, MISSING, CUT, exclude) if exclude else None,
        "per_kind_excluding": per_kind(rows, exclude) if exclude else None,
        "missing_per_skill": per_skill(rows, MISSING, CUT),
        "received_per_skill": per_skill(rows, RECEIVED, CUT),
        "decision_share": DECISION_SHARE,
        "decision": decision,
    }


def sample(rows: Sequence[Mapping[str, Any]], n: int, *, seed: int = 508) -> List[Dict[str, Any]]:
    """Up to *n* missing-skill pairs for a person to read: half over the cut,
    half under, drawn with a fixed seed so two readers see the same pairs."""
    over: List[Dict[str, Any]] = []
    under: List[Dict[str, Any]] = []
    for row in rows:
        for name, s in sorted(row["skills"].items()):
            if s["arm"] != MISSING or s["score"] is None:
                continue
            pair = {"cwd": row["cwd"], "task_head": row["task_head"],
                    "skill": name, "score": s["score"]}
            (over if s["score"] >= CUT else under).append(pair)
    rng = random.Random(seed)
    half = n // 2
    take_over = rng.sample(over, min(len(over), half))
    take_under = rng.sample(under, min(len(under), n - len(take_over)))
    return sorted(take_over, key=lambda p: -p["score"]) + sorted(take_under, key=lambda p: -p["score"])


def _pct(block: Mapping[str, Any]) -> str:
    if block["share"] is None:
        return "n/a (nothing judged)"
    low, high = block["ci"]
    return "{}/{} = {:.0%} [{:.0%}, {:.0%}]".format(
        block["pieces"], block["of"], block["share"], low, high)


def format_report(summary: Mapping[str, Any], samples: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "{} pieces, {} unjudged; relevant at noul >= {}".format(
            summary["pieces"], summary["unjudged"], summary["cut"]),
        "pieces with >= 1 relevant MISSING skill:  " + _pct(summary["missing"]),
        "pieces with >= 1 relevant received skill: " + _pct(summary["received"]),
        "sensitivity (missing): " + ", ".join(
            "{} -> {}".format(cut, _pct(block)) for cut, block in summary["sensitivity"].items()),
    ]
    if summary["decision"] == "drop":
        lines.append("decision (pre-registered, #508): < {:.0%} -> drop skill routing".format(
            summary["decision_share"]))
    elif summary["decision"] == "a/b":
        lines.append("decision (pre-registered, #508): >= {:.0%} -> A/B with vs without "
                     "the routed skill".format(summary["decision_share"]))
    if summary["missing_excluding"] is not None:
        lines.append("post-hoc, not the decision — excluding {}: {}".format(
            ", ".join(summary["excluded"]), _pct(summary["missing_excluding"])))
    lines.append("")
    lines.append("missing, per repo:")
    for repo, block in summary["per_repo"].items():
        lines.append("  {:<16} {}".format(repo, _pct(block)))
    lines.append("missing, per prompt kind:")
    for kind, block in summary["per_kind"].items():
        extra = ""
        if summary["per_kind_excluding"] is not None:
            extra = "   excluding: " + _pct(summary["per_kind_excluding"][kind])
        lines.append("  {:<16} {}{}".format(kind, _pct(block), extra))
    for title, key in (("missing", "missing_per_skill"), ("received", "received_per_skill")):
        lines.append("")
        lines.append(title + " skills, relevant/judged:")
        for name, hit, asked in summary[key]:
            if hit or title == "missing":
                lines.append("  {:>4}/{:<4} {}".format(hit, asked, name))
    if samples:
        lines.append("")
        lines.append("hand check — {} missing-skill pairs (seed 508):".format(len(samples)))
        for pair in samples:
            lines.append("  [{:.2f}] {}  <-  {}".format(
                pair["score"], pair["skill"], os.path.basename(pair["cwd"].rstrip("/"))))
            lines.append("         " + pair["task_head"][:200])
    return "\n".join(lines)


# --------------------------------------------------------------------------

def default_scores_path() -> Path:
    from mnemo import cli

    return Path(cli._resolve_vault()) / ".mnemo" / "skill-relevance-scores.json"


def write_scores(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"model": rerank.DEFAULT_MODEL, "cut": CUT, "rows": list(rows)}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def read_scores(path: Path) -> List[Dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8")).get("rows") or [])


def main(argv: Optional[List[str]] = None, *, client: Optional[Client] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--skills-dir", default=os.path.expanduser("~/.claude/skills"))
    parser.add_argument("--last", type=int, default=50, help="newest pieces to judge (default 50)")
    parser.add_argument("--send", action="store_true",
                        help="post task texts and skill descriptions to TypeSafe (third party)")
    parser.add_argument("--scores", help="scores file (default <vault>/.mnemo/skill-relevance-scores.json)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sample", type=int, default=20, help="pairs to print for the hand check")
    parser.add_argument("--exclude", action="append", default=[], metavar="SKILL",
                        help="also print the share without SKILL (repeatable); "
                             "post-hoc, never the decision")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    scores_path = Path(args.scores) if args.scores else default_scores_path()

    if not args.send:
        if scores_path.is_file():
            rows = read_scores(scores_path)
        else:
            pieces = pieces_from(dispatch_transcripts(args.projects), last=args.last)
            maintainer = maintainer_skills(Path(args.skills_dir))
            cost = estimate(pieces, maintainer)
            print("dry run: {} pieces, {} maintainer skills, {} questions in {} requests, "
                  "~{} tokens (~${}). --send posts the task texts and skill descriptions "
                  "to {}".format(len(pieces), len(maintainer), cost["questions"],
                                 cost["requests"], cost["tokens"], cost["usd"], rerank.TYPESAFE_URL))
            return 0
    else:
        if client is None:
            chosen = {"provider": "typesafe", "keyEnv": rerank.DEFAULT_KEY_ENV}
            key, _source = rerank.resolve_key(chosen)
            if not key:
                print("error: --send needs a key: run `mnemo rerank --setup` or set "
                      + rerank.DEFAULT_KEY_ENV, file=sys.stderr)
                return 1
            client = rerank.typesafe_client(key, model=rerank.DEFAULT_MODEL, timeout=60.0)
        pieces = pieces_from(dispatch_transcripts(args.projects), last=args.last)
        maintainer = maintainer_skills(Path(args.skills_dir))
        rows = judge(pieces, maintainer, client, workers=args.workers)
        write_scores(scores_path, rows)
        print("scores written to " + str(scores_path), file=sys.stderr)

    summary = summarize(rows, args.exclude)
    samples = sample(rows, args.sample)
    prov = _provenance.provenance(__file__, argv,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    if args.json:
        print(json.dumps(_provenance.stamp({"summary": summary, "sample": samples}, prov),
                         indent=2, ensure_ascii=False))
    else:
        print(_provenance.line(prov))
        print(format_report(summary, samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
