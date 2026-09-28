"""Does the right briefing help a session pick up its work, and does the wrong one hurt? (#540)

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_value.py              # build the arms, pending, cost; report
    PYTHONPATH=src python3 tools/measure_briefing_value.py --send       # answer the arms, judge, report
        [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_briefing_value.py --json       # the report as data

#534 (``measure_briefing_pick``) had two blind raters list, for 227 human
sessions, which of the briefings the session could have been handed were about
its work. Both named one for 110 of them; the hook's own pick was one of those
in 40. Before spending more on *choosing*, this asks whether the choice
matters: at the session's first typed prompt, is the reply better placed with
the right briefing than with none, and is it worse with the one mnemo injected
when that one was not right?

**Units.** The sessions where #534's two raters agree a right briefing exists
(``<vault>/.mnemo/briefing-pick``: ``units.json``, ``labels.json``). The
*right* briefing is the one both raters called best; when they named different
ones, the newest of those both listed (:func:`choose_right`).

**Arms at the first typed prompt**, :data:`SAMPLES` samples each, answered on
the model the session ran on (the assistant message after that prompt), with
#434/#527's harness (``measure_rule_lift.ARM_SYSTEM``, no tools, a scratch
working directory; ``measure_broad_value.arm_prompt``). Every arm has what the
session had in context — the ``instructions`` files Claude Code loaded, the
prompt's own ``UserPromptSubmit`` context — and mnemo's SessionStart envelope
rebuilt as the hook builds it today: the session's recorded envelope (the file
Claude Code saved it to, when it was persisted) without its
``[last-briefing]`` block, the arm's briefing appended last with today's
framing line, and the whole cut to #533's cap by the hook's own
``_fit_briefing``. Only the briefing slot varies:

- ``right``: the right briefing;
- ``none``: no briefing at all;
- ``newest``: the hook's pick (#534's ``newest``, which matched the briefing
  the transcript recorded in every session where both could be read), only
  where it is not one both raters listed.

**Grounded judge.** Two blind raters (:data:`RATERS`) compare ``right`` with
``none`` and ``newest`` with ``none``, the ``k``-th sample with the ``k``-th.
They never see a briefing. They see the first prompt, the two replies
(which comes first seeded per comparison and flipped for the second rater),
and what the session did next: the developer's later typed messages and the
agent's last message, #534's own view (:data:`JUDGE_SYSTEM`). They say which
reply is more consistent with where the work went, or tie, and mark each reply
for a wrong assumption about the state of the work. A briefing's framing line
and session id are masked in the replies. Primary reading: both raters agree;
a disagreement is a tie, and a reply is *wrong* when both mark it.

**Metrics,** bootstrap CIs over sessions (one unit per session):
``h_right = P(right better) − P(none better)``, ``h_wrong = P(newest better) −
P(none better)``, the rate of wrong-state replies per arm (``none``'s read in
the same comparisons as the arm it is set against), and the raters' kappa on
the three-way question, next to #527's 0.24 on "which reply is better".

**The bar, set by the maintainer before measuring** (:func:`verdicts`):
*a right briefing helps* — ``h_right`` >= +0.15 and CI lower bound > 0; *does
not measurably help* — CI upper bound of ``h_right`` < +0.15; *a wrong briefing
hurts* — CI upper bound of ``h_wrong`` < 0; *ruler too weak* — kappa < 0.40:
say so and read no verdict from it.

**State on 2026-09-28, the maintainer's machine: no verdict yet.** 110 units,
all placed (the right briefing is the raters' agreed best in 106); a newest arm
in 70; the right briefing is cut to #533's cap in 26. 489 of the 556 answers
are cached; the run stopped when the ``claude`` CLI lost its login, before the
first of ~630 judge calls. ``--send`` resumes it.

Only ``--send`` calls a model. Calls are paced and threaded
(``measure_prevented_repeats.Sender``) from a scratch cwd, and every answer is
cached under ``--out`` (default ``<vault>/.mnemo/briefing-value``) the moment
it arrives, so a rerun resumes. A unit's arms are frozen in ``arms.json`` once
built.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bp = _sibling("measure_briefing_pick")
bv = _sibling("measure_broad_value")
mpt = _sibling("measure_persist_threshold")
mja = bp.mja
mpr = bv.mpr
mrc = bv.mrc
rl = bv.rl

RATERS = bp.RATERS
SEED = 540
BOOTSTRAP = 4000
SAMPLES = 2
OUT_DIR = "briefing-value"
PICK_DIR = bp.OUT_DIR
FALLBACK_MODEL = bv.FALLBACK_MODEL
PAUSE_SECONDS = 2.0
WORKERS = 3
JUDGE_TOKENS = 120
#: The first typed prompt is looked for among this many turns.
MAX_TURN = 8

RIGHT, NONE, NEWEST, TIE = "right", "none", "newest", "tie"
ARMS = (RIGHT, NONE, NEWEST)
#: What each treatment arm is compared with.
PAIRS = ((RIGHT, NONE), (NEWEST, NONE))
BOTH = "both"

#: The maintainer's bar, set before measuring.
HELP_BAR = 0.15
KAPPA_BAR = 0.40
#: #527's kappa on "which reply is better", for comparison.
KAPPA_527 = 0.24

JUDGE_SYSTEM = """\
You are shown the first message a developer typed in an AI coding session, two
replies an agent could have sent to it, and what the session actually went on
to do: the developer's later messages, in order, and the agent's last message.

Which reply is more consistent with where the work actually went: it takes the
right current state of the work, the right next step, and makes no wrong
assumption about what was already done or decided? If neither is, or both are
equally, say tie. Length and polish are not merit on their own. Some text may
be masked as [...]; ignore that.

For each reply, also say whether it states a wrong assumption about the state
of the work: something it takes as already done, decided, broken or pending
that what the session went on to do shows to be false. A reply that only asks,
or proposes without assuming, does not.

Reply with JSON only:
{"better": "1" | "2" | "tie", "wrong_state": {"1": true | false, "2": true | false}, "why": "<one sentence>"}
"""

_BRIEF_OPEN = "\n\n[last-briefing"
_BRIEF_CLOSE = "[/last-briefing]"
_FRAMING = re.compile(r"\[/?last-briefing[^\]]*\]")
MASK = "[...]"


# --- the unit ----------------------------------------------------------------------------

def choose_right(unit: Dict[str, Any], labels: Dict[str, Dict[str, Any]],
                 raters: Sequence[str] = RATERS) -> Optional[Tuple[str, str]]:
    """``(candidate id, how)`` of the right briefing, or None when the raters
    do not agree one exists: the best both named, else the newest both listed."""
    right = bp.right_set(unit["id"], labels, raters)
    if not right:
        return None
    bests = {(labels[r][unit["id"]] or {}).get("best") for r in raters}
    if len(bests) == 1:
        best = next(iter(bests))
        if best in right:
            return best, "agreed best"
    newest = min((c for c in unit["candidates"] if c["id"] in right), key=lambda c: c["rank"])
    return newest["id"], "newest listed"


def newest_of(unit: Dict[str, Any], right: Set[str]) -> Optional[str]:
    """The hook's pick when it is not one the raters listed, else None."""
    pick = bp.pick_newest(unit)
    return pick if pick is not None and pick not in right else None


def first_context(events: List[dict]) -> Optional[Dict[str, Any]]:
    """``measure_broad_value.context_at`` at the first prompt the developer typed."""
    for i in range(MAX_TURN):
        ctx = bv.context_at(events, i)
        if ctx is None:
            return None
        if ctx["text"].strip() and not mja._NOT_TYPED.search(ctx["text"]):
            return ctx
    return None


def envelope_heads(session_start: Sequence[str], read_file: Any = None) -> Tuple[List[str], int, str]:
    """(the recorded envelopes without their ``[last-briefing]`` block, the
    index of the one that carried it, where they were read).

    A persisted envelope is read from the file Claude Code saved it to; when
    that is gone, from the preview (``"preview"``). A session whose hook fired
    twice (two installs) keeps both texts, as it had them; the briefing goes
    back into the one that carried it, else the last.
    """
    heads, host, how = [], -1, "inline"
    for text in session_start:
        if mpt.PERSISTED in text:
            got = mpt.measure_text(text, read_file)
            if got["how"] == "saved":
                text, how = got["text"], "saved"
            else:
                text = re.sub(r"</?persisted-output>|Output too large[^\n]*\n|Preview \(first [^)]*\):\n", "", text)
                text, how = text.strip().rstrip(".").rstrip(), "preview"
        if "[last-briefing" in text and host < 0:
            host = len(heads)
        heads.append(strip_briefing(text))
    return heads, (host if host >= 0 else len(heads) - 1), how


def strip_briefing(text: str) -> str:
    """The envelope without its ``[last-briefing]`` block, wherever it sat."""
    i = text.find(_BRIEF_OPEN)
    if i < 0:
        i = 0 if text.startswith("[last-briefing") else -1
    if i < 0:
        return text
    j = text.find(_BRIEF_CLOSE, i)
    rest = text[j + len(_BRIEF_CLOSE):] if j >= 0 else ""
    return (text[:i].rstrip() + rest).strip()


def framing(meta: Dict[str, Any], sid: str) -> str:
    """The hook's ``[last-briefing …]`` line for a briefing's frontmatter."""
    return "[last-briefing session=%s date=%s duration_minutes=%s]" % (
        meta.get("session_id") or sid, meta.get("date") or "", meta.get("duration_minutes") or "0")


def envelope(head: str, body: Optional[str], meta: Dict[str, Any], sid: str, path: str) -> Tuple[str, bool]:
    """(the SessionStart envelope with ``body`` as its briefing, whether the
    briefing was trimmed): the hook's own assembly and #533's cap."""
    from mnemo.hooks import session_start as ss

    if not body:
        return head, False
    block = "\n\n" + framing(meta, sid) + "\n" + body.rstrip() + "\n" + _BRIEF_CLOSE
    room = ss.ENVELOPE_MAX_BYTES - ss._utf8_len(head)
    fitted = ss._fit_briefing(block, room, path)
    text = head + fitted if head else fitted.lstrip("\n")
    return text, fitted != block


def later_work(unit: Dict[str, Any]) -> Dict[str, Any]:
    """What the session did after its first prompt, in #534's view."""
    first = mpr._head(mja._IMAGE.sub("[image]", unit.get("first_prompt") or "").strip(), mja.BRIEFING_PROMPT_CHARS)
    prompts = list(unit.get("prompts") or [])
    if prompts and prompts[0] == first:
        prompts = prompts[1:]
    return {"prompts": prompts, "more_prompts": unit.get("more_prompts") or 0,
            "short_replies": unit.get("short_replies") or 0, "final": unit.get("final") or ""}


def build_arms(unit: Dict[str, Any], ctx: Dict[str, Any], right: str, how: str, newest: Optional[str],
               metas: Dict[str, Dict[str, Any]], read_file: Any = None) -> Dict[str, Any]:
    """The unit's arm prompts and what the judge needs. ``metas``: candidate id
    -> ``{"meta": frontmatter, "path": file}``."""
    bodies = {c["id"]: c["body"] for c in unit["candidates"]}
    heads, host, env_from = envelope_heads(ctx["session_start"], read_file)
    native = mpr._head("\n\n".join("Contents of %s:\n\n%s" % (p, t) for p, t in ctx["native"]), bv.NATIVE_CHARS)
    now = [t for owner, t in ctx["reflex"] if owner == ctx["i"]]
    previous = rl.tail(ctx["answered"])
    out: Dict[str, Any] = {
        "id": unit["id"], "project": unit.get("project", ""), "i": ctx["i"],
        "model": ctx["model"] or FALLBACK_MODEL, "model_from": "transcript" if ctx["model"] else "fallback",
        "prompt": ctx["text"], "previous": previous, "envelope_from": env_from,
        "briefing": {RIGHT: right, NEWEST: newest}, "right_from": how,
        "work": later_work(unit), "trimmed": {}, "prompts": {}}
    for arm, cid in ((RIGHT, right), (NONE, None), (NEWEST, newest)):
        if arm == NEWEST and cid is None:
            continue
        m = metas.get(cid or "", {})
        envs = list(heads) or [""]
        envs[max(host, 0)], trimmed = envelope(envs[max(host, 0)], bodies.get(cid) if cid else None,
                                              m.get("meta") or {}, cid or "", m.get("path") or "")
        out["trimmed"][arm] = trimmed
        out["prompts"][arm] = bv.arm_prompt(ctx["text"], previous, native,
                                            {"ss": [e for e in envs if e], "earlier": [], "mcp": [], "now": now})
    return out


# --- the judge ---------------------------------------------------------------------------

def comparison_id(uid: str, arm: str, k: int) -> str:
    return "%s|%s|%d" % (uid, arm, k)


def treatment_first(cid: str, rater_index: int) -> bool:
    """Whether reply 1 is the treatment arm: seeded per comparison, flipped for the second rater."""
    first = int(hashlib.md5(("%d:%s" % (SEED, cid)).encode()).hexdigest(), 16) % 2 == 0
    return first if rater_index % 2 == 0 else not first


def mask(text: str, ids: Sequence[str]) -> str:
    """A reply without a briefing's framing line or session id, whichever arm wrote it."""
    text = _FRAMING.sub(MASK, text)
    for i in ids:
        if i:
            text = text.replace(i, MASK)
    return text


def work_text(work: Dict[str, Any]) -> str:
    lines = ["Developer's later messages, in order:"]
    if not work["prompts"]:
        lines.append("(none)")
    for n, t in enumerate(work["prompts"], 2):
        lines.append("%d. %s" % (n, t))
    if work.get("more_prompts"):
        lines.append("(%d more messages)" % work["more_prompts"])
    if work.get("short_replies"):
        lines.append("(and %d short replies such as \"ok\" left out)" % work["short_replies"])
    lines += ["", "Agent's last message in the session:", work.get("final") or "(none)"]
    return "\n".join(lines)


def judge_prompt(arms: Dict[str, Any], replies: Tuple[str, str]) -> str:
    return "\n\n".join([
        "## The developer's first message", arms["prompt"],
        "## Reply 1", replies[0], "## Reply 2", replies[1],
        "## What the session actually went on to do", work_text(arms["work"])])


def comparison_prompt(arms: Dict[str, Any], answers: Dict[str, List[Dict[str, Any]]], arm: str, k: int,
                      rater_index: int) -> Optional[str]:
    if arm not in arms["prompts"] or any(len(answers.get(a, [])) <= k for a in (arm, NONE)):
        return None
    ids = [x for x in arms["briefing"].values() if x]
    t = mask(answers[arm][k]["text"], ids)
    c = mask(answers[NONE][k]["text"], ids)
    pair = (t, c) if treatment_first(comparison_id(arms["id"], arm, k), rater_index) else (c, t)
    return judge_prompt(arms, pair)


def _bool(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    return True if s in ("true", "yes") else False if s in ("false", "no") else None


def parse_judge(text: str, arm: str, cid: str, rater_index: int) -> Optional[Dict[str, Any]]:
    """``{"better": arm|none|tie, "wrong": {arm: bool, "none": bool}, "why"}``, or None."""
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    said = str(payload.get("better") or "").strip().lower().strip('"').rstrip(".")
    said = {"reply 1": "1", "reply 2": "2", "neither": TIE}.get(said, said)
    ws = payload.get("wrong_state")
    if said not in ("1", "2", TIE) or not isinstance(ws, dict):
        return None
    w1, w2 = _bool(ws.get("1")), _bool(ws.get("2"))
    if w1 is None or w2 is None:
        return None
    one = treatment_first(cid, rater_index)
    if said == TIE:
        better = TIE
    else:
        better = arm if (said == "1") == one else NONE
    wrong = {arm: w1 if one else w2, NONE: w2 if one else w1}
    return {"better": better, "wrong": wrong, "why": str(payload.get("why") or "")[:400]}


# --- the numbers -------------------------------------------------------------------------

def agreed(votes: Sequence[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Both raters' reading: the reply both named, else a tie; a reply is wrong
    when both mark it. None while one is missing."""
    if not votes or any(v is None for v in votes):
        return None
    better = votes[0]["better"] if all(v["better"] == votes[0]["better"] for v in votes) else TIE
    wrong = {a: all(v["wrong"][a] for v in votes) for a in votes[0]["wrong"]}
    return {"better": better, "wrong": wrong}


def unit_scores(verdicts: Dict[str, Dict[str, Dict[str, Any]]], uid: str, arm: str, raters: Sequence[str],
                samples: int = SAMPLES) -> Optional[Dict[str, float]]:
    """Over the unit's comparisons of ``arm`` with none: ``h``, the share each
    way, and each arm's wrong-state rate. None until every one is judged."""
    got = {"h": 0.0, "arm": 0.0, "none": 0.0, "tie": 0.0, "wrong_arm": 0.0, "wrong_none": 0.0}
    for k in range(samples):
        v = agreed([verdicts.get(r, {}).get(comparison_id(uid, arm, k)) for r in raters])
        if v is None:
            return None
        key = {arm: "arm", NONE: "none", TIE: "tie"}[v["better"]]
        got[key] += 1.0 / samples
        got["wrong_arm"] += float(v["wrong"][arm]) / samples
        got["wrong_none"] += float(v["wrong"][NONE]) / samples
    got["h"] = got["arm"] - got["none"]
    return got


def _pct(values: List[float], q: float) -> float:
    return values[min(len(values) - 1, max(0, int(q * len(values))))]


def bootstrap(rows: Sequence[Dict[str, float]], keys: Sequence[str], n_boot: int = BOOTSTRAP,
              seed: int = SEED) -> Dict[str, Any]:
    """Mean of each key over units (one per session), with a bootstrap CI over them."""
    n = len(rows)
    if not n:
        return {"n": 0}
    rng = random.Random(seed)
    boots: Dict[str, List[float]] = {k: [] for k in keys}
    for _ in range(n_boot):
        pick = [rows[rng.randrange(n)] for _ in range(n)]
        for k in keys:
            boots[k].append(sum(r[k] for r in pick) / n)
    out: Dict[str, Any] = {"n": n}
    for k in keys:
        s = sorted(boots[k])
        out[k] = sum(r[k] for r in rows) / n
        out[k + "_ci"] = [_pct(s, 0.025), _pct(s, 0.975)]
    return out


KEYS = ("h", "arm", "none", "tie", "wrong_arm", "wrong_none", "wrong_diff")


def arm_stats(verdicts: Dict[str, Dict[str, Dict[str, Any]]], uids: Sequence[str], arm: str,
              raters: Sequence[str]) -> Dict[str, Any]:
    rows = []
    for uid in uids:
        s = unit_scores(verdicts, uid, arm, raters)
        if s is not None:
            rows.append(dict(s, wrong_diff=s["wrong_arm"] - s["wrong_none"]))
    return bootstrap(rows, KEYS)


def kappa(verdicts: Dict[str, Dict[str, Dict[str, Any]]], cids: Sequence[str],
          raters: Sequence[str]) -> Dict[str, Any]:
    """The two raters' agreement on the three-way question and on the wrong-state marks."""
    a, b, wa, wb = [], [], [], []
    for cid in cids:
        va, vb = (verdicts.get(r, {}).get(cid) for r in raters)
        if not va or not vb:
            continue
        arm = cid.split("|")[1]
        a.append(va["better"] if va["better"] == TIE else ("treatment" if va["better"] == arm else NONE))
        b.append(vb["better"] if vb["better"] == TIE else ("treatment" if vb["better"] == arm else NONE))
        for x in (arm, NONE):
            wa.append(str(va["wrong"][x]))
            wb.append(str(vb["wrong"][x]))
    return {"n": len(a), "same": sum(x == y for x, y in zip(a, b)), "kappa": bv._kappa3(a, b),
            "wrong_n": len(wa), "wrong_kappa": bv._kappa3(wa, wb)}


def verdicts(right: Dict[str, Any], wrong: Dict[str, Any], k: Optional[float]) -> Dict[str, str]:
    """The pre-set bar. A kappa under :data:`KAPPA_BAR` reads nothing."""
    if k is None or not right.get("n"):
        return {"ruler": "no estimate yet", "right": "no estimate yet", "wrong": "no estimate yet"}
    if k < KAPPA_BAR:
        return {"ruler": "too weak", "right": "no verdict (ruler too weak)", "wrong": "no verdict (ruler too weak)"}
    lo, hi = right["h_ci"]
    if right["h"] >= HELP_BAR and lo > 0:
        rv = "a right briefing helps"
    elif hi < HELP_BAR:
        rv = "a right briefing does not measurably help"
    else:
        rv = "inconclusive"
    if not wrong.get("n"):
        wv = "no estimate yet"
    elif wrong["h_ci"][1] < 0:
        wv = "a wrong briefing hurts"
    else:
        wv = "not shown to hurt"
    return {"ruler": "ok", "right": rv, "wrong": wv}


# --- the vault -----------------------------------------------------------------------------

def briefing_metas(vault: Path) -> Dict[str, Dict[str, Any]]:
    """``session id -> {"meta": frontmatter, "path": file}`` for every briefing."""
    from mnemo.core.extract.scanner import parse_frontmatter

    out: Dict[str, Dict[str, Any]] = {}
    for md in sorted((vault / "bots").glob("*/briefings/sessions/*.md")):
        try:
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        out[str(fm.get("session_id") or md.stem)] = {"meta": {k: str(v) for k, v in fm.items()}, "path": str(md)}
    return out


# --- report --------------------------------------------------------------------------------

def _ci(st: Dict[str, Any], key: str, pct: bool = False) -> str:
    if not st.get("n"):
        return "n/a"
    f = (lambda x: "%.1f%%" % (100 * x)) if pct else (lambda x: "%+.3f" % x)
    return "%s [%s, %s]" % (f(st[key]), f(st[key + "_ci"][0]), f(st[key + "_ci"][1]))


def report_lines(data: Dict[str, Any]) -> List[str]:
    c = data["counts"]
    lines = ["%d sessions with a right briefing (#534, both raters); %d placed, %d frozen; newest arm in %d "
             "(the hook's pick was right in %d)" % (c["units"], c["placed"], c["frozen"], c["newest"],
                                                   c["newest_right"]),
             "right briefing chosen by: " + ", ".join("%s %d" % kv for kv in sorted(data["right_from"].items())),
             "envelope read: " + ", ".join("%s %d" % kv for kv in sorted(data["envelope_from"].items()))
             + "; briefing trimmed to #533's cap: " + ", ".join(
                 "%s %d" % kv for kv in sorted(data["trimmed"].items())),
             "answer models: " + ", ".join("%s %d" % kv for kv in sorted(data["models"].items()))]
    for col, res in data["results"].items():
        lines += ["", "%s%s:" % (col, "  <- the verdict" if col == BOTH else "")]
        for arm, name in ((RIGHT, "h_right"), (NEWEST, "h_wrong")):
            st = res[arm]
            if not st.get("n"):
                lines.append("  %-8s no estimate yet" % name)
                continue
            lines.append("  %-8s %s over %d sessions (%s better %.1f%%, none better %.1f%%, tie %.1f%%)"
                         % (name, _ci(st, "h"), st["n"], arm, 100 * st["arm"], 100 * st["none"], 100 * st["tie"]))
            lines.append("  %-8s wrong-state replies: %s %s, none %s; difference %s"
                         % ("", arm, _ci(st, "wrong_arm", True), _ci(st, "wrong_none", True),
                            _ci(st, "wrong_diff")))
    k = data["kappa"]
    if k.get("n"):
        lines += ["", "rater agreement: 'which reply' %d of %d the same, kappa %s (#527's ruler: %.2f); "
                  "wrong-state marks kappa %s over %d"
                  % (k["same"], k["n"], "n/a" if k["kappa"] is None else "%.2f" % k["kappa"], KAPPA_527,
                     "n/a" if k["wrong_kappa"] is None else "%.2f" % k["wrong_kappa"], k["wrong_n"])]
    for title, groups in (data.get("breakdowns") or {}).items():
        lines += ["", "h_right by %s (both raters):" % title]
        for name, st in groups.items():
            lines.append("  %-22s %s over %d" % (name, _ci(st, "h"), st.get("n", 0)))
    v = data["verdicts"]
    lines += ["", "BAR (set before measuring): right helps if h_right >= +%.2f and CI lower > 0; does not "
              "measurably help if CI upper < +%.2f; wrong hurts if CI upper of h_wrong < 0; kappa < %.2f reads "
              "nothing" % (HELP_BAR, HELP_BAR, KAPPA_BAR),
              "  ruler: %s" % v["ruler"], "  right briefing: %s" % v["right"], "  wrong briefing: %s" % v["wrong"]]
    cost = data.get("cost") or {}
    if cost:
        lines += ["", "notional cost of every call on file: " + ", ".join(
            "%s $%.2f (%d calls)" % (m, x["usd"], x["calls"]) for m, x in sorted(cost.items()))
            + " — subscription usage, not money"]
    return lines


# --- driver --------------------------------------------------------------------------------

def run(argv: Optional[List[str]] = None, provider: Any = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.briefing import _load_jsonl_events

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--pick", default="", help="#534's cache (default <vault>/.mnemo/briefing-pick)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/briefing-value)")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = list(RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    pick = Path(args.pick).expanduser() if args.pick else vault / ".mnemo" / PICK_DIR
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    built = bp._read(pick / "units.json", {})
    if not built.get("units"):
        raise SystemExit("error: no %s; run tools/measure_briefing_pick.py first" % (pick / "units.json"))
    all_labels = bp._read(pick / "labels.json", {})
    labels = {r: all_labels.get(mrc.column(r, bp.SYSTEM), {}) for r in raters}

    units = []
    for u in built["units"]:
        got = choose_right(u, labels, raters)
        if got is not None:
            right = bp.right_set(u["id"], labels, raters) or set()
            units.append((u, got[0], got[1], newest_of(u, right)))

    arms_file = bp._read(out / "arms.json", {})
    transcripts = None
    metas = None
    for u, right, how, newest in units:
        if u["id"] in arms_file:
            continue
        if transcripts is None:
            transcripts = {p.stem: p for p in Path(args.projects).expanduser().glob("*/*.jsonl")}
            metas = briefing_metas(vault)
        path = transcripts.get(u["id"])
        ctx = first_context(_load_jsonl_events(path)) if path else None
        if ctx is not None:
            arms_file[u["id"]] = build_arms(u, ctx, right, how, newest, metas or {})
    bp._write(out / "arms.json", arms_file)

    all_answers = bp._read(out / "answers.json", {})
    answers = all_answers.setdefault(mrc.column("session-model", rl.ARM_SYSTEM), {})
    all_verdicts = bp._read(out / "verdicts.json", {})
    verdicts_ = {r: all_verdicts.setdefault(mrc.column(r, JUDGE_SYSTEM), {}) for r in raters}

    def answer_todo() -> List[Tuple[Dict[str, Any], str]]:
        todo = []
        for k in range(SAMPLES):
            for uid in sorted(arms_file):
                a = arms_file[uid]
                for arm in ARMS:
                    if arm in a["prompts"] and len(answers.get(uid, {}).get(arm, [])) <= k:
                        todo.append((a, arm))
        return todo

    def judge_todo(r: str) -> List[Tuple[Dict[str, Any], str, int, str]]:
        ri = raters.index(r)
        todo = []
        for uid in sorted(arms_file):
            a = arms_file[uid]
            for arm, _ in PAIRS:
                for k in range(SAMPLES):
                    if comparison_id(uid, arm, k) in verdicts_[r]:
                        continue
                    p = comparison_prompt(a, answers.get(uid, {}), arm, k, ri)
                    if p is not None:
                        todo.append((a, arm, k, p))
        return todo

    sender = None
    if args.send:
        if provider is None:
            provider = llm.resolve(cfg)
        timeout = max(int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180), 600)
        sender = mpr.Sender(provider, timeout, out / "calls.jsonl", args.workers, args.pause)
        here = os.getcwd()
        scratch = tempfile.mkdtemp(prefix="mnemo-briefing-value-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches an arm or a rater
        try:
            def on_answer(a: Dict[str, Any], arm: str) -> Callable[[str], None]:
                def take(text: str) -> None:
                    if text.strip():
                        answers.setdefault(a["id"], {}).setdefault(arm, []).append({"text": text, "model": a["model"]})
                        bp._write(out / "answers.json", all_answers)
                return take
            todo = answer_todo()
            todo = todo[:args.limit] if args.limit else todo
            sender.run([(a["model"], a["prompts"][arm], rl.ARM_SYSTEM, on_answer(a, arm)) for a, arm in todo])

            def on_judge(r: str, a: Dict[str, Any], arm: str, k: int) -> Callable[[str], None]:
                cid = comparison_id(a["id"], arm, k)

                def take(text: str) -> None:
                    got = parse_judge(text, arm, cid, raters.index(r))
                    if got is not None:
                        verdicts_[r][cid] = got
                        bp._write(out / "verdicts.json", all_verdicts)
                return take
            if not sender.stopped:
                calls = [(r, p, JUDGE_SYSTEM, on_judge(r, a, arm, k)) for r in raters for a, arm, k, p in judge_todo(r)]
                sender.run(calls[:args.limit] if args.limit else calls)
        finally:
            os.chdir(here)
            shutil.rmtree(scratch, ignore_errors=True)

    # the numbers
    uids = sorted(arms_file)
    columns = [BOTH] + raters
    results = {}
    for col in columns:
        rs = raters if col == BOTH else [col]
        results[col] = {arm: arm_stats(verdicts_, [u for u in uids if arm in arms_file[u]["prompts"]], arm, rs)
                        for arm, _ in PAIRS}
    cids = [comparison_id(u, arm, k) for u in uids for arm, _ in PAIRS for k in range(SAMPLES)]
    kp = kappa(verdicts_, cids, raters)

    def count(key: Callable[[Dict[str, Any]], str]) -> Dict[str, int]:
        got: Dict[str, int] = {}
        for a in arms_file.values():
            got[key(a)] = got.get(key(a), 0) + 1
        return got

    def by(key: Callable[[Dict[str, Any]], str]) -> Dict[str, Any]:
        groups: Dict[str, List[str]] = {}
        for u in uids:
            groups.setdefault(key(arms_file[u]), []).append(u)
        return {name: arm_stats(verdicts_, g, RIGHT, raters) for name, g in sorted(groups.items())}

    trimmed: Dict[str, int] = {}
    for a in arms_file.values():
        for arm, t in a["trimmed"].items():
            trimmed[arm] = trimmed.get(arm, 0) + int(bool(t))
    data = {
        "counts": {"units": len(units), "placed": len(arms_file), "frozen": len(arms_file),
                   "newest": sum(1 for a in arms_file.values() if NEWEST in a["prompts"]),
                   "newest_right": sum(1 for _, _, _, n in units if n is None)},
        "right_from": count(lambda a: a["right_from"]), "envelope_from": count(lambda a: a["envelope_from"]),
        "trimmed": trimmed, "models": count(lambda a: "%s (%s)" % (a["model"], a["model_from"])),
        "results": results, "kappa": kp,
        "verdicts": verdicts(results[BOTH][RIGHT], results[BOTH][NEWEST], kp.get("kappa")),
        "breakdowns": {"answer model": by(lambda a: a["model"]),
                       "the hook's pick": by(lambda a: "was wrong" if a["briefing"][NEWEST] else "was right"),
                       "right briefing trimmed": by(lambda a: "trimmed" if a["trimmed"].get(RIGHT) else "whole")},
        "cost": bv.spent(out / "calls.jsonl"),
    }

    pend = answer_todo()
    by_model: Dict[str, List[Tuple[str, str]]] = {}
    for a, arm in pend:
        by_model.setdefault(a["model"], []).append((a["prompts"][arm], rl.ARM_SYSTEM))
    for m, ps in sorted(by_model.items()):
        print("%s: pending %d answer call(s); notional ~$%.2f" % (m, len(ps), bv.notional(m, ps, bv.ANSWER_TOKENS)),
              file=sys.stderr)
    for r in raters:
        jt = judge_todo(r)
        print("%s: pending %d judge call(s); notional ~$%.2f"
              % (r, len(jt), bv.notional(r, [(p, JUDGE_SYSTEM) for _, _, _, p in jt], JUDGE_TOKENS)), file=sys.stderr)
    if sender is not None:
        print("spent $%.2f notional this run (subscription usage, not money)" % sender.usd, file=sys.stderr)
    data["pending"] = {"answers": len(pend), **{r: len(judge_todo(r)) for r in raters}}
    bp._write(out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1, sort_keys=True))
    else:
        print("\n".join(report_lines(data)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
