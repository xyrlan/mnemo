"""Day one with the reference gate on the auto-memory's project pages (#486).

Usage:
    PYTHONPATH=src python3 tools/measure_day_one_gate.py --dry-run   # bounds; calls nothing, sends nothing
    PYTHONPATH=src python3 tools/measure_day_one_gate.py --send      # gate, replay, label (resumes)
    PYTHONPATH=src python3 tools/measure_day_one_gate.py             # the report, from gate.json
    PYTHONPATH=src python3 tools/measure_day_one_gate.py --json

On day one the auto-memory mirror is the only source that reaches the reflex
(#472, arm (c)): 81 live pages, 80 of them ``project``, which
``promote_projects`` puts live with no model call and no gate. #480 found
arm (c)'s noise is those project status pages. This asks whether the
reference gate (#417) would have filtered them, **measuring only**: no
extraction code changes here.

It reads what ``tools/measure_day_one.py`` left under ``--work`` (default
``~/.cache/mnemo/day-one``) and writes only ``gate.json``, ``labels.json``
(new pairs, same rater column) and a held-out vault under ``gate-held/``.

1. **The gate.** Every live ``shared/project/`` page of arm (c)'s vault goes
   through the real :func:`mnemo.core.extract.reference_gate.judge_pages` —
   its system prompt, :func:`~reference_gate.view`, prompt builder, parser
   and failure handling — with the model ``extraction.referenceGate.model``
   ships with. ``judge_pages`` only asks about pages it would judge (an
   inferred ``reference``), so each project page is handed to it as one
   (:class:`GatePage`); the text the gate reads is the page's name and its
   retrieval body, the same view ``measure_backfill_routes`` gives raters.
   Pages go in ``extraction.chunkSize`` batches in slug order, one call each,
   as an extraction chunk would. A page the gate gave no answer for would
   stage in extraction; it is counted, and re-asked on a rerun.
2. **The counterfactual.** Arm (c)'s vault with the G and N pages removed
   (``measure_day_one.rewind`` over the remaining live pages, indexes
   rebuilt), then arm (c)'s same 50 prompts through the hook's decision,
   judge off and judge on (``measure_day_one.replay_prompts``, the stage
   #479 ran). Judge on reuses #479's Jev answer for a prompt whose pool is
   the same set of rules it was then (:class:`CachedJev`) and sends a new
   request only for a pool that changed. New pairs are labelled blind by the
   same rater, same column, so every number shares its labels with
   #472/#479's.
3. **The cost.** Held pages that carried an on-point injection in #472's
   run or in #479's (off or on): the value the gate would take away.

Budget: ``--claude-budget`` (15) gate plus label calls, ``--jev-budget``
(100) new Jev requests; ``--send`` refuses to start the replay when its
local bound (every ranked prompt asked, no answer reused) would cross the
Jev budget, and labels only what the Claude budget has left. Only
``--send`` leaves the machine: the gate sends page text to Claude, and Jev
gets what the hook sends (a prompt's first 1,200 characters, each rule's
first 800) — the same data #479 already sent.

**First run, 2026-09-24** (clubinho, the arm (c) vault and labels #472/#479
left; dry run bounded 9 Claude calls and 48 Jev requests). 10 Claude calls
of 15 (9 gate, 1 label), $0.31 API-price equivalent; 21 new Jev requests of
100, 27 prompts answered from #479's scores.

- **The gate holds 7 of 80: N 7, G 0, S 64, T 9.** It reads project status
  pages as what its own definition says they are: S, "how one particular
  system works — its components, data, conventions, decisions or incidents".
  The first pass lost one chunk of 10 (no parsable answer; extraction would
  have staged those 10), the rerun answered it.
- **Replay without the 7** (judge-off matches #479's judge-off on 48/50):
  judge off 7 → 6 of 50 fire (14% → 12%), 11 → 10 pairs, on-point 2 → 2,
  noise 9 → 8 (82% → 80%). Judge on 6 → 6 fire (12%), 7 → 7 pairs,
  on-point 3 → 2, noise 2 → 2 (29% → 29%).
- **The cost**: one held page carried an on-point injection,
  ``coletor-sgp-113`` (N), judge on; its slot went to a marginal rule.
- Of the 9 judge-off noise pairs, 8 come from pages the gate calls S or T.
  Day one's noise is status pages injected on prompts they are not about,
  a relevance miss, not a category the gate sorts out.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)  # its dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


day = _sibling("measure_day_one")
mrr = day.mrr

GATE_NAME = "gate.json"
HELD_DIR = "gate-held"
#: Claude calls (gate + labels) and new Jev requests (#486).
CLAUDE_BUDGET = 15
JEV_BUDGET = 100
#: What the counterfactual holds out: what both 2026-09-22 raters called junk.
HOLD = ("G", "N")
ARM = "c"


@dataclass(frozen=True)
class GatePage:
    """A live project page as ``judge_pages`` sees a page it judges.

    ``type`` and ``confidence`` are what :func:`reference_gate.needs_judging`
    asks: an inferred reference page. The gate's prompt carries neither, only
    :func:`reference_gate.view` of ``name`` and ``body``.
    """

    slug: str
    name: str
    body: str
    type: str = "reference"
    confidence: str = "inferred"
    judged: Optional[str] = None


def project_pages(vault: Path) -> List[GatePage]:
    """Every live ``shared/project/`` page, in slug order."""
    from mnemo.core.extract.scanner import parse_frontmatter as split_frontmatter
    from mnemo.core.filters import parse_frontmatter
    from mnemo.core.text_utils import retrieval_body

    out = []
    for md in sorted((Path(vault) / "shared" / "project").glob("*.md")):
        raw = md.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(raw) or {}
        _, body = split_frontmatter(raw)
        out.append(GatePage(slug=str(fm.get("slug") or md.stem), name=str(fm.get("name") or md.stem),
                            body=retrieval_body(body)))
    return out


def chunks(pages: Sequence[GatePage], size: int) -> List[List[GatePage]]:
    return [list(pages[i:i + size]) for i in range(0, len(pages), max(1, size))]


def run_gate(pages: Sequence[GatePage], verdicts: Dict[str, str], ask: Callable[[str], str],
             size: int, calls_left: int) -> int:
    """Judge every chunk holding a page without an answer; return the calls made.

    ``verdicts`` (slug -> category, ``""`` for no answer) is updated in place.
    """
    from mnemo.core.extract import reference_gate

    made = 0
    for chunk in chunks(pages, size):
        if all(verdicts.get(p.slug) for p in chunk):
            continue
        if made >= calls_left:
            break
        made += 1
        for p in reference_gate.judge_pages(chunk, ask):
            verdicts[p.slug] = p.judged or ""
    return made


def held_slugs(verdicts: Dict[str, str]) -> List[str]:
    return sorted(s for s, v in verdicts.items() if v in HOLD)


def verdict_counts(verdicts: Dict[str, str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in verdicts.values():
        out[v or "none"] = out.get(v or "none", 0) + 1
    return out


def hold_out(src: Path, dest: Path, held: Sequence[str]) -> Path:
    """``src`` with the ``held`` project pages removed, indexes rebuilt, at ``dest``."""
    from mnemo.core.filters import parse_frontmatter

    gone = set(held)
    keep = []
    for ptype in day.LIVE_TYPES:
        for md in sorted((src / "shared" / ptype).glob("*.md")):
            slug = str((parse_frontmatter(md.read_text(encoding="utf-8", errors="replace")) or {})
                       .get("slug") or md.stem)
            if not (ptype == "project" and slug in gone):
                keep.append("%s/%s" % (ptype, md.stem))
    marker = dest / ".mnemo" / "rewound.json"
    try:
        same = json.loads(marker.read_text(encoding="utf-8")) == sorted(keep)
    except (OSError, ValueError):
        same = False
    if not same and dest.exists():
        shutil.rmtree(str(dest))
    return day.rewind(src, dest, keep)


class CachedJev(day.Jev):
    """#479's stage, answering from #479's Jev scores when a prompt's pool is unchanged.

    ``cache`` maps (session, time) to the ``judge`` row #479 recorded. A hit
    needs the same set of rules asked and every one answered; the picks are
    then :func:`judge.chosen` over those scores at the shipped bar, which is
    what ``judge.ask`` returned for them. Anything else is a new request.
    """

    def __init__(self, client, chosen, budget, cache: Dict[Tuple[str, float], Dict[str, Any]], sent: int = 0):
        super().__init__(client, chosen, budget, sent)
        self.cache = cache
        self.hits = 0

    def stage(self, vault, project, rows):
        from mnemo.core.reflex import judge

        live = super().stage(vault, project, rows)

        def _stage(prompt, pool):
            hit = self.cache.get((prompt.session_id, prompt.ts))
            if hit and not hit.get("fallback") and hit.get("scores") \
                    and len(hit["scores"]) == hit.get("asked") \
                    and set(pool) == {s for s, _ in hit["scores"]}:
                picks = judge.chosen({s: float(v) for s, v in hit["scores"]},
                                     float(self.chosen["injectAt"]))
                rows[(prompt.session_id, prompt.ts)] = dict(hit, cached=True, injected=len(picks),
                                                            fallback=False)
                self.hits += 1
                return picks
            return live(prompt, pool)

        return _stage


def jev_cache(units: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, float], Dict[str, Any]]:
    return {(u["session_id"], u["ts"]): u["judge"] for u in units if u.get("judge")}


def carried(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
            slugs: Sequence[str]) -> Dict[str, Dict[str, int]]:
    """Per slug in ``slugs``: pairs injected, and how many the rater called on-point."""
    want = set(slugs)
    out: Dict[str, Dict[str, int]] = {}
    for u in units:
        for c in u["pool"]:
            if c["slug"] in want:
                row = out.setdefault(c["slug"], {"pairs": 0, "on_point": 0})
                row["pairs"] += 1
                row["on_point"] += labels.get(u["uid"], {}).get(c["slug"]) == day.ON_POINT
    return out


def load_source(work: Path, corpus: Path) -> Dict[str, Any]:
    """Arm (c)'s vault and prompts, #472's units and #479's (off and on)."""
    src = day.judge_sources(work, work, corpus).get(ARM)
    if src is None:
        raise SystemExit("error: no arm (%s) run in %s; measure_day_one.py --send --arm c first" % (ARM, work))
    st = (day._read(work / day.JUDGE_NAME, {}).get("arms") or {}).get(ARM) or {}
    if st.get("units") is None:
        raise SystemExit("error: no judged arm (%s) in %s; measure_day_one.py --judge --send first"
                         % (ARM, work))
    return dict(src, recorded=src["off"], off479=st["off_units"], on479=st["units"])


def report(state: Dict[str, Any], src: Dict[str, Any], labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    verdicts = state.get("verdicts") or {}
    held = held_slugs(verdicts)
    runs = {"472 recorded": src["recorded"], "479 off": src["off479"], "479 on": src["on479"]}
    if state.get("off_units") is not None:
        runs["held off"] = state["off_units"]
    if state.get("units") is not None:
        runs["held on"] = state["units"]
    rows = {}
    for name, units in runs.items():
        r = day.rates(units, labels)
        r["noise_share"] = day.noise_share(r)
        rows[name] = r
    cost = {name: carried(runs[name], labels, held) for name in ("472 recorded", "479 off", "479 on")}
    cost_slugs = sorted({s for c in cost.values() for s, v in c.items() if v["on_point"]})
    return {"pages": len(verdicts), "verdicts": verdict_counts(verdicts),
            "by_verdict": {k: sorted(s for s, v in verdicts.items() if (v or "none") == k)
                           for k in verdict_counts(verdicts)},
            "held": held, "runs": rows, "cost": cost, "cost_slugs": cost_slugs,
            "gate_calls": state.get("gate_calls", 0), "label_calls": state.get("label_calls", 0),
            "usd": round(state.get("usd", 0.0), 4), "claude_budget": state.get("claude_budget"),
            "requests": state.get("requests", 0), "cached": state.get("cached", 0),
            "jev_budget": state.get("jev_budget"), "gate_model": state.get("gate_model"),
            "off_check": state.get("off_check")}


def report_lines(data: Dict[str, Any]) -> List[str]:
    v = data["verdicts"]
    lines = ["reference gate (%s) over arm (c)'s %d live project page(s): %s"
             % (data.get("gate_model"), data["pages"],
                ", ".join("%s %d" % (k, v[k]) for k in ("G", "N", "S", "T", "none") if k in v)),
             "held out (G or N): %d page(s)" % len(data["held"]),
             "calls: gate %d + labels %d of a %s Claude budget, $%.2f API-price equivalent; "
             "Jev %d new request(s) of a %s budget, %d answer(s) reused from #479"
             % (data["gate_calls"], data["label_calls"], data["claude_budget"], data["usd"],
                data["requests"], data["jev_budget"], data["cached"]),
             ""]
    if data.get("off_check"):
        lines.append("held-out vault, judge off, matches #479's judge-off replay on %d of %d prompt(s)"
                     % tuple(data["off_check"]))
    lines.append("run            prompts  fired  emit    pairs  labelled  on-point  marginal  noise  noise%")
    for name, r in data["runs"].items():
        lines.append("%-14s %7d  %5d  %-6s %5d  %8d  %8d  %8d  %5d  %6s"
                     % (name, r["prompts"], r["fired"], day.pct(r["fired"], r["prompts"]), r["pairs"],
                        r["labelled"], r["on_point"], r["marginal"], r["noise"],
                        day._share(r["noise_share"])))
    lines.append("")
    lines.append("held pages that carried an on-point injection (the gate's cost): %d"
                 % len(data["cost_slugs"]))
    for name, c in data["cost"].items():
        lines.append("  %-13s held pages injected %d time(s), %d on-point%s"
                     % (name, sum(x["pairs"] for x in c.values()), sum(x["on_point"] for x in c.values()),
                        (": " + ", ".join(s for s in sorted(c) if c[s]["on_point"]))
                        if any(x["on_point"] for x in c.values()) else ""))
    lines.append("")
    for k in ("G", "N", "S", "T", "none"):
        if data["by_verdict"].get(k):
            lines.append("%s: %s" % (k, ", ".join(data["by_verdict"][k])))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm
    from mnemo.core.extract import reference_gate
    from mnemo.core.mcp import rerank
    from mnemo.core.reflex import judge

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="bounds; calls nothing, sends nothing")
    ap.add_argument("--send", action="store_true", help="gate, replay and label (model calls, Jev)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--work", type=Path, default=None,
                    help="measure_day_one.py's --work (default: ~/%s)" % day.DEFAULT_WORK)
    ap.add_argument("--corpus", type=Path, default=None,
                    help="transcript directory (default: ~/%s)" % day.DEFAULT_CORPUS)
    ap.add_argument("--rater", default=day.DEFAULT_RATER)
    ap.add_argument("--claude-budget", type=int, default=CLAUDE_BUDGET, help="gate + label calls")
    ap.add_argument("--jev-budget", type=int, default=JEV_BUDGET, help="new Jev requests")
    args = ap.parse_args(argv)

    home = Path.home()
    corpus = (args.corpus or home / day.DEFAULT_CORPUS).expanduser()
    work = (args.work or home / day.DEFAULT_WORK).expanduser().resolve()
    state_path, labels_path = work / GATE_NAME, work / "labels.json"
    state: Dict[str, Any] = day._read(state_path, {})
    all_labels: Dict[str, Dict[str, Dict[str, int]]] = day._read(labels_path, {})
    labels = all_labels.setdefault(mrr.column(args.rater), {})
    src = load_source(work, corpus)
    defaults = config.load_config(missing_path=Path("/nonexistent/mnemo.config.json"))
    gate_model = str((defaults["extraction"].get("referenceGate") or {}).get("model")
                     or defaults["extraction"]["model"])
    size = int(defaults["extraction"]["chunkSize"])
    pages = project_pages(src["vault"])
    chosen = day.shipped_judge()

    if not args.dry_run and not args.send:
        if not state.get("verdicts"):
            print("no gate results in %s; --dry-run, then --send" % work, file=sys.stderr)
            return 1
        data = report(state, src, labels)
        print(json.dumps(data, indent=1) if args.json else "\n".join(report_lines(data)))
        return 0

    verdicts: Dict[str, str] = state.setdefault("verdicts", {})
    gate_todo = sum(1 for c in chunks(pages, size) if not all(verdicts.get(p.slug) for p in c))
    if args.dry_run:
        cap = int((defaults.get("reflex") or {}).get("maxEmissionsPerSession", 10))
        with tempfile.TemporaryDirectory(prefix="mnemo-day-one-gate-") as tmp:
            # Before the gate has answered, the vault it would thin is not known;
            # the full one ranks at least as many prompts.
            vault = (hold_out(src["vault"], Path(tmp) / "vault", held_slugs(verdicts))
                     if not gate_todo else src["vault"])
            with day.arm_vault(vault):
                asks = day.request_bound(vault, src["agent"], src["items"])
        # Judge on and judge off each inject at most this many; both are labelled.
        pairs = 2 * day.pairs_bound(asks, chosen["candidates"], cap)
        print("gate: %d live project page(s) in %d chunk(s) of %d; %d call(s) to %s still owed%s"
              % (len(pages), len(chunks(pages, size)), size, gate_todo, gate_model,
                 "" if gate_todo else "; holds %d (G or N)" % len(held_slugs(verdicts))))
        print("replay: %d prompt(s) over the %s vault; Jev <= %d request(s) (every ranked prompt "
              "asked, nothing reused from #479) of a %d budget"
              % (len(src["items"]), "held-out" if not gate_todo else "full", sum(asks), args.jev_budget))
        print("labels: <= %d call(s) for <= %d pair(s) on and off, fewer where #472/#479 labelled them; "
              "Claude total <= %d of a %d budget (labelling stops at it)"
              % (day.label_calls(pairs), pairs,
                 int(state.get("gate_calls", 0)) + gate_todo + day.label_calls(pairs), args.claude_budget))
        return 0

    cfg = config.load_config()
    provider = llm.resolve(cfg)
    timeout = max(300, int(cfg["extraction"]["subprocessTimeout"]))
    state.update({"gate_model": gate_model, "claude_budget": args.claude_budget,
                  "jev_budget": args.jev_budget})

    def save() -> None:
        day._write(state_path, state)
        day._write(labels_path, all_labels)

    def ask(prompt: str) -> str:
        resp = provider(prompt, system=reference_gate.SYSTEM_PROMPT, model=gate_model, timeout=timeout)
        state["usd"] = float(state.get("usd", 0.0)) + float(resp.total_cost_usd or 0.0)
        return resp.text

    def claude_left() -> int:
        return args.claude_budget - int(state.get("gate_calls", 0)) - int(state.get("label_calls", 0))

    with tempfile.TemporaryDirectory(prefix="mnemo-day-one-gate-") as scratch, mrr.mrl._chdir(scratch), \
            day.arm_vault(src["vault"]):
        state["gate_calls"] = int(state.get("gate_calls", 0)) + run_gate(
            pages, verdicts, ask, size, claude_left())
    save()
    print("gate: %s" % verdict_counts(verdicts), file=sys.stderr)
    if any(not verdicts.get(p.slug) for p in pages):
        print("gate: %d page(s) without an answer; the replay waits for them (rerun --send)"
              % sum(1 for p in pages if not verdicts.get(p.slug)), file=sys.stderr)
        print("\n".join(report_lines(report(state, src, labels))))
        return 1

    held = held_slugs(verdicts)
    vault = hold_out(src["vault"], work / HELD_DIR / "vault", held)
    if state.get("held") != held:
        for k in ("off_units", "units", "off_check", "requests", "cached"):
            state.pop(k, None)
    state["held"] = held
    if state.get("units") is None:
        with day.arm_vault(vault):
            bound = sum(day.request_bound(vault, src["agent"], src["items"]))
            if bound > args.jev_budget:
                raise SystemExit("error: the replay may need %d Jev request(s), over the %d budget"
                                 % (bound, args.jev_budget))
            key, key_source = judge.resolve_key(dict(chosen))
            if not key:
                print("error: no TypeSafe key (%s or ~/.mnemo/secrets.json)" % chosen["keyEnv"],
                      file=sys.stderr)
                return 1
            client = rerank.typesafe_client(key, model=chosen["model"],
                                            timeout=float(chosen["timeoutSeconds"]))
            jev = CachedJev(client, chosen, args.jev_budget, jev_cache(src["on479"]))
            texts: Dict[str, str] = {}
            off = day.adopt_uids(day.replay_prompts(vault, src["agent"], src["items"], texts), src["off479"])
            on = day.adopt_uids(day.replay_prompts(vault, src["agent"], src["items"], texts, jev),
                                src["off479"])
        state.update({"off_units": off, "units": on, "off_check": day.reproduced(off, src["off479"]),
                      "requests": jev.sent, "cached": jev.hits, "key_source": key_source})
        save()
    left = claude_left()
    if left > 0:
        with tempfile.TemporaryDirectory(prefix="mnemo-day-one-gate-") as scratch, \
                mrr.mrl._chdir(scratch), day.arm_vault(vault):
            state["label_calls"] = int(state.get("label_calls", 0)) + day.label(
                state["off_units"] + state["units"], labels, args.rater, save, limit=left)
        save()
    data = report(state, src, labels)
    print(json.dumps(data, indent=1) if args.json else "\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
