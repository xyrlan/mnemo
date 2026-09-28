"""Check the two "better answer" raters against the maintainer, blind (#536).

Usage:
    PYTHONPATH=src python3 tools/label_better_pairs.py --export [N]   # draw N comparisons (default 30), write the file
        [--seed S] [--reply-chars C] [--pairs PATH]
    PYTHONPATH=src python3 tools/label_better_pairs.py --import       # read the filled file, report
        [--json]

**Nothing here calls a model.** It reads #527's cache
(``<vault>/.mnemo/broad-value``, ``tools/measure_broad_value.py``) and writes
two files:

- ``<vault>/rater-check/pairs.md`` — the comparisons, blind, for the
  maintainer to open in Obsidian and fill in. Not under ``.mnemo/``, where the
  issue first put it: Obsidian does not index a folder whose name starts with a
  dot, so a file there cannot be opened in it. mnemo reads nothing outside
  ``shared/``, so the folder is inert to it. ``--pairs`` moves it.
- ``<vault>/.mnemo/rater-check/key.json`` — which reply is which, the raters'
  answers, the slug, the session. Obsidian cannot show it, which is the point.

Every "is the answer better?" verdict so far rests on two model raters that
agree with each other at kappa 0.24 over #527's 276 comparisons. This checks
that ruler once against the person the answers are for.

**The draw.** A comparison is one *with* reply against one *without* reply of
one unit, as #527's raters judged it. ``--export`` draws ``N`` with a fixed
seed, stratified by the both-raters reading (both named the same reply, else a
tie): a third each where it says *with* better, *without* better and tie, the
remainder going to those strata in that order. At most one comparison per
unit, so a unit's two samples never both count.

**What the file shows** is built from :func:`blind` alone, and a test pins what
it lets through: the developer's message, the agent's previous message and the
repository's ``CLAUDE.md`` as the raters saw them, then the two replies as
*Reply 1* and *Reply 2* in a seeded random order. No rule, arm name, rater
answer, slug or session. Every field goes through #527's mask (slugs,
``[[…]]``, ``read_mnemo_rule`` become ``[...]``), which the raters' view
applied to the replies and the previous message; the prompt and ``CLAUDE.md``
are masked here too. The question is the raters' own sentence, word for word.

**Length.** Every reply over ``--reply-chars`` (default :data:`REPLY_CHARS`)
shows its head, cut at a line break when one is near, and the rest folded
under it: nothing the raters read is lost, and both replies of a comparison
get the same cap. A developer's message over ``--prompt-chars`` is folded the
same way; the previous message and ``CLAUDE.md`` are folded whole. The raters
read every reply whole, so the report splits agreement by whether an item had
a folded reply. ``--export`` prints the words left unfolded. On the
maintainer's cache on 2026-09-28 (seed 536; #527's replies run a median of
2,359 characters), the unfolded text was 27,974 words whole (``--reply-chars
99999``, ~112 minutes at 250 words a minute), 12,472 at 1,200 (~50 minutes, 29
of 30 items with a fold) and 8,754 at 800 (~36 minutes). The issue's 15
minutes for 30 comparisons would leave each reply about 60 words, too little
to judge by, so the default stops at 1,200 and the file says how long it is.
Choices live in the file, so it can be filled over several sittings.

**The bar**, set in the issue before any labelling: the raters are an adequate
ruler if the maintainer agrees with the both-raters reading on at least 70% of
the items (:data:`BAR`). ``--import`` reports that agreement and each rater's
alone, Cohen's kappa for each, and ``h`` (with better − without better) as the
maintainer judges these N, beside the raters' ``h`` on the same N. Because the
draw is stratified, ``h`` on these N is not #527's ``h``; the report also
reweights the maintainer's per-stratum ``h`` to #527's shares of the three
readings.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import measure_broad_value as mbv  # noqa: E402

WITH, WITHOUT, TIE = mbv.WITH, mbv.WITHOUT, mbv.TIE
READINGS = (WITH, WITHOUT, TIE)
SCORE = {WITH: 1, WITHOUT: -1, TIE: 0}

N_DEFAULT = 30
SEED = 536
#: Characters of a reply shown before the rest is folded; the same for both replies.
REPLY_CHARS = 1200
#: Characters of the developer's message shown before the rest is folded.
PROMPT_CHARS = 1000
#: A cut backs up to a line break when one is within this share of the cap.
CUT_SLACK = 0.25
#: Words a minute, for the reading estimate in the file header.
WORDS_PER_MINUTE = 250
#: The issue's bar, set before labelling.
BAR = 0.70

PAIRS_DIR = "rater-check"
PAIRS_NAME = "pairs.md"
KEY_NAME = "key.json"
REPORT_NAME = "report.json"

#: The raters' question (``measure_broad_value.JUDGE_SYSTEM``), word for word.
QUESTION = ("Which reply better serves this developer in this repository: more correct, "
            "more useful, a better fit for what they asked and for how this repository "
            "works? Length and polish are not merit on their own. If neither is better, "
            "say tie.")

#: What an item may show. Everything else stays in the key.
VISIBLE = ("id", "prompt", "previous", "claude_md", "replies")


# --- the comparisons #527 judged -------------------------------------------------------------

def comparisons(arms: Dict[str, Dict[str, Any]], answers: Dict[str, Dict[str, List[Dict[str, Any]]]],
                verdicts: Dict[str, Dict[str, Dict[str, str]]], raters: Sequence[str],
                samples: int = mbv.SAMPLES) -> List[Dict[str, Any]]:
    """Every comparison both raters judged, with its both-raters reading.

    ``verdicts`` is keyed by rater name. Sorted by comparison id, so a draw
    does not depend on the order the cache was written in.
    """
    out = []
    for uid in sorted(arms):
        a = arms[uid]
        for k in range(samples):
            cid = mbv.comparison_id(uid, k)
            votes = {r: (verdicts.get(r, {}).get(cid) or {}).get("better") for r in raters}
            reading = mbv.agreed([votes[r] for r in raters])
            got = answers.get(uid, {})
            if reading is None or any(len(got.get(arm, [])) <= k for arm in (WITH, WITHOUT)):
                continue
            out.append({"cid": cid, "uid": uid, "k": k, "slug": a["slug"], "session_id": a["session_id"],
                        "prompt": a["prompt"], "previous": a["previous"], "claude_md": a["claude_md"],
                        "replies": {WITH: got[WITH][k]["text"], WITHOUT: got[WITHOUT][k]["text"]},
                        "votes": votes, "reading": reading})
    return out


def quotas(n: int) -> Dict[str, int]:
    base, extra = divmod(n, len(READINGS))
    return {r: base + (1 if i < extra else 0) for i, r in enumerate(READINGS)}


def draw(comps: Sequence[Dict[str, Any]], n: int = N_DEFAULT, seed: int = SEED) -> List[Dict[str, Any]]:
    """``n`` comparisons, a third per reading, at most one per unit, shuffled,
    each with a seeded reply order (``with_is``: which reply number is *with*).
    A reading with fewer comparisons than its quota gives what it has."""
    rng = random.Random(seed)
    pools = {r: sorted((c for c in comps if c["reading"] == r), key=lambda c: c["cid"]) for r in READINGS}
    for r in READINGS:
        rng.shuffle(pools[r])
    want = quotas(n)
    used_units = set()
    picked: List[Dict[str, Any]] = []
    # round-robin, so no reading gets first claim on every shared unit
    while True:
        took = False
        for r in READINGS:
            if sum(1 for p in picked if p["reading"] == r) >= want[r]:
                continue
            while pools[r]:
                c = pools[r].pop(0)
                if c["uid"] not in used_units:
                    used_units.add(c["uid"])
                    picked.append(c)
                    took = True
                    break
        if not took:
            break
    rng.shuffle(picked)
    return [dict(c, id=i, with_is=1 if rng.random() < 0.5 else 2) for i, c in enumerate(picked, 1)]


# --- what the maintainer sees ------------------------------------------------------------------

def split(text: str, limit: int) -> Tuple[str, str]:
    """The first ``limit`` characters, backed up to a line break when one is
    near, and the rest (``""`` when nothing is left over)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, ""
    head = text[:limit]
    brk = head.rfind("\n")
    if brk >= limit * (1 - CUT_SLACK):
        head = head[:brk]
    return head.rstrip(), text[len(head):].strip()


def _masked(text: str, slug: str) -> str:
    return mbv.mask(text or "", slug)[0]


def blind(item: Dict[str, Any], reply_chars: int = REPLY_CHARS,
          prompt_chars: int = PROMPT_CHARS) -> Dict[str, Any]:
    """What an item shows: masked, the replies in the item's order. ``prompt``
    and each of ``replies`` (Reply 1, Reply 2) are ``(shown, folded)``."""
    slug = item["slug"]
    order = (WITH, WITHOUT) if item["with_is"] == 1 else (WITHOUT, WITH)
    seen = {"id": item["id"], "prompt": split(_masked(item["prompt"], slug), prompt_chars),
            "previous": _masked(item["previous"], slug), "claude_md": _masked(item["claude_md"], slug),
            "replies": [split(_masked(item["replies"][arm], slug), reply_chars) for arm in order]}
    return {k: seen[k] for k in VISIBLE}


def _open_fence(text: str) -> bool:
    return sum(1 for ln in text.split("\n") if ln.lstrip().startswith("```")) % 2 == 1


def _escape_html(text: str) -> str:
    """``<`` outside code as ``&lt;``: Obsidian renders raw HTML, and a reply
    that mentions ``<div>`` in prose would lose it."""
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        parts = line.split("`")
        out.append("`".join(p.replace("<", "&lt;") if i % 2 == 0 else p for i, p in enumerate(parts)))
    if fenced:
        out.append("```")  # a code block left open would swallow what follows
    return "\n".join(out)


def callout(kind: str, title: str, text: str, folded: bool = False) -> str:
    body = _escape_html(text.strip()) if text and text.strip() else "(none)"
    lines = ["> [!%s]%s %s" % (kind, "-" if folded else "", title)]
    lines += ["> " + ln if ln.strip() else ">" for ln in body.split("\n")]
    return "\n".join(lines)


def shown_and_folded(kind: str, title: str, part: Tuple[str, str]) -> List[str]:
    """The head in a callout, and the rest, if any, folded under it. A code
    block the cut split is reopened in the fold."""
    head, rest = part
    out = [callout(kind, title, head)]
    if rest:
        if _open_fence(head):
            rest = "```\n" + rest
        out += ["", callout(kind, "%s, the rest (%d more characters)" % (title, len(rest)), rest, folded=True)]
    return out


def words_shown(seen: Sequence[Dict[str, Any]]) -> int:
    """Words outside the folded blocks: the prompts' and the replies' heads."""
    return sum(len(s["prompt"][0].split()) + sum(len(h.split()) for h, _ in s["replies"]) for s in seen)


def has_fold(seen: Dict[str, Any]) -> bool:
    return any(rest for _, rest in seen["replies"])


def render(items: Sequence[Dict[str, Any]], reply_chars: int = REPLY_CHARS,
           prompt_chars: int = PROMPT_CHARS) -> str:
    seen = [blind(it, reply_chars, prompt_chars) for it in items]
    n_fold = sum(1 for s in seen if has_fold(s))
    words = words_shown(seen)
    head = [
        "# Which reply is better? (#536)",
        "",
        "%d moments of past sessions. At each one, two versions of the agent replied to "
        "the same message. For each item, read the developer's message and the two "
        "replies, then write `1`, `2` or `tie` after `choice:`." % len(seen),
        "",
        "The question is the one the two model raters were asked, word for word:",
        "",
        "> " + QUESTION,
        "",
        "- The folded blocks hold the agent's previous message and the repository's "
        "`CLAUDE.md`, as the raters saw them. Open them only when you need them.",
        "- Each reply shows its first %d characters, cut at a line break when one is near, "
        "the same for both replies; the rest is folded under it. %d of %d items have a "
        "folded reply. The raters read every reply whole. A message over %d characters "
        "is folded the same way." % (reply_chars, n_fold, len(seen), prompt_chars),
        "- Some text is masked as `[...]`, as it was for the raters. Ignore it.",
        "- About %d words outside the folded blocks, ~%d minutes at %d words a minute. "
        "Your choices are only in this file: stop and come back whenever you like."
        % (words, math.ceil(words / WORDS_PER_MINUTE), WORDS_PER_MINUTE),
        "- Save the file, then run `PYTHONPATH=src python3 tools/label_better_pairs.py --import`.",
    ]
    out = ["\n".join(head)]
    for s in seen:
        block = ["---", "", "## Item %d" % s["id"], ""]
        block += shown_and_folded("question", "The developer's message", s["prompt"])
        block += ["", callout("note", "The agent's previous message", s["previous"], folded=True), "",
                  callout("note", "The repository's CLAUDE.md", s["claude_md"], folded=True)]
        for n, part in enumerate(s["replies"], 1):
            block += [""] + shown_and_folded("quote", "Reply %d" % n, part)
        block += ["", "choice: "]
        out.append("\n".join(block))
    return "\n\n".join(out) + "\n"


# --- reading the file back -----------------------------------------------------------------------

_ITEM = re.compile(r"^##\s+Item\s+(\d+)\s*$")
_CHOICE = re.compile(r"^\s*choice\s*:\s*(.*?)\s*$", re.IGNORECASE)


def parse_choices(text: str) -> Dict[int, str]:
    """``{item id: what follows "choice:"}``, from lines outside the quoted
    blocks, where everything the replies carry lives."""
    out: Dict[int, str] = {}
    current: Optional[int] = None
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        m = _ITEM.match(line)
        if m:
            current = int(m.group(1))
            continue
        m = _CHOICE.match(line)
        if m and current is not None:
            out[current] = m.group(1)
    return out


def read_choice(raw: str) -> Optional[str]:
    """``"1" | "2" | "tie"``, ``""`` when blank, None when unreadable."""
    said = (raw or "").strip().strip("*`_").strip().lower().rstrip(".")
    said = re.sub(r"^reply\s+", "", said)
    if not said:
        return ""
    return said if said in ("1", "2", "tie") else None


def to_arm(choice: str, with_is: int) -> str:
    if choice == "tie":
        return TIE
    return WITH if int(choice) == with_is else WITHOUT


# --- the report -------------------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def _against(mine: Sequence[str], theirs: Sequence[str]) -> Dict[str, Any]:
    same = sum(a == b for a, b in zip(mine, theirs))
    n = len(mine)
    return {"n": n, "same": same, "share": same / n if n else None, "ci": wilson(same, n),
            "kappa": mbv._kappa3(list(mine), list(theirs)),
            "reversed": sum(1 for a, b in zip(mine, theirs) if {a, b} == {WITH, WITHOUT})}


def _h(values: Sequence[str]) -> Optional[float]:
    return sum(SCORE[v] for v in values) / len(values) if values else None


def report(key: Dict[str, Any], choices: Dict[int, str]) -> Dict[str, Any]:
    items = key["items"]
    raters = key["raters"]
    known = {it["id"] for it in items}
    invalid = sorted(i for i, raw in choices.items() if i in known and read_choice(raw) is None)
    unknown = sorted(i for i in choices if i not in known)
    done = []
    for it in items:
        c = read_choice(choices.get(it["id"], ""))
        if c:
            done.append(dict(it, maintainer=to_arm(c, it["with_is"])))
    mine = [it["maintainer"] for it in done]
    out: Dict[str, Any] = {
        "labelled": len(done), "of": len(items), "invalid": invalid, "unknown": unknown,
        "complete": len(done) == len(items) and bool(items),
        "bar": BAR,
        "maintainer_counts": {r: mine.count(r) for r in READINGS},
        "both": _against(mine, [it["reading"] for it in done]),
        "raters": {r: _against(mine, [it["votes"][r] for it in done]) for r in raters},
        "confusion": {"%s->%s" % (m, b): sum(1 for it in done if it["maintainer"] == m and it["reading"] == b)
                      for m in READINGS for b in READINGS},
        "h": {"maintainer": _h(mine), "both": _h([it["reading"] for it in done]),
              **{r: _h([it["votes"][r] for it in done]) for r in raters}},
    }
    # the draw is a third per reading; #527's comparisons are not
    pop = key.get("population") or {}
    total = sum(pop.values())
    by_stratum = {r: _h([it["maintainer"] for it in done if it["reading"] == r]) for r in READINGS}
    if total and all(by_stratum[r] is not None for r in READINGS if pop.get(r)):
        out["h_reweighted"] = sum(pop.get(r, 0) / total * (by_stratum[r] or 0.0) for r in READINGS)
    else:
        out["h_reweighted"] = None
    out["h_by_reading"] = by_stratum
    out["by_fold"] = {name: _against([it["maintainer"] for it in done if bool(it["folded"]) == flag],
                                     [it["reading"] for it in done if bool(it["folded"]) == flag])
                      for name, flag in (("no reply folded", False), ("a reply folded", True))}
    share = out["both"]["share"]
    if not out["complete"]:
        out["verdict"] = "provisional"
    else:
        out["verdict"] = "adequate" if share is not None and share >= BAR else "not adequate"
    return out


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.0f%%" % (100 * x)


def _num(x: Optional[float], fmt: str = "%+.3f") -> str:
    return "n/a" if x is None else fmt % x


def _line(name: str, a: Dict[str, Any]) -> str:
    ci = a["ci"]
    return "  %-26s %d of %d the same, %s%s   kappa %s   reversed %d" % (
        name, a["same"], a["n"], _pct(a["share"]),
        " [%s, %s]" % (_pct(ci[0]), _pct(ci[1])) if ci else "", _num(a["kappa"], "%.2f"), a["reversed"])


def format_report(r: Dict[str, Any], raters: Sequence[str]) -> List[str]:
    c = r["maintainer_counts"]
    lines = ["labelled %d of %d  (with better %d, without better %d, tie %d)"
             % (r["labelled"], r["of"], c[WITH], c[WITHOUT], c[TIE])]
    if r["invalid"]:
        lines.append("unreadable choice on item(s) %s: write 1, 2 or tie"
                     % ", ".join(str(i) for i in r["invalid"]))
    if r["unknown"]:
        lines.append("item(s) %s are not in the key: was the file drawn again?"
                     % ", ".join(str(i) for i in r["unknown"]))
    lines += ["", "the maintainer against (95% Wilson CI; reversed = opposite replies named):",
              _line("both raters' reading", r["both"])]
    lines += [_line(name, r["raters"][name]) for name in raters]
    lines += ["", "maintainer -> both raters: " + "  ".join(
        "%s %d" % (k, v) for k, v in r["confusion"].items() if v)]
    for name, a in r["by_fold"].items():
        if a["n"]:
            lines.append(_line(name, a))
    h = r["h"]
    lines += ["", "h (with better - without better) on these %d:" % r["labelled"],
              "  maintainer %s   both raters %s   %s" % (
                  _num(h["maintainer"]), _num(h["both"]),
                  "   ".join("%s %s" % (name, _num(h[name])) for name in raters)),
              "  maintainer, reweighted to #527's shares of the three readings: %s"
              % _num(r["h_reweighted"])]
    lines.append("")
    share = r["both"]["share"]
    if r["verdict"] == "provisional":
        lines.append("PROVISIONAL: %d of %d labelled; the bar is read on all of them." % (r["labelled"], r["of"]))
    elif r["verdict"] == "adequate":
        lines.append("ADEQUATE: the maintainer agrees with the both-raters reading on %s of the items "
                     "(bar: >= %s, set before labelling)." % (_pct(share), _pct(BAR)))
    else:
        lines.append("NOT ADEQUATE: the maintainer agrees with the both-raters reading on %s of the items, "
                     "under the %s bar set before labelling. Future \"better answer\" tests need a different "
                     "rater setup before any verdict is trusted." % (_pct(share), _pct(BAR)))
    return lines


# --- files -------------------------------------------------------------------------------------------

def _save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def holds_choices(path: Path) -> bool:
    """Whether writing the file again would throw the maintainer's work away."""
    if not path.is_file():
        return False
    return any(read_choice(v) for v in parse_choices(path.read_text(encoding="utf-8")).values())


def build_key(items: Sequence[Dict[str, Any]], comps: Sequence[Dict[str, Any]], raters: Sequence[str],
              seed: int, reply_chars: int = REPLY_CHARS, prompt_chars: int = PROMPT_CHARS) -> Dict[str, Any]:
    return {
        "drawn_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed, "reply_chars": reply_chars, "prompt_chars": prompt_chars,
        "raters": list(raters), "bar": BAR,
        "population": {r: sum(1 for c in comps if c["reading"] == r) for r in READINGS},
        "items": [{"id": it["id"], "cid": it["cid"], "uid": it["uid"], "k": it["k"],
                   "session_id": it["session_id"], "slug": it["slug"], "with_is": it["with_is"],
                   "reading": it["reading"], "votes": it["votes"],
                   "folded": has_fold(blind(it, reply_chars, prompt_chars))} for it in items],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", nargs="?", type=int, const=N_DEFAULT, default=None, metavar="N",
                    help="draw N comparisons (default %d) and write the file" % N_DEFAULT)
    ap.add_argument("--import", dest="import_", action="store_true", help="read the filled file and report")
    ap.add_argument("--vault", default="")
    ap.add_argument("--source", default="", help="#527's cache (default <vault>/.mnemo/broad-value)")
    ap.add_argument("--pairs", default="", help="the file to fill (default <vault>/rater-check/pairs.md)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--reply-chars", type=int, default=REPLY_CHARS,
                    help="characters of a reply shown before the rest is folded (default %d)" % REPLY_CHARS)
    ap.add_argument("--prompt-chars", type=int, default=PROMPT_CHARS,
                    help="the same for the developer's message (default %d)" % PROMPT_CHARS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if (args.export is None) == (not args.import_):
        ap.error("give one of --export or --import")

    from mnemo.core import config, paths

    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(config.load_config())
    source = Path(args.source).expanduser() if args.source else vault / ".mnemo" / mbv.OUT_DIR
    pairs = Path(args.pairs).expanduser() if args.pairs else vault / PAIRS_DIR / PAIRS_NAME
    here = vault / ".mnemo" / PAIRS_DIR
    key_path = here / KEY_NAME
    raters = list(mbv.RATERS)

    if args.export is not None:
        if holds_choices(pairs):
            print("error: %s already holds choices; move it away to draw again" % pairs, file=sys.stderr)
            return 1
        arms = mbv.mrc._read(source / "arms.json", None)
        if arms is None:
            print("error: no %s; run tools/measure_broad_value.py --send first" % (source / "arms.json"),
                  file=sys.stderr)
            return 1
        answers = mbv.mrc._read(source / "answers.json", {}).get(
            mbv.mrc.column("session-model", mbv.rl.ARM_SYSTEM), {})
        all_verdicts = mbv.mrc._read(source / "verdicts.json", {})
        verdicts = {r: all_verdicts.get(mbv.mrc.column(r, mbv.JUDGE_SYSTEM), {}) for r in raters}
        comps = comparisons(arms, answers, verdicts, raters)
        items = draw(comps, args.export, args.seed)
        key = build_key(items, comps, raters, args.seed, args.reply_chars, args.prompt_chars)
        key["pairs"] = str(pairs)
        _save(key_path, json.dumps(key, indent=1, ensure_ascii=False) + "\n")
        _save(pairs, render(items, args.reply_chars, args.prompt_chars))
        seen = [blind(it, args.reply_chars, args.prompt_chars) for it in items]
        words = words_shown(seen)
        print("%d comparisons on file (%s); drew %d (%s), one per unit"
              % (len(comps), ", ".join("%s %d" % (r, n) for r, n in key["population"].items()), len(items),
                 ", ".join("%s %d" % (r, sum(1 for it in items if it["reading"] == r)) for r in READINGS)))
        print("%d of %d items have a reply folded after %d characters; %d words outside the folded blocks "
              "(~%d minutes at %d words a minute)"
              % (sum(1 for it in key["items"] if it["folded"]), len(items), args.reply_chars, words,
                 math.ceil(words / WORDS_PER_MINUTE), WORDS_PER_MINUTE))
        print("fill in: %s" % pairs)
        print("key:     %s" % key_path)
        return 0

    key = mbv.mrc._read(key_path, None)
    if key is None or not pairs.is_file():
        print("error: no %s or no %s; run with --export first" % (key_path, pairs), file=sys.stderr)
        return 1
    result = report(key, parse_choices(pairs.read_text(encoding="utf-8")))
    _save(here / REPORT_NAME, json.dumps(result, indent=1) + "\n")
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print("\n".join(format_report(result, key["raters"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
