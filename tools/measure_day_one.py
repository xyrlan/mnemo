"""Day one: what an empty vault carries, with and without Claude Code history (#467).

Usage:
    PYTHONPATH=src python3 tools/measure_day_one.py --dry-run     # plan, calls and cost per arm; calls nothing
    PYTHONPATH=src python3 tools/measure_day_one.py --send        # run both arms (resumes), then label
    PYTHONPATH=src python3 tools/measure_day_one.py --send --arm b
    PYTHONPATH=src python3 tools/measure_day_one.py               # the report, from saved results
    PYTHONPATH=src python3 tools/measure_day_one.py --json

Every number that says mnemo works was measured on the maintainer's vault.
This starts two vaults from nothing and replays one repository's real
transcripts through them — :data:`DEFAULT_CORPUS`, the maintainer's clubinho
sessions, standing in for somebody else's history — with the judge off in
both, because a new user has no TypeSafe key.

**Arm (a), with history.** The first :data:`--install-after` sessions are the
history that exists at install. ``mnemo backfill --install-run`` runs over
them in-process, the real ``cmd_backfill`` with its real ``installCap``,
mutation gate and ledger; two seams are pointed elsewhere, both in the
discovery step: ``discover.projects_root`` at a directory that links only
the history transcripts, and ``backfill._current_project`` at the corpus's
own agent name (the tool runs from a scratch directory, not the repo, so
that its helpers' transcripts never land among the repo's). Then the
extraction the first SessionEnd would run (a never-extracted vault is never
debounced), then the SessionStart payload, then the next ``--prompts``
prompts through the hook's own decision (``reflex.replay.run``: gates,
per-session cap and dedupe) against that vault, held fixed. The vault does
not grow during those prompts; arm (b) is where it grows.

**Arm (b), without history.** Sessions in the order they started, one at a
time. Per session: the prompts through the reflex against the vault as it
stands, then the SessionEnd path — ``run_extraction`` when
``session_end._debounce_passes`` says so, then ``generate_session_briefing``
— then the indexes rebuilt and the SessionStart payload the next session
would get. Two things keep that the hook's path and not a tidier one:

- **Extraction before briefing.** SessionEnd spawns both detached, the
  extraction first; it scans the vault at once, while the briefing is still
  a model call away. So a session's own briefing is consolidated at the next
  extraction, not this one.
- **The transcript's clock.** The debounce reads ``last_run`` and briefing
  mtimes; a replay taking minutes would otherwise extract on every session.
  After each extraction ``last_run`` is set to the session's end, and each
  briefing's mtime to a minute after it (:data:`BRIEFING_LAG`), so ``minIntervalMinutes`` and
  ``minNewMemories`` see the time the sessions actually had.

**What "carries" means.** The SessionStart payload is built by the hook's own
block builders (topics, ``[last-briefing]``, the staged-backfill notice, the
learned block, the review offer) over the arm's vault. The hook's ``main``
is not run: it mirrors every ``~/.claude/projects/*/memory`` directory on the
machine into the vault and repairs ``settings.json`` hook matchers, neither
of which may touch a measurement. So Claude Code's own auto-memory — a third
day-one source a real SessionEnd *would* mirror — is not in either arm; the
report counts how many such files the corpus has. The first-run invitation
is not counted as carrying anything: it is an offer to run the backfill.

**On-point.** #411's 0/1/2 rubric, with ``measure_reflex_reach``'s rater
instruction, blind: the rater sees the prompt and the injected rule's text
and nothing else. Only injected (prompt, rule) pairs are labelled, since the
question is what reached the prompt. Headline: the first arm-(b) session
whose prompts got an on-point rule, or "> N" for the sessions run.

**Isolation.** Each arm scaffolds its own vault under ``--work`` and runs with
``MNEMO_CONFIG_PATH`` at that vault's config; ``HOME`` is untouched, because
``claude`` authenticates from it. Every model call goes through
``core.llm.call``, which already hands helpers ``MNEMO_HOOKS_OFF``; a hook that
fired anyway would still read this process's ``MNEMO_CONFIG_PATH``, i.e. the
arm's vault. The run snapshots ``~/mnemo`` before and after and reports what
changed there, since the maintainer's own sessions keep writing to it.

**Budget.** ``--dry-run`` prints the plan, with a bound on calls and an
API-price estimate per arm. ``--send`` refuses a plan over ``--budget``
(default :data:`BUDGET`) and meters every call: arm (b) stops before a
session whose worst case, plus the labels still owed, would cross it, and
says where it stopped. At the budget the meter raises
``LLMSubprocessError``, which backfill and extraction both treat as an
environmental stop. Cost is the CLI's API-price equivalent (#441), not money.

**First run, 2026-09-23**, clubinho, 88 sessions (08-22 to 09-23), 1,549
prompts, install after session 44, rater ``claude-sonnet-5``. The dry run
bounded it at 150 calls; it took 127 (126 metered, one briefing lost to a
crash the rerun fixed), 98 Haiku and 28 Sonnet, $10.46 API-price
equivalent.

- **Arm (a) carries nothing the reflex can use.** The install run took the
  20 newest history transcripts, 11 calls (one timed out twice; nine had no
  file mutations), and wrote 59 memory pages. The first extraction, 8
  calls, staged 56 of them and promoted none: backfill-origin pages always
  stage (``extract/inbox/paths.py``). Live pages 0, so the next 50 prompts
  and the next 35 first turns fire 0 times. The first SessionStart carries
  the staged notice, a review offer, and a ``[mnemo learned]`` block
  announcing 32 of the staged ``project`` pages as learned with a ``veto``
  line, though none is live: ``promote_projects`` reports staged backfill
  project pages in ``written_fresh`` and ``_record_learned`` records them.
- **Arm (b)**: first live page after session 2, first injection in session
  5, first on-point injection in **session 24** (09-09, 18 days in). Over
  all 88: the reflex fires on 252 of 1,549 prompts (16.3%), 328 pairs, 11
  on-point (3.4%), 41 marginal, 276 noise. Sessions 1-20: 19 fired, none
  on-point. 21-44: 8 on-point prompts of 81 fired. 45-88: 3 of 152. Live
  pages end at 31, staged 38. The five most-injected rules are generic
  (the top one, 37 times). Every SessionStart from session 2 on carries a
  ``[last-briefing]``; the topic line holds 2 topics throughout.
- ``~/mnemo`` changed in 52 files during the run. The 7 naming clubinho were
  the maintainer's own clubinho session ending at 13:47 and 14:12 (its log
  lines, and its briefing differing from this run's), not this run.

**Resume.** Everything lives under ``--work`` (default
:data:`DEFAULT_WORK`): each arm's vault, ``progress.json`` after every
session, ``labels.json`` after every rater call. A rerun continues; a session
interrupted halfway is replayed whole (briefings skip unchanged
transcripts, extraction skips clean files).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrr = _sibling("measure_reflex_reach")
mrg = mrr.mrg

#: Relative to ``$HOME``, resolved when the tool runs.
DEFAULT_CORPUS = Path(".claude") / "projects" / "-Users-xyrlan-github-clubinho"
DEFAULT_WORK = Path(".cache") / "mnemo" / "day-one"
REAL_VAULT = Path("mnemo")

#: Model calls both arms may spend together (#467).
BUDGET = 200
#: Arm (a)'s replay length.
PROMPTS = 50
DEFAULT_RATER = mrr.DEFAULT_RATER
ON_POINT = mrr.ON_POINT

#: Dry-run assumptions, printed beside the numbers they produce.
PAGES_PER_HARVEST = 3
TOKENS_PER_BRIEFING = 1500
TOKENS_PER_PAGE = 400
OUT_TOKENS = {"harvest": 1500, "briefing": 1200, "extract": 2000, "gate": 300, "label": 250}
#: USD per MTok (in, out) for the estimate; unknown models price as Sonnet.
PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0)}

LIVE_TYPES = ("feedback", "user", "reference", "project")

#: Seconds between a session's end and its briefing landing: the briefing is a
#: model call spawned beside the extraction, so it is written after the
#: extraction's ``last_run`` watermark, and the next debounce counts it.
BRIEFING_LAG = 60


# --- the corpus -----------------------------------------------------------------

@dataclass
class Session:
    sid: str
    path: Path
    start: float
    end: float
    mutations: int
    prompts: List[Tuple[float, str]] = field(default_factory=list)
    mtime: float = 0.0


def read_session(path: Path) -> Optional[Session]:
    """One transcript as the replay needs it, or None when it holds no event time."""
    from mnemo.core.briefing import _count_file_mutations, _load_jsonl_events
    from mnemo.core.reflex.replay import _parse_ts, read_prompts

    events = _load_jsonl_events(path)
    stamps = [t for t in (_parse_ts(e.get("timestamp")) for e in events
                          if isinstance(e, dict)) if t is not None]
    if not stamps:
        return None
    prompts = [(p.ts, p.text) for p in read_prompts(path, path.stem, lambda _c, _s: "")]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = max(stamps)
    return Session(sid=path.stem, path=path, start=min(stamps), end=max(stamps),
                   mutations=_count_file_mutations(events), prompts=prompts, mtime=mtime)


def load_sessions(corpus: Path) -> List[Session]:
    """Every transcript of the corpus directory, in the order the sessions started."""
    out = [s for s in (read_session(p) for p in sorted(corpus.glob("*.jsonl"))) if s]
    out.sort(key=lambda s: (s.start, s.sid))
    return out


def corpus_agent(corpus: Path) -> str:
    """The agent name the corpus's own recorded cwd resolves to — what the hooks file it under."""
    from mnemo.core.backfill import discover

    cwd = discover.recorded_cwd(sorted(corpus.glob("*.jsonl")))
    if not cwd:
        raise SystemExit("error: no transcript in %s records a cwd" % corpus)
    return discover.agent_for_cwd(cwd)


# --- the pure part ----------------------------------------------------------------

def install_selection(history: Sequence[Session], cap: int, min_mutations: int) -> List[Session]:
    """What ``--install-run`` harvests: the newest ``cap`` by mtime, then the mutation gate.

    Mirrors ``discover.find_transcripts`` (newest-first by mtime, cap as a
    prefix) and ``harvest_session``'s gate, for the dry run's count only; the
    run itself calls the real code.
    """
    newest = sorted(history, key=lambda s: s.mtime, reverse=True)[:max(0, cap)]
    return [s for s in newest if s.mutations >= min_mutations]


def first_prompts(future: Sequence[Session], n: int) -> List[Tuple[Session, float, str]]:
    """The next ``n`` prompts after the install point, in the order they were typed."""
    out: List[Tuple[Session, float, str]] = []
    for s in future:
        for ts, text in s.prompts:
            if len(out) >= n:
                return out
            out.append((s, ts, text))
    return out


def first_turns(future: Sequence[Session], n: int) -> List[Tuple[Session, float, str]]:
    """The first prompt of each of the next ``n`` sessions that has one."""
    return [(s, s.prompts[0][0], s.prompts[0][1]) for s in future if s.prompts][:n]


def plan_b(sessions: Sequence[Session], *, min_interval_min: int, min_new: int,
           min_mutations: int, chunk: int) -> List[Dict[str, Any]]:
    """The SessionEnd schedule arm (b) will follow, and each session's worst-case calls.

    Per session, in the hook's order: extraction if the debounce passes on
    the transcript's clock — never before a first extraction, afterwards
    ``min_interval_min`` since the last and ``min_new`` briefings written
    since — consuming the briefings written so far; then a briefing if the
    session mutated files. Worst case per extraction: one call per chunk of
    ``chunk`` briefings plus one reference-gate call per chunk.
    """
    rows: List[Dict[str, Any]] = []
    last_run: Optional[float] = None
    pending = 0
    for s in sessions:
        extract = last_run is None or (
            s.end - last_run >= min_interval_min * 60 and pending >= min_new)
        consumed = pending if extract else 0
        chunks = int(math.ceil(consumed / float(chunk))) if consumed else 0
        briefing = s.mutations >= min_mutations
        rows.append({"sid": s.sid, "extract": extract, "consumed": consumed,
                     "extract_calls": 2 * chunks, "briefing": briefing,
                     "worst": 2 * chunks + (1 if briefing else 0),
                     "prompts": len(s.prompts)})
        if extract:
            last_run, pending = s.end, 0
        if briefing:
            pending += 1
    return rows


def label_calls(pairs: int, batch: int = mrr.BATCH) -> int:
    return int(math.ceil(pairs / float(batch))) if pairs else 0


def max_pairs(prompts: int, cap: int) -> int:
    """Upper bound on injected pairs in one session: two per prompt, ``cap`` per session."""
    return min(cap, 2 * prompts)


def price(model: str, tokens_in: int, tokens_out: int) -> float:
    rates = PRICES["haiku"] if "haiku" in model else PRICES["sonnet"]
    return (tokens_in * rates[0] + tokens_out * rates[1]) / 1e6


def first_on_point(rows: Sequence[Dict[str, Any]]) -> Optional[int]:
    """1-based session number of the first row with an on-point injection."""
    for n, row in enumerate(rows, 1):
        if row.get("on_point", 0) > 0:
            return n
    return None


def rates(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """Emit and on-point counts over replayed prompts.

    ``units`` are prompts, each with the ``pool`` of rules injected into it
    (empty when the reflex stayed silent). emit = prompts with an injection;
    on-point = injected pairs the rater called 2; ``unlabelled`` pairs are
    reported, never guessed.
    """
    fired = [u for u in units if u["pool"]]
    pairs = [(u["uid"], c["slug"]) for u in fired for c in u["pool"]]
    lab = [labels.get(uid, {}).get(slug) for uid, slug in pairs]
    on = sum(1 for v in lab if v == ON_POINT)
    return {"prompts": len(units), "fired": len(fired), "pairs": len(pairs),
            "on_point": on, "labelled": sum(1 for v in lab if v is not None),
            "prompts_on_point": sum(
                1 for u in fired
                if any(labels.get(u["uid"], {}).get(c["slug"]) == ON_POINT for c in u["pool"]))}


def carries(blocks: Dict[str, Any]) -> bool:
    """Whether a SessionStart payload carries anything learned (the invitation does not count)."""
    return bool(blocks.get("local_topics") or blocks.get("universal_topics")
                or blocks.get("last_briefing") or blocks.get("learned")
                or blocks.get("staged_notice") or blocks.get("offer"))


def pct(k: int, n: int) -> str:
    return "%.1f%%" % (100.0 * k / n) if n else "n/a"


def fingerprint(root: Path) -> Dict[str, Tuple[float, int]]:
    """relpath -> (mtime, size) for every file under ``root`` (empty when absent)."""
    out: Dict[str, Tuple[float, int]] = {}
    if not root.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(str(root)):
        for name in files:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = (st.st_mtime, st.st_size)
    return out


def changed(before: Dict[str, Tuple[float, int]], after: Dict[str, Tuple[float, int]]) -> List[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def suspects(paths: Iterable[str], markers: Sequence[str]) -> List[str]:
    """Changed real-vault paths that name the corpus or this run — a hook that leaked."""
    return [p for p in paths if any(m and m in p for m in markers)]


# --- the vault side -----------------------------------------------------------------

class Meter:
    """Counts and bounds every model call, by wrapping ``core.llm.call``."""

    def __init__(self, budget: int, spent: int = 0, usd: float = 0.0):
        self.budget, self.calls, self.usd = budget, spent, usd
        self.by_model: Dict[str, int] = {}

    @contextlib.contextmanager
    def installed(self):
        from mnemo.core import llm

        real = llm.call

        def metered(prompt: str, **kw: Any):
            if self.calls >= self.budget:
                raise llm.LLMSubprocessError("day-one budget of %d model calls reached" % self.budget)
            self.calls += 1
            model = str(kw.get("model") or "")
            self.by_model[model] = self.by_model.get(model, 0) + 1
            resp = real(prompt, **kw)
            self.usd += float(resp.total_cost_usd or 0.0)
            return resp

        llm.call = metered
        try:
            yield self
        finally:
            llm.call = real


@contextlib.contextmanager
def arm_vault(vault: Path):
    """Scaffold ``vault`` (idempotent) and point every config read at it."""
    from mnemo.install import scaffold

    scaffold.scaffold_vault(vault)
    cfg_path = vault / "mnemo.config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["vaultRoot"] = str(vault)
    # The judge is off by default already; pinned so a default change cannot
    # quietly turn a new user's key-less day one into a keyed one.
    raw.setdefault("reflex", {}).setdefault("judge", {})["provider"] = "none"
    raw.setdefault("recall", {}).setdefault("rerank", {})["provider"] = "none"
    cfg_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    old = os.environ.get("MNEMO_CONFIG_PATH")
    os.environ["MNEMO_CONFIG_PATH"] = str(cfg_path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("MNEMO_CONFIG_PATH", None)
        else:
            os.environ["MNEMO_CONFIG_PATH"] = old


def vault_counts(vault: Path) -> Dict[str, int]:
    """Live rule pages (``shared/<type>/``), staged pages, and memory files."""
    from mnemo.core import filters

    shared = vault / "shared"
    live = staged = 0
    for md in filters.iter_shared_pages(vault, include_inbox=True):
        rel = md.relative_to(shared).parts
        if rel[0] == "_inbox":
            staged += 1
        elif rel[0] in LIVE_TYPES:
            live += 1
    memory = sum(1 for p in vault.glob("bots/*/memory/*.md") if p.name != "MEMORY.md")
    briefings = sum(1 for _ in vault.glob("bots/*/briefings/sessions/*.md"))
    return {"live": live, "staged": staged, "memory": memory, "briefings": briefings}


def error_counts(vault: Path) -> Dict[str, int]:
    """``where:kind`` -> count from the arm vault's error log.

    The meter counts ``llm.call``s; a call that timed out twice was two CLI
    invocations and no answer, and this is where that shows.
    """
    from mnemo.core import errors

    out: Dict[str, int] = {}
    try:
        lines = (vault / errors.ERROR_LOG_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = "%s:%s" % (row.get("where"), row.get("kind"))
        out[key] = out.get(key, 0) + 1
    return out


def rebuild_indexes(vault: Path) -> None:
    """What SessionStart rebuilds before anything reads them."""
    from mnemo.core import rule_activation
    from mnemo.core.reflex import index as reflex_index

    rule_activation.write_index(vault, rule_activation.build_index(vault))
    reflex_index.write_index(vault, reflex_index.build_index(vault))


def session_start_blocks(vault: Path, project: str, cwd: str) -> Dict[str, Any]:
    """The hook's own blocks for ``project``, summarised; the envelope text is kept too."""
    from mnemo.core import config
    from mnemo.hooks import session_start as ss

    cfg = config.load_config()
    rebuild_indexes(vault)
    out: Dict[str, Any] = {}
    envelope = ss._build_injection_payload(vault, current_project=project, inject_briefing=True,
                                           session_id=None, source="startup")
    for line in envelope.splitlines():
        for key in ("local", "universal"):
            if line.startswith(key + ": ["):
                out[key + "_topics"] = [t for t in line[len(key) + 3:-1].split(", ") if t]
    out["last_briefing"] = "[last-briefing" in envelope
    out["staged_notice"] = ss._staged_backfill_notice(vault)
    out["learned"] = ss._learned_block(vault, cfg, project)
    out["offer"] = ss._offer_block(vault, cfg, project, cwd, None)
    out["text"] = "\n\n".join(x for x in (envelope, out["staged_notice"], out["learned"],
                                          out["offer"]) if x)
    out["carries"] = carries(out)
    return out


def replay_prompts(vault: Path, project: str, items: Sequence[Tuple[Session, float, str]],
                   texts: Dict[str, str]) -> List[Dict[str, Any]]:
    """The prompts through the hook's decision, in order; one unit per prompt."""
    from mnemo.core import config
    from mnemo.core.mcp import rerank, tools as mcp_tools
    from mnemo.core.reflex import replay
    from mnemo.core.reflex.index import load_index

    cfg = config.load_config()
    index = load_index(vault) or {"docs": {}, "postings": {}, "doc_count": 0}
    prompts = [replay.Prompt(session_id=s.sid, project=project, ts=ts, text=text)
               for s, ts, text in items]
    result = replay.run(prompts, index, {}, reflex_cfg=cfg.get("reflex") or {})
    injected: Dict[Tuple[str, float], List[str]] = {}
    for inj in result.injections:
        injected.setdefault((inj.session_id, inj.ts), []).append(inj.slug)
    units = []
    for p in prompts:
        pool = []
        for slug in injected.get((p.session_id, p.ts), []):
            if slug not in texts:
                page = mcp_tools.read_mnemo_rule(vault, slug, scope="vault") or {}
                texts[slug] = rerank.rule_text(page.get("body") or "")
            pool.append({"slug": slug, "text": texts[slug]})
        units.append({"uid": mrg.unit_id(p.session_id, p.ts, p.text), "session_id": p.session_id,
                      "ts": p.ts, "prompt": mrg.judge_state(p.text), "pool": pool})
    return units


def set_last_run(vault: Path, when: float) -> None:
    """Move extraction's ``last_run`` watermark onto the transcript's clock."""
    path = vault / ".mnemo" / "extraction-state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    state["last_run"] = datetime.fromtimestamp(when).isoformat(timespec="seconds")
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def extraction_row(summary: Any) -> Dict[str, Any]:
    return {k: getattr(summary, k, None) for k in (
        "llm_calls", "pages_written", "auto_promoted", "demoted_unverified",
        "reference_held", "failed_chunks", "total_cost_usd")}


# --- the arms -----------------------------------------------------------------------

def run_arm_a(ctx: Dict[str, Any], progress: Dict[str, Any], save: Callable[[], None]) -> None:
    from mnemo.cli.commands import backfill as backfill_cmd
    from mnemo.core import config, extract
    from mnemo.core.backfill import discover

    a = progress.setdefault("a", {})
    vault = ctx["work"] / "arm-a" / "vault"
    history, future = ctx["history"], ctx["future"]
    with arm_vault(vault):
        if not a.get("backfill"):
            stage = ctx["work"] / "arm-a" / "projects" / ctx["corpus"].name
            stage.mkdir(parents=True, exist_ok=True)
            for s in history:
                link = stage / s.path.name
                if not link.exists():
                    link.symlink_to(s.path)
            real_root, real_proj = discover.projects_root, backfill_cmd._current_project
            discover.projects_root = lambda: stage.parent
            backfill_cmd._current_project = lambda: ctx["agent"]
            before = ctx["meter"].calls
            try:
                args = argparse.Namespace(install_run=True, dry_run=False, yes=True,
                                          retry_failed=False, project=None, all=False, limit=None)
                code = backfill_cmd.cmd_backfill(args)
            finally:
                discover.projects_root, backfill_cmd._current_project = real_root, real_proj
            a["backfill"] = {"exit": code, "calls": ctx["meter"].calls - before,
                             "counts": vault_counts(vault),
                             "carry": session_start_blocks(vault, ctx["agent"], ctx["cwd"])}
            save()
        if not a.get("extract"):
            before = ctx["meter"].calls
            summary = extract.run_extraction(config.load_config(), background=True)
            a["extract"] = {"summary": extraction_row(summary),
                            "calls": ctx["meter"].calls - before,
                            "counts": vault_counts(vault),
                            "carry": session_start_blocks(vault, ctx["agent"], ctx["cwd"])}
            save()
        if not a.get("units"):
            texts: Dict[str, str] = {}
            a["units"] = replay_prompts(vault, ctx["agent"], first_prompts(future, ctx["prompts"]), texts)
            a["first_turns"] = replay_prompts(vault, ctx["agent"], first_turns(future, ctx["prompts"]), texts)
            save()


def run_arm_b(ctx: Dict[str, Any], progress: Dict[str, Any], save: Callable[[], None]) -> None:
    from mnemo.core import briefing, config, extract
    from mnemo.core import errors as err_mod
    from mnemo.hooks import session_end

    b = progress.setdefault("b", {"rows": []})
    vault = ctx["work"] / "arm-b" / "vault"
    sessions, plan = ctx["sessions"], ctx["plan_b"]
    cap = ctx["cap_emissions"]
    with arm_vault(vault):
        if "carry0" not in b:
            b["carry0"] = session_start_blocks(vault, ctx["agent"], ctx["cwd"])
            save()
        texts: Dict[str, str] = {}
        while len(b["rows"]) < min(len(sessions), ctx["max_sessions"]):
            k = len(b["rows"])
            s, step = sessions[k], plan[k]
            owed = sum(len(u["pool"]) for r in b["rows"] for u in r["units"])
            reserve = label_calls(owed + max_pairs(len(s.prompts), cap)) + ctx["label_reserve_a"]
            if ctx["meter"].calls + step["worst"] + reserve > ctx["meter"].budget:
                b["stopped"] = "budget: session %d would need up to %d call(s) with %d spent and %d owed to labels" % (
                    k + 1, step["worst"], ctx["meter"].calls, reserve)
                save()
                break
            before = ctx["meter"].calls
            units = replay_prompts(vault, ctx["agent"], [(s, ts, t) for ts, t in s.prompts], texts)
            cfg = config.load_config()
            state_path = vault / ".mnemo" / "extraction-state.json"
            ran: Optional[Dict[str, Any]] = None
            if session_end._debounce_passes(state_path, vault, cfg, now=datetime.fromtimestamp(s.end)):
                ran = extraction_row(extract.run_extraction(cfg, background=True))
                set_last_run(vault, s.end)
            # reuse_unchanged only matters to a resumed session: a first run
            # has no briefing on disk, so it is the hook's call exactly.
            # ``mnemo briefing``'s own handling: a failed briefing is logged and
            # the session simply has none — the hook never retries it.
            try:
                out = briefing.generate_session_briefing(s.path, ctx["agent"], cfg,
                                                         reuse_unchanged=True)
            except Exception as exc:
                err_mod.log_error(vault, "briefing.cli", exc)
                out = None
            if out is not None and Path(out).exists():
                os.utime(str(out), (s.end + BRIEFING_LAG, s.end + BRIEFING_LAG))
            b["rows"].append({
                "sid": s.sid, "start": s.start, "end": s.end, "extraction": ran,
                "briefed": out is not None, "calls": ctx["meter"].calls - before,
                "counts": vault_counts(vault),
                "carry_next": {k2: v for k2, v in session_start_blocks(
                    vault, ctx["agent"], ctx["cwd"]).items() if k2 != "text"},
                "units": units})
            save()
            print("arm b: session %d/%d %s — %d prompt(s), %d call(s) so far"
                  % (k + 1, len(sessions), s.sid[:8], len(s.prompts), ctx["meter"].calls),
                  file=sys.stderr)


def label(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]], rater: str,
          save: Callable[[], None]) -> int:
    from mnemo.core import config, llm

    cfg = config.load_config()
    provider = llm.resolve(cfg)
    timeout = int(cfg["extraction"]["subprocessTimeout"])
    todo = mrr.batches([u for u in units if u["pool"]], labels)
    for n, batch in enumerate(todo, 1):
        resp = provider(mrr.rater_prompt(batch), system=mrr.RATER_SYSTEM, model=rater,
                        timeout=timeout)
        for uid, row in mrr.parse_labels(resp.text, batch).items():
            labels.setdefault(uid, {}).update(row)
        save()
        print("labels: call %d/%d" % (n, len(todo)), file=sys.stderr)
    return len(todo)


# --- the report ---------------------------------------------------------------------

def arm_b_rows(b: Dict[str, Any], labels: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    rows = []
    for n, r in enumerate(b.get("rows", []), 1):
        rt = rates(r["units"], labels)
        rows.append({"n": n, "sid": r["sid"][:8],
                     "date": datetime.fromtimestamp(r["start"]).strftime("%m-%d"),
                     "live": r["counts"]["live"], "staged": r["counts"]["staged"],
                     "carries_next": bool(r["carry_next"].get("carries")),
                     "topics_next": len(r["carry_next"].get("local_topics") or [])
                     + len(r["carry_next"].get("universal_topics") or []),
                     "extracted": r["extraction"] is not None, "calls": r["calls"], **rt})
    return rows


def report(progress: Dict[str, Any], labels: Dict[str, Dict[str, int]],
           work: Optional[Path] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"meta": progress.get("meta", {}), "calls": progress.get("calls", {})}
    if work is not None:
        out["errors"] = {arm: error_counts(work / ("arm-" + arm) / "vault") for arm in ("a", "b")}
    a = progress.get("a") or {}
    if a.get("units") is not None:
        out["a"] = {
            "backfill": {k: a["backfill"][k] for k in ("exit", "calls", "counts")},
            "carry_after_backfill": {k: v for k, v in a["backfill"]["carry"].items() if k != "text"},
            "extract": {k: a["extract"][k] for k in ("summary", "calls", "counts")},
            "carry_first_session": {k: v for k, v in a["extract"]["carry"].items() if k != "text"},
            "carry_first_session_text": a["extract"]["carry"].get("text", ""),
            "next_prompts": rates(a["units"], labels),
            "first_turns": rates(a.get("first_turns") or [], labels),
        }
    b = progress.get("b") or {}
    if b.get("rows"):
        rows = arm_b_rows(b, labels)
        first = first_on_point(rows)
        first_fire = next((r["n"] for r in rows if r["fired"]), None)
        first_live = next((r["n"] for r in rows if r["live"]), None)
        units = [u for r in b["rows"] for u in r["units"]]
        out["b"] = {"rows": rows, "sessions": len(rows), "stopped": b.get("stopped"),
                    "first_on_point": first, "first_fire": first_fire, "first_live": first_live,
                    "carry0": bool((b.get("carry0") or {}).get("carries")),
                    "total": rates(units, labels)}
    return out


def report_lines(data: Dict[str, Any]) -> List[str]:
    meta = data.get("meta", {})
    lines = ["corpus %s: %s sessions, %s prompts; install after session %s; judge off"
             % (meta.get("corpus"), meta.get("sessions"), meta.get("prompts"), meta.get("install_after"))]
    calls = data.get("calls") or {}
    if calls:
        lines.append("model calls: %s total (budget %s), API-price equivalent $%.2f; by model %s"
                     % (calls.get("total"), calls.get("budget"), calls.get("usd", 0.0),
                        calls.get("by_model")))
    b = data.get("b")
    a = data.get("a")
    lines.append("")
    lines.append("HEADLINE")
    if b:
        n = b["sessions"]
        lines.append("  arm (b): first on-point injection at session %s (of %d run)%s"
                     % (b["first_on_point"] if b["first_on_point"] else "> %d" % n, n,
                        "; %s" % b["stopped"] if b["stopped"] else ""))
    if a:
        np_ = a["next_prompts"]
        lines.append("  arm (a): after the install backfill, %d live page(s), %d staged; the first session "
                     "%s; reflex fires on %d of %d prompts (%s), %d on-point pair(s)"
                     % (a["extract"]["counts"]["live"], a["extract"]["counts"]["staged"],
                        "carries something" if a["carry_first_session"]["carries"] else "carries nothing",
                        np_["fired"], np_["prompts"], pct(np_["fired"], np_["prompts"]), np_["on_point"]))
    if a:
        lines += ["", "ARM (a) — with history"]
        bf = a["backfill"]
        lines.append("  backfill: exit %s, %d call(s); after it: %s"
                     % (bf["exit"], bf["calls"], bf["counts"]))
        lines.append("  first extraction: %d call(s), %s; after it: %s"
                     % (a["extract"]["calls"], a["extract"]["summary"], a["extract"]["counts"]))
        c = a["carry_first_session"]
        lines.append("  first SessionStart: topics local %d / universal %d, last-briefing %s, "
                     "staged notice %s, learned block %s, offer %s"
                     % (len(c.get("local_topics") or []), len(c.get("universal_topics") or []),
                        c.get("last_briefing"), bool(c.get("staged_notice")), bool(c.get("learned")),
                        bool(c.get("offer"))))
        for key, name in (("next_prompts", "next prompts"), ("first_turns", "first turns")):
            r = a[key]
            lines.append("  %s: %d, fired %d (%s), pairs %d, on-point %d of %d labelled"
                         % (name, r["prompts"], r["fired"], pct(r["fired"], r["prompts"]),
                            r["pairs"], r["on_point"], r["labelled"]))
    if b:
        t = b["total"]
        lines += ["", "ARM (b) — without history",
                  "  first live page after session %s; first injection in session %s; "
                  "first on-point in session %s" % (b["first_live"], b["first_fire"], b["first_on_point"]),
                  "  all sessions: %d prompts, fired %d (%s), pairs %d, on-point %d of %d labelled"
                  % (t["prompts"], t["fired"], pct(t["fired"], t["prompts"]), t["pairs"],
                     t["on_point"], t["labelled"]),
                  "",
                  "   n  date  sid       live staged extr calls carries topics  prompts fired on-point"]
        for r in b["rows"]:
            lines.append("  %2d  %s  %s  %4d %6d %4s %5d %7s %6d  %7d %5d %8d"
                         % (r["n"], r["date"], r["sid"], r["live"], r["staged"],
                            "yes" if r["extracted"] else "-", r["calls"],
                            "yes" if r["carries_next"] else "no", r["topics_next"],
                            r["prompts"], r["fired"], r["on_point"]))
    for arm, counts in sorted((data.get("errors") or {}).items()):
        if counts:
            lines.append("")
            lines.append("arm (%s) error log: %s" % (arm, ", ".join(
                "%s x%d" % kv for kv in sorted(counts.items()))))
    guard = (data.get("meta") or {}).get("real_vault")
    if guard:
        lines += ["", "~/mnemo during the run: %d file(s) changed, %d naming the corpus or this run%s"
                  % (guard["changed"], len(guard["suspects"]),
                     (": " + ", ".join(guard["suspects"][:10])) if guard["suspects"] else "")]
    return lines


# --- main -----------------------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


def estimate(sessions: Sequence[Session], history: Sequence[Session], future: Sequence[Session],
             plan: Sequence[Dict[str, Any]], cfg: Dict[str, Any], *, prompts: int,
             max_sessions: int, budget: int) -> Dict[str, Any]:
    from mnemo.cli.commands.backfill import _estimate_input_tokens

    ext = cfg["extraction"]
    model, gate = ext["model"], (ext.get("referenceGate") or {}).get("model") or ext["model"]
    bf = cfg["backfill"]
    cap = int((cfg.get("reflex") or {}).get("maxEmissionsPerSession", 10))
    chosen = install_selection(history, int(bf["installCap"]), int(bf["minFileMutations"]))
    harvest_in = _estimate_input_tokens(chosen)
    pages = PAGES_PER_HARVEST * len(chosen)
    a_extract = 2 * 3 * int(math.ceil(pages / 3.0 / ext["chunkSize"])) if pages else 0
    a_pairs = sum(max_pairs(1, cap) for _ in first_prompts(future, prompts))
    a_labels = label_calls(min(a_pairs, 2 * prompts)) + label_calls(2 * len(first_turns(future, prompts)))
    a_calls = len(chosen) + a_extract + a_labels
    a_usd = (price(model, harvest_in, OUT_TOKENS["harvest"] * len(chosen))
             + price(model, pages * TOKENS_PER_PAGE, OUT_TOKENS["extract"] * a_extract // 2)
             + price(gate, pages * TOKENS_PER_PAGE, OUT_TOKENS["gate"] * a_extract // 2)
             + price(DEFAULT_RATER, a_labels * 40 * 200, OUT_TOKENS["label"] * a_labels))

    fit = 0
    spent = a_calls
    pairs = 0
    for s, step in zip(sessions, plan):
        if fit >= max_sessions:
            break
        need = spent + step["worst"] + label_calls(pairs + max_pairs(len(s.prompts), cap))
        if need > budget:
            break
        spent += step["worst"]
        pairs += max_pairs(len(s.prompts), cap)
        fit += 1
    run = list(zip(sessions, plan))[:fit]
    brief = [s for s, st in run if st["briefing"]]
    b_extract = sum(st["extract_calls"] for _, st in run)
    b_labels = label_calls(pairs)
    b_usd = (price(model, _estimate_input_tokens(brief), OUT_TOKENS["briefing"] * len(brief))
             + price(model, sum(st["consumed"] for _, st in run) * TOKENS_PER_BRIEFING,
                     OUT_TOKENS["extract"] * b_extract // 2)
             + price(gate, sum(st["consumed"] for _, st in run) * TOKENS_PER_PAGE,
                     OUT_TOKENS["gate"] * b_extract // 2)
             + price(DEFAULT_RATER, pairs * 200, OUT_TOKENS["label"] * b_labels))
    return {"a": {"history": len(history), "install_selected": len(chosen),
                  "harvest_calls": len(chosen), "extract_calls_bound": a_extract,
                  "label_calls_bound": a_labels, "calls": a_calls, "usd": a_usd,
                  "harvest_tokens_in": harvest_in},
            "b": {"sessions_fit": fit, "of": len(sessions), "briefing_calls": len(brief),
                  "extractions": sum(1 for _, st in run if st["extract"]),
                  "extract_calls_bound": b_extract, "label_calls_bound": b_labels,
                  "calls": len(brief) + b_extract + b_labels, "usd": b_usd,
                  "prompts": sum(len(s.prompts) for s, _ in run)},
            "a_label_reserve": a_labels}


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="plan, calls and cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="run the arms (model calls), then label")
    ap.add_argument("--arm", choices=("a", "b", "both"), default="both")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="transcript directory (default: ~/%s)" % DEFAULT_CORPUS)
    ap.add_argument("--work", type=Path, default=None,
                    help="vaults, progress and labels (default: ~/%s)" % DEFAULT_WORK)
    ap.add_argument("--install-after", type=int, default=None,
                    help="sessions of history at install (default: half the corpus)")
    ap.add_argument("--prompts", type=int, default=PROMPTS)
    ap.add_argument("--max-sessions", type=int, default=10 ** 6, help="arm (b) session cap")
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--rater", default=DEFAULT_RATER)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    home = Path.home()
    args.corpus = (args.corpus or home / DEFAULT_CORPUS).expanduser()
    work = (args.work or home / DEFAULT_WORK).expanduser().resolve()
    real_vault = home / REAL_VAULT
    progress_path, labels_path = work / "progress.json", work / "labels.json"
    progress: Dict[str, Any] = _read(progress_path, {})
    all_labels: Dict[str, Dict[str, Dict[str, int]]] = _read(labels_path, {})
    labels = all_labels.setdefault(mrr.column(args.rater), {})

    if not args.dry_run and not args.send:
        if not progress:
            print("no results in %s; --dry-run, then --send" % work, file=sys.stderr)
            return 1
        data = report(progress, labels, work)
        if args.json:
            print(json.dumps(data, indent=1, default=str))
        else:
            print("\n".join(report_lines(data)))
        return 0

    sessions = load_sessions(args.corpus)
    meta = progress.get("meta") or {}
    # The corpus is live: a session still running in that repo grows it. A
    # resumed run keeps the sessions its first run saw, in the same order.
    if meta.get("sids"):
        by_sid = {s.sid: s for s in sessions}
        sessions = [by_sid[sid] for sid in meta["sids"] if sid in by_sid]
    if not sessions:
        raise SystemExit("error: no transcripts in %s" % args.corpus)
    install_after = args.install_after if args.install_after is not None else meta.get(
        "install_after", len(sessions) // 2)
    if meta and meta.get("install_after") != install_after:
        raise SystemExit("error: %s was run with --install-after %s; use another --work"
                         % (work, meta.get("install_after")))
    history, future = sessions[:install_after], sessions[install_after:]
    cfg = config.load_config(missing_path=Path("/nonexistent/mnemo.config.json"))  # defaults: a new user's config
    plan = plan_b(sessions, min_interval_min=int(cfg["extraction"]["auto"]["minIntervalMinutes"]),
                  min_new=int(cfg["extraction"]["auto"]["minNewMemories"]),
                  min_mutations=1, chunk=int(cfg["extraction"]["chunkSize"]))
    est = estimate(sessions, history, future, plan, cfg, prompts=args.prompts,
                   max_sessions=args.max_sessions, budget=args.budget)

    if args.dry_run:
        ea, eb = est["a"], est["b"]
        memory_dir = args.corpus / "memory"
        print("corpus %s: %d sessions, %d prompts, %s to %s"
              % (args.corpus.name, len(sessions), sum(len(s.prompts) for s in sessions),
                 datetime.fromtimestamp(sessions[0].start).date(),
                 datetime.fromtimestamp(sessions[-1].start).date()))
        print("arm (a): %d session(s) of history; the install run harvests %d (cap %d, >= %d mutation); "
              "~%dk input tokens" % (ea["history"], ea["install_selected"], cfg["backfill"]["installCap"],
                                     cfg["backfill"]["minFileMutations"], ea["harvest_tokens_in"] // 1000))
        print("         calls: %d harvest + <= %d extraction (assumes %d pages per harvest) + <= %d label "
              "= <= %d, ~$%.2f" % (ea["harvest_calls"], ea["extract_calls_bound"], PAGES_PER_HARVEST,
                                   ea["label_calls_bound"], ea["calls"], ea["usd"]))
        print("arm (b): %d of %d session(s) fit the budget (%d prompts): %d briefing + <= %d extraction "
              "(%d run(s) on the transcript clock) + <= %d label = <= %d, ~$%.2f"
              % (eb["sessions_fit"], eb["of"], eb["prompts"], eb["briefing_calls"],
                 eb["extract_calls_bound"], eb["extractions"], eb["label_calls_bound"],
                 eb["calls"], eb["usd"]))
        print("total: <= %d model call(s) of a %d budget, ~$%.2f API-price equivalent "
              "(Max-plan usage, not money)" % (ea["calls"] + eb["calls"], args.budget,
                                                 ea["usd"] + eb["usd"]))
        print("not in either arm: %d Claude Code auto-memory file(s) in %s, which a real "
              "SessionEnd would mirror" % (len(list(memory_dir.glob("*.md"))), memory_dir))
        return 0

    if args.arm in ("a", "both") and est["a"]["calls"] > args.budget:
        raise SystemExit("error: arm (a) alone may need %d calls, over the %d budget"
                         % (est["a"]["calls"], args.budget))
    agent = corpus_agent(args.corpus)
    calls = progress.get("calls") or {}
    meter = Meter(args.budget, spent=int(calls.get("total", 0)), usd=float(calls.get("usd", 0.0)))
    meter.by_model = dict(calls.get("by_model") or {})
    progress["meta"] = {**meta, "corpus": args.corpus.name, "sessions": len(sessions),
                        "prompts": sum(len(s.prompts) for s in sessions),
                        "install_after": install_after, "agent": agent, "rater": args.rater,
                        "sids": [s.sid for s in sessions],
                        "estimate": est}

    def save() -> None:
        progress["calls"] = {"total": meter.calls, "usd": round(meter.usd, 4),
                             "by_model": meter.by_model, "budget": args.budget}
        _write(progress_path, progress)
        _write(labels_path, all_labels)

    before = fingerprint(real_vault)
    with tempfile.TemporaryDirectory(prefix="mnemo-day-one-") as scratch, meter.installed():
        ctx = {"work": work, "corpus": args.corpus, "agent": agent, "cwd": scratch,
               "history": history, "future": future, "sessions": sessions, "plan_b": plan,
               "prompts": args.prompts, "meter": meter, "max_sessions": args.max_sessions,
               "cap_emissions": int((cfg.get("reflex") or {}).get("maxEmissionsPerSession", 10)),
               "label_reserve_a": est["a_label_reserve"] if args.arm in ("a", "both")
               and not (progress.get("a") or {}).get("labelled") else 0}
        with mrr.mrl._chdir(scratch):
            if args.arm in ("a", "both"):
                run_arm_a(ctx, progress, save)
                with arm_vault(work / "arm-a" / "vault"):
                    label(progress["a"]["units"] + progress["a"]["first_turns"], labels, args.rater, save)
                progress["a"]["labelled"] = True
                ctx["label_reserve_a"] = 0
                save()
            if args.arm in ("b", "both"):
                run_arm_b(ctx, progress, save)
                with arm_vault(work / "arm-b" / "vault"):
                    label([u for r in progress["b"]["rows"] for u in r["units"]], labels,
                          args.rater, save)
    after = fingerprint(real_vault)
    diff = changed(before, after)
    progress["meta"]["real_vault"] = {
        "changed": len(diff), "paths": diff[:200],
        "suspects": suspects(diff, [args.corpus.name, agent + "/", "day-one", "mnemo-day-one"])}
    save()
    data = report(progress, labels, work)
    print("\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
