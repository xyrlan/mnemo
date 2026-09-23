"""Does an injected on-point rule change the agent's answer at all? (#434)

Usage:
    PYTHONPATH=src python3 tools/measure_rule_lift.py --dry-run             # pairs, calls, tokens; calls nothing
    PYTHONPATH=src python3 tools/measure_rule_lift.py --send                # answer both arms, then judge
    PYTHONPATH=src python3 tools/measure_rule_lift.py --send --limit 2      # a smoke run on the first 2 pairs
    PYTHONPATH=src python3 tools/measure_rule_lift.py                       # the report, local

Everything tuned so far grades whether the *right rule was shown*. This asks
whether showing it *did anything*.

**Population.** Every (prompt, rule) pair the blind rater of #411 scored 2
("inject") in ``reflex-labels-<rater>.json``: 64 pairs over 55 prompts. The
prompt is re-read from its transcript — the full text the agent saw, not the
1,200 characters ``reflex-units.json`` keeps — matched by the unit's own id
(``measure_reflex_gate.unit_id``), together with the **last assistant turn
before it** (its text blocks, the last :data:`CONTEXT_CHARS` characters), so a
prompt like "sim, pode rodar" means what it meant. The pairs are frozen into
``pairs.json`` on first run and never rebuilt under existing answers.

**Arms.** Same model, same :data:`ARM_SYSTEM`, no tools, through
``core.llm``'s provider, from a scratch working directory so neither arm
inherits a project's ``CLAUDE.md`` or auto-memory:

- A: the previous turn and the prompt;
- B: the same, with the rule appended the way Claude Code delivers a
  ``UserPromptSubmit`` hook's context — a ``<system-reminder>`` carrying the
  text :func:`mnemo.hooks.user_prompt_submit._emit_reflex_context` writes, run
  here and captured, so the injection is the hook's own bytes.

**Judge, blind to the arm.** One rule and one answer per item, every item of
every pair shuffled together with a fixed seed, ten a call, numbered 1..10 so
nothing in the request names the arm. Before the judge reads an answer the
rule's slug, ``read_mnemo_rule`` and the reflex header are masked in *both*
arms: B's answer quoting ``[[slug]]`` back would otherwise tell the judge
which arm it is reading. The count of masked answers per arm is reported.

**Metric (pre-registered in #434).** Per pair, follow = 1 if the judge said
"yes". Lift = mean(B − A) over pairs where the judge did not answer "na" for
both arms, with a paired bootstrap 95% CI (:data:`BOOTSTRAP` resamples,
seeded). Secondary: the share of those pairs where A already follows the rule
(the rule was redundant). Decision rule, fixed before the run:

- CI excludes 0 and lift ≥ 10 pp → rules carry; per-rule lift is a pruning signal;
- CI includes 0 → stop tuning recall/reflex, invest in the briefing;
- anything else (CI above 0 but lift < 10 pp, or CI below 0) fits neither
  branch, and the report says so rather than rounding it into one.

**Cost.** The maintainer runs on a Max subscription, so these calls draw on
the plan's allowance. The dollar figure printed is the CLI's
``total_cost_usd`` — the API-price equivalent of that usage — never money
spent (#441).

Limits to carry with any number from here: the labels are one model's, not
the developer's; no tools and one preceding turn is less context than a real
session, so B − A is a proxy for the real effect, not the effect; 64 pairs
gives a wide CI — it can say "none" or "large", not "+4 pp"; and the
subprocess still carries the user's own non-mnemo plugin hooks, identically
in both arms.

First run, 2026-09-22, 1 sample per arm, arms and judge ``claude-sonnet-5``:
the judge answered all 128 answers, and 7 pairs were "na" in both arms. Over
the other 57 pairs the follow rate went from 45.6% (A) to 75.4% (B): lift
**+29.8 pp, 95% CI [+12.3, +47.4]**, 25 pairs gained and 8 lost. That is the
"rules carry" branch. 45.6% of pairs were redundant (A already followed the
rule). Dropping the 20 pairs whose B answer was masked or still named the
reflex or "memory" gives +28.9 pp [+5.3, +50.0]. The 8 lost pairs show how
much one sample moves on its own. So a one-pair rule at −100 pp is not yet
evidence to retire it: per-rule pruning needs ``--samples 2`` or more first.
Cost: 128 arm calls at $3.53 plus 13 judge calls at $0.90, the API-price
equivalent of subscription usage. The dry run had predicted $4.14.

Second sample, same day (``--send --samples 2``, which added only the missing
half: 128 arm calls at $3.41 and 13 judge calls at $0.97). Over both samples,
3 pairs were "na" in both arms. Over the other 61, the follow rate went from
41.0% (A) to 72.1% (B): lift **+31.1 pp, 95% CI [+19.7, +42.6]**, 34 pairs
gained and 7 lost. The branch is the same and the CI is tighter. "Redundant"
rose to 60.7% because it counts A following the rule in *either* sample, so
it is not comparable with the one-sample 45.6%. Per rule, samples are not the
bottleneck: 43 of the 52 rules have a single pair (9 have two or three), so a
rule's lift still moves in steps of 50 pp. The lowest are five one-pair rules
at −50 pp and one two-pair rule at −25 pp, and none is negative across two
pairs. Retiring a rule on its lift needs more pairs per rule, not a third
sample.

Files, under ``<vault>/.mnemo/rule-lift/``: ``pairs.json``, ``answers.json``
(per arm column — model and :data:`ARM_SYSTEM` hash — per pair, per arm, a
list of samples; saved after every call) and ``verdicts.json`` (per arm column
and judge column, per answer id; saved after every call). A changed model or
prompt is a new column, so a report never mixes two.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent

OUT_DIR = "rule-lift"
PAIRS_NAME = "pairs.json"
ANSWERS_NAME = "answers.json"
VERDICTS_NAME = "verdicts.json"

#: The arms' model, pre-registered.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_JUDGE = "claude-sonnet-5"
DEFAULT_RATER = "claude-fable-5-1"

#: The label that makes a pair: the rater's "inject".
ON_POINT = 2

#: How much of the previous assistant turn both arms see: its tail, which is
#: where a turn's report to the user sits.
CONTEXT_CHARS = 4000

#: How much of a rule the judge reads.
RULE_CHARS = 1500

#: Answers per judge call.
JUDGE_CHUNK = 10

BOOTSTRAP = 10000
SEED = 434

#: The decision rule's bar on the point estimate.
MIN_LIFT = 0.10

YES, NO, NA = "yes", "no", "na"
ARMS = ("A", "B")

#: API list price of claude-sonnet-5, USD per million tokens, for the dry run
#: only; a real run reports the CLI's own ``total_cost_usd``.
USD_IN_PER_MTOK = 2.0
USD_OUT_PER_MTOK = 10.0
#: What ``claude --print`` adds to every call's input on this machine
#: (environment and plugin reminders): measured 1006 input tokens for a
#: one-sentence prompt, 2026-09-22.
CLI_OVERHEAD_TOKENS = 1000
#: Length of one arm's answer, for the dry run: the 2026-09-22 smoke run's
#: four answers were 1992-2780 output tokens.
ANSWER_TOKENS = 2300
JUDGE_OUT_TOKENS = 80

ARM_SYSTEM = (
    "You are an AI coding agent continuing a working session with a developer "
    "in their project. You cannot run tools for this reply. Write the reply you "
    "would send, and state concretely what you would do next — the commands you "
    "would run, the edits you would make, the decisions you would take. Reply "
    "in the language the developer writes in. The environment details you are "
    "shown (working directory, git status) describe a sandbox this reply is "
    "written in, not the developer's machine: take the project and the state "
    "the conversation describes as the real ones."
)

JUDGE_SYSTEM = (
    "You grade whether an AI coding agent's answer acts on a rule. Each numbered "
    "item gives you a RULE and an ANSWER the agent wrote to a developer. Answer "
    "per item:\n"
    "- yes: the answer acts on the rule — it does what the rule says, or applies "
    "a specific fact, command, caution or decision the rule carries;\n"
    "- no: the rule bears on what the answer is doing, but the answer does not "
    "act on it (ignores it, or goes against it);\n"
    "- na: the rule does not bear on what this answer is about.\n"
    "Judge what the answer does, not whether it names the rule. Some text may be "
    "masked as [...]; ignore that. Reply with one JSON object mapping each item "
    'number to "yes", "no" or "na", and nothing else.'
)

MASK = "[...]"
_REFLEX_HEADER = "mnemo reflex context"


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the population -----------------------------------------------------------

def on_point_pairs(labels: Dict[str, Dict[str, int]]) -> List[Tuple[str, str]]:
    """(uid, slug) for every pair the rater scored :data:`ON_POINT`, sorted."""
    return sorted((uid, slug) for uid, row in labels.items()
                  for slug, v in row.items() if v == ON_POINT)


def pair_id(uid: str, slug: str) -> str:
    return "%s:%s" % (uid, slug)


def _assistant_text(msg: Any) -> str:
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "").strip() for b in content
                     if isinstance(b, dict) and b.get("type") == "text"
                     and b.get("text", "").strip())


def prompt_in_context(lines: Sequence[str], session_id: str, uid: str,
                      uid_of: Callable[[str, float, str], str]) -> Optional[Tuple[str, str]]:
    """(full prompt, previous assistant turn) for the prompt whose id is *uid*.

    A human prompt is what :func:`mnemo.core.reflex.replay.read_prompts`
    counts as one — the reader that built the units — so the ids line up. The
    previous turn is every assistant text block since the human prompt before
    it; tool calls and tool results are not text a developer read. When that
    turn is empty — the prompt is the output of a ``! command`` the developer
    ran, which follows its ``<bash-input>`` with no reply between — it is the
    last turn that had text.
    """
    from mnemo.core.reflex.replay import _parse_ts
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    turn: List[str] = []
    last: List[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if kind == "assistant":
            text = _assistant_text(entry.get("message"))
            if text:
                turn.append(text)
            continue
        if kind != "user" or entry.get("isMeta"):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        text = plain_user_text(msg.get("content"))
        if not text or SYNTHETIC_TURN.search(text):
            continue
        ts = _parse_ts(entry.get("timestamp"))
        if ts is None:
            continue
        if turn:
            last = turn
        if uid_of(session_id, ts, text) == uid:
            return text, "\n\n".join(last)
        turn = []
    return None


def tail(text: str, limit: int = CONTEXT_CHARS) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def injection(index: Dict[str, Any], slug: str) -> str:
    """The additionalContext the hook emits for *slug*, from the hook's own code."""
    from mnemo.hooks import user_prompt_submit as ups

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ups._emit_reflex_context(index, [slug])
    return json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]


def build_pairs(units: Dict[str, Dict[str, Any]], labels: Dict[str, Dict[str, int]],
                transcripts: Dict[str, Path], index: Dict[str, Any],
                uid_of: Callable[[str, float, str], str]) -> List[Dict[str, Any]]:
    """Every on-point pair with what both arms and the judge need.

    A prompt whose transcript is gone keeps the unit's stored text and an empty
    previous turn, flagged ``context: false``, rather than silently leaving the
    population.
    """
    found: Dict[str, Optional[Tuple[str, str]]] = {}
    out = []
    for uid, slug in on_point_pairs(labels):
        unit = units[uid]
        if uid not in found:
            path = transcripts.get(unit["session_id"])
            lines = (path.read_text(encoding="utf-8", errors="replace").splitlines()
                     if path else [])
            found[uid] = prompt_in_context(lines, unit["session_id"], uid, uid_of)
        hit = found[uid]
        cand = next(c for c in unit["candidates"] if c["slug"] == slug)
        out.append({
            "id": pair_id(uid, slug), "uid": uid, "slug": slug,
            "project": unit["project"],
            "prompt": hit[0] if hit else unit["prompt"],
            "previous": tail(hit[1]) if hit else "",
            "context": hit is not None,
            "rule": cand["text"][:RULE_CHARS],
            "injection": injection(index, slug),
        })
    return out


# --- the arms ---------------------------------------------------------------

def arm_prompt(pair: Dict[str, Any], arm: str) -> str:
    """What one arm sends. A and B differ by the reminder block and nothing else."""
    parts = []
    if pair["previous"]:
        parts.append("Your previous reply in this session:\n<assistant>\n%s\n</assistant>"
                     % pair["previous"])
    parts.append("The developer's next message:\n<user>\n%s\n</user>" % pair["prompt"])
    if arm == "B":
        parts.append("<system-reminder>\nUserPromptSubmit hook additional context: %s\n"
                     "</system-reminder>" % pair["injection"])
    return "\n\n".join(parts)


def arm_order(pid: str) -> Tuple[str, str]:
    """Which arm a pair calls first, by its id: neither arm is always the later one."""
    return ARMS if int(hashlib.md5(pid.encode()).hexdigest(), 16) % 2 else ARMS[::-1]


def pending_calls(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
                  samples: int) -> List[Tuple[Dict[str, Any], str]]:
    """(pair, arm) still owed a sample, in call order."""
    out = []
    for k in range(samples):
        for p in pairs:
            for arm in arm_order(p["id"]):
                if len(answers.get(p["id"], {}).get(arm, [])) <= k:
                    out.append((p, arm))
    return out


# --- the judge ----------------------------------------------------------------

def answer_id(pid: str, arm: str, k: int) -> str:
    return "%s|%s|%d" % (pid, arm, k)


def mask(answer: str, slug: str) -> Tuple[str, bool]:
    """The answer with every trace of the injection's wording masked, both arms alike."""
    pattern = re.compile(r"\[\[[^\]]*\]\]|%s|read_mnemo_rule|%s" % (
        re.escape(slug), re.escape(_REFLEX_HEADER)), re.IGNORECASE)
    masked, n = pattern.subn(MASK, answer)
    return masked, n > 0


def judge_items(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
                done: Dict[str, str], *, seed: int = SEED) -> List[Dict[str, str]]:
    """Every answered, unjudged answer as a blind item, shuffled across arms and pairs."""
    items = []
    for p in pairs:
        for arm in ARMS:
            for k, sample in enumerate(answers.get(p["id"], {}).get(arm, [])):
                aid = answer_id(p["id"], arm, k)
                if aid in done:
                    continue
                items.append({"id": aid, "rule": p["rule"], "answer": mask(sample["text"], p["slug"])[0]})
    items.sort(key=lambda i: i["id"])
    random.Random(seed).shuffle(items)
    return items


def judge_prompt(batch: Sequence[Dict[str, str]]) -> str:
    return "\n\n".join("## Item %d\nRULE:\n%s\n\nANSWER:\n%s" % (n, it["rule"], it["answer"])
                       for n, it in enumerate(batch, 1))


def parse_verdicts(text: str, batch: Sequence[Dict[str, str]]) -> Dict[str, str]:
    """Answer id → verdict for every item the judge answered legibly; the rest stay pending."""
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError:
        return {}
    out = {}
    for n, it in enumerate(batch, 1):
        v = str(obj.get(str(n), "")).strip().lower().rstrip(".")
        v = {"n/a": NA, "not applicable": NA}.get(v, v)
        if v in (YES, NO, NA):
            out[it["id"]] = v
    return out


# --- the numbers ----------------------------------------------------------------

def pair_rows(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
              verdicts: Dict[str, str]) -> List[Dict[str, Any]]:
    """Per fully judged pair: each arm's follow rate over its samples, and whether
    every verdict in both arms was ``na``."""
    rows = []
    for p in pairs:
        per: Dict[str, List[str]] = {}
        for arm in ARMS:
            n = len(answers.get(p["id"], {}).get(arm, []))
            per[arm] = [verdicts.get(answer_id(p["id"], arm, k)) for k in range(n)]
        if not all(per[a] and all(per[a]) for a in ARMS):
            continue
        rows.append({
            "id": p["id"], "slug": p["slug"],
            "A": sum(v == YES for v in per["A"]) / len(per["A"]),
            "B": sum(v == YES for v in per["B"]) / len(per["B"]),
            "both_na": all(v == NA for a in ARMS for v in per[a]),
        })
    return rows


def lift(rows: Sequence[Dict[str, Any]], *, n_boot: int = BOOTSTRAP,
         seed: int = SEED) -> Dict[str, Any]:
    """Paired B − A over the rows that count, with a percentile bootstrap 95% CI."""
    kept = [r for r in rows if not r["both_na"]]
    n = len(kept)
    if not n:
        return {"n": 0, "excluded_na": len(rows)}
    diffs = [r["B"] - r["A"] for r in kept]
    rng = random.Random(seed)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return {
        "n": n, "excluded_na": len(rows) - n,
        "lift": sum(diffs) / n,
        "ci": (boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]),
        "follow_a": sum(r["A"] for r in kept) / n,
        "follow_b": sum(r["B"] for r in kept) / n,
        "redundant": sum(r["A"] > 0 for r in kept) / n,
        "gained": sum(r["B"] > r["A"] for r in kept),
        "lost": sum(r["B"] < r["A"] for r in kept),
    }


def decision(stats: Dict[str, Any]) -> str:
    """Which branch of the pre-registered rule the numbers fall on."""
    if not stats.get("n"):
        return "no judged pairs yet"
    lo, hi = stats["ci"]
    if lo <= 0 <= hi:
        return ("CI includes 0: stop tuning recall/reflex and invest in the briefing, "
                "the channel that measurably carries")
    if lo > 0 and stats["lift"] >= MIN_LIFT:
        return ("CI excludes 0 and lift >= %d pp: rules carry; per-rule lift is a "
                "pruning signal" % round(MIN_LIFT * 100))
    return "neither branch: CI excludes 0 but %s" % (
        "the lift is under %d pp" % round(MIN_LIFT * 100) if lo > 0 else "the lift is negative")


def per_rule(rows: Sequence[Dict[str, Any]]) -> List[Tuple[str, int, float]]:
    """(slug, pairs, mean lift) for every rule, lowest lift first — the pruning list."""
    by: Dict[str, List[float]] = {}
    for r in rows:
        if not r["both_na"]:
            by.setdefault(r["slug"], []).append(r["B"] - r["A"])
    return sorted(((s, len(d), sum(d) / len(d)) for s, d in by.items()),
                  key=lambda t: (t[2], t[0]))


def estimate(pairs: Sequence[Dict[str, Any]], samples: int) -> Dict[str, Any]:
    """Calls, tokens and API-price equivalent of a full run, from text lengths alone."""
    def tok(s: str) -> int:
        return len(s) // 4 + 1

    arm_in = sum(tok(ARM_SYSTEM) + CLI_OVERHEAD_TOKENS + tok(arm_prompt(p, a))
                 for p in pairs for a in ARMS) * samples
    arm_calls = len(pairs) * len(ARMS) * samples
    arm_out = arm_calls * ANSWER_TOKENS
    answers = arm_calls
    judge_calls = -(-answers // JUDGE_CHUNK)
    per_answer = sum(tok(p["rule"]) for p in pairs) / max(len(pairs), 1) + ANSWER_TOKENS
    judge_in = int(judge_calls * (tok(JUDGE_SYSTEM) + CLI_OVERHEAD_TOKENS)
                   + answers * per_answer)
    judge_out = judge_calls * JUDGE_OUT_TOKENS
    usd = ((arm_in + judge_in) * USD_IN_PER_MTOK + (arm_out + judge_out) * USD_OUT_PER_MTOK) / 1e6
    return {"pairs": len(pairs), "prompts": len({p["uid"] for p in pairs}),
            "rules": len({p["slug"] for p in pairs}),
            "no_context": sum(not p["context"] for p in pairs),
            "arm_calls": arm_calls, "judge_calls": judge_calls,
            "input_tokens": arm_in + judge_in, "output_tokens": arm_out + judge_out,
            "usd": usd}


def report_lines(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
                 verdicts: Dict[str, str]) -> List[str]:
    rows = pair_rows(pairs, answers, verdicts)
    stats = lift(rows)
    lines = ["pairs %d, fully judged %d, excluded (na in both arms) %d"
             % (len(pairs), len(rows), stats.get("excluded_na", 0))]
    if stats.get("n"):
        lo, hi = stats["ci"]
        lines += [
            "follow rate: A %.1f%%, B %.1f%% over %d pairs"
            % (100 * stats["follow_a"], 100 * stats["follow_b"], stats["n"]),
            "lift B - A: %+.1f pp, 95%% CI [%+.1f, %+.1f] (paired bootstrap, %d resamples)"
            % (100 * stats["lift"], 100 * lo, 100 * hi, BOOTSTRAP),
            "pairs B gained / lost: %d / %d" % (stats["gained"], stats["lost"]),
            "redundant (A already follows): %.1f%%" % (100 * stats["redundant"]),
        ]
    lines.append("decision: " + decision(stats))
    masked = {a: sum(mask(s["text"], p["slug"])[1] for p in pairs
                     for s in answers.get(p["id"], {}).get(a, [])) for a in ARMS}
    lines.append("answers masked before judging: A %d, B %d" % (masked["A"], masked["B"]))
    usd = sum(s.get("usd") or 0.0 for a in answers.values() for ss in a.values() for s in ss)
    lines.append("arm calls so far: %d, API-price equivalent $%.2f (subscription usage, not money spent)"
                 % (sum(len(ss) for a in answers.values() for ss in a.values()), usd))
    rules = per_rule(rows)
    if rules:
        lines += ["", "per rule (pairs, mean lift), lowest first:"]
        lines += ["  %+5.0f pp  %d  %s" % (100 * d, n, s) for s, n, d in rules]
    return lines


# --- the vault ------------------------------------------------------------------

def column(model: str, system: str) -> str:
    """Where one model-and-prompt's results are filed."""
    return "%s@%s" % (model, hashlib.sha256(system.encode("utf-8")).hexdigest()[:8])


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8")


def _build(vault: Path, rater: str) -> List[Dict[str, Any]]:
    from mnemo.core.mcp.recall_sessions import _transcripts_by_session
    from mnemo.core.reflex.index import load_index

    mrg = _sibling("measure_reflex_gate")
    labels = _read(vault / ".mnemo" / ("reflex-labels-%s.json" % mrg._safe_rater(rater)), None)
    if labels is None:
        raise SystemExit("error: no labels for rater %r; see tools/measure_reflex_gate.py" % rater)
    units = {u["uid"]: u for u in mrg._load_units(vault)}
    index = load_index(vault)
    if index is None:
        raise SystemExit("error: no reflex index in %s; run `mnemo index` first" % vault)
    return build_pairs(units, labels["labels"], _transcripts_by_session(Path.home() / ".claude" / "projects"),
                       index, mrg.unit_id)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="pairs, calls, tokens, cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="answer both arms and judge (model calls)")
    ap.add_argument("--samples", type=int, default=1, help="samples per arm (default 1)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N pairs")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE)
    ap.add_argument("--rater", default=DEFAULT_RATER)
    ap.add_argument("--json", action="store_true", help="the headline numbers as data")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    out = vault / ".mnemo" / OUT_DIR
    pairs_path, answers_path, verdicts_path = out / PAIRS_NAME, out / ANSWERS_NAME, out / VERDICTS_NAME
    all_answers: Dict[str, Dict[str, Dict[str, List[Any]]]] = _read(answers_path, {})
    all_verdicts: Dict[str, Dict[str, str]] = _read(verdicts_path, {})
    arm_col = column(args.model, ARM_SYSTEM)
    col = "%s/%s" % (arm_col, column(args.judge_model, JUDGE_SYSTEM))
    answers = all_answers.setdefault(arm_col, {})

    if not pairs_path.exists():
        if any(all_answers.values()):
            raise SystemExit("error: %s holds answers but %s is gone; refusing to rebuild"
                             % (answers_path, pairs_path))
        out.mkdir(parents=True, exist_ok=True)
        _write(pairs_path, _build(vault, args.rater))
    pairs = _read(pairs_path, [])
    todo = pairs[:args.limit] if args.limit else pairs

    if args.dry_run:
        e = estimate(todo, args.samples)
        print("pairs %(pairs)d over %(prompts)d prompts and %(rules)d rules "
              "(%(no_context)d without a transcript)" % e)
        print("calls: %d arm + %d judge = %d" % (e["arm_calls"], e["judge_calls"],
                                                 e["arm_calls"] + e["judge_calls"]))
        print("tokens: ~%d in, ~%d out (answers assumed %d tokens)"
              % (e["input_tokens"], e["output_tokens"], ANSWER_TOKENS))
        print("API-price equivalent: ~$%.2f at $%g/$%g per MTok — subscription usage on a "
              "Max plan, not money" % (e["usd"], USD_IN_PER_MTOK, USD_OUT_PER_MTOK))
        return 0

    if args.send:
        provider = llm.resolve(cfg)
        timeout = int(cfg["extraction"]["subprocessTimeout"])
        # A scratch cwd: no project CLAUDE.md or auto-memory in either arm.
        with tempfile.TemporaryDirectory(prefix="mnemo-rule-lift-") as scratch:
            with _chdir(scratch):
                calls = pending_calls(todo, answers, args.samples)
                for n, (p, arm) in enumerate(calls, 1):
                    resp = provider(arm_prompt(p, arm), system=ARM_SYSTEM, model=args.model,
                                    timeout=timeout)
                    answers.setdefault(p["id"], {}).setdefault(arm, []).append({
                        "text": resp.text, "usd": resp.total_cost_usd,
                        "in": resp.input_tokens, "out": resp.output_tokens})
                    _write(answers_path, all_answers)
                    print("arm %d/%d %s %s" % (n, len(calls), arm, p["id"]), file=sys.stderr)
                verdicts = all_verdicts.setdefault(col, {})
                items = judge_items(todo, answers, verdicts)
                judge_usd = 0.0
                for start in range(0, len(items), JUDGE_CHUNK):
                    batch = items[start:start + JUDGE_CHUNK]
                    resp = provider(judge_prompt(batch), system=JUDGE_SYSTEM,
                                    model=args.judge_model, timeout=timeout)
                    judge_usd += float(resp.total_cost_usd or 0.0)
                    verdicts.update(parse_verdicts(resp.text, batch))
                    _write(verdicts_path, all_verdicts)
                    print("judge %d/%d" % (start // JUDGE_CHUNK + 1, -(-len(items) // JUDGE_CHUNK)),
                          file=sys.stderr)
                if items:
                    print("judge calls: API-price equivalent $%.2f" % judge_usd)

    verdicts = all_verdicts.get(col, {})
    if args.json:
        rows = pair_rows(todo, answers, verdicts)
        stats = lift(rows)
        print(json.dumps({"judge": col, "stats": stats, "decision": decision(stats),
                          "per_rule": per_rule(rows)}, indent=1))
        return 0
    print("arms and judge %s, %s\n" % (col, pairs_path))
    for line in report_lines(todo, answers, verdicts):
        print(line)
    return 0


@contextlib.contextmanager
def _chdir(path: str):
    import os

    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


if __name__ == "__main__":
    sys.exit(main())
