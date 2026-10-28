"""Are the reflex's 0.40-0.49 injections on-point less often than those at >= 0.5? (#764)

Usage:
    PYTHONPATH=src python3 tools/measure_inject_band.py --build              # population + frozen sample, local
    PYTHONPATH=src python3 tools/measure_inject_band.py --export-blind DIR   # chunks for a rater, local
    PYTHONPATH=src python3 tools/measure_inject_band.py --import-labels DIR  # fold the labels back, local
    PYTHONPATH=src python3 tools/measure_inject_band.py                      # the report, local
    PYTHONPATH=src python3 tools/measure_inject_band.py --json               # the same as data

Read-only on everything mnemo owns and calls nothing: the judge's scores are
the ones the hook already logged, and the labels come from a rater that reads
the exported chunks somewhere else. Writes go to ``<vault>/.mnemo/inject-band/``.

**Why.** With ``reflex.judge`` on, a rule is injected when the judge scores the
(prompt, rule) pair at or above ``injectAt`` (0.4, the shipped default since
#461). On the maintainer's Windows vault, 2026-10-13 to 10-27, 28% of injected
rules scored 0.40-0.49, and one generic rule was injected 83 times in 76
sessions, always near the bar. Raising ``injectAt`` to 0.5 would cut about a
quarter of the injected bytes; whether it loses rules that mattered was never
measured by band (#411 measured one on-point injection in ten, overall).

**Population.** Every rule the hook injected with the judge's say-so: rows of
``reflex-log.jsonl`` (and its rotated ``.1``) whose ``judge.status`` is ``ok``,
one pair per ``emitted`` slug, carrying the score the judge gave it in that
row. The band is read off that score: ``near`` is ``[0.40, 0.50)``, ``above``
is ``>= 0.50``. The log keeps a hash of the prompt, never its text; the text
is found again in the session's transcript by that hash, computed by the
hook's own ``_prompt_hash``, among both the typed user turns and the prompts
Claude Code queued while the agent was busy (``queued_command`` attachments
and ``queue-operation`` rows) — which is where every background-task notice,
peer message and mnemo child notice lives. A row whose prompt is not found is
counted and left out.

**Kind.** A prompt that opens with a machine envelope (:data:`NOTICE_TAGS`:
``<task-notification>``, ``<cross-session-message>``, ``<mnemo-child-finished>``
and the like) is a ``notice``: nobody typed it, yet the hook runs the reflex on
it like on any other prompt. Everything else is ``typed``. The first look at
the log while writing this tool found about half the injected prompts are
notices, and every one of the 83-injection rule's injections landed on a
``<task-notification>``, so kind is reported beside band throughout.

**Sample and labels.** :data:`PER_BAND` pairs per band, uniform within the
band (so a rule injected 80 times is sampled in proportion to what the user
read), seeded, frozen into ``sample.json`` with the prompt as the judge was sent
it (``judge.state``) and the rule as #411's rater read it
(``rerank.rule_text`` of today's page, read from the file so a rule disabled
since is still shown). The blind chunks and the import are
``measure_reflex_gate``'s own :func:`blind_chunks` and :func:`imported_labels`:
a chunk carries the prompt and the rule text and nothing else — no slug, no
score, no band, no kind — and one prompt's pairs are never split. Same 0/1/2
rubric as #411: 0 noise, 1 marginal, 2 on-point ("inject").

**Pre-registered (written and committed before any label was read).**

- *Primary, the injectAt question.* ``d = on_point(above) - on_point(near)``,
  where ``on_point`` is the share of a band's labelled pairs rated 2, with a
  95% CI from :data:`BOOTSTRAP` resamples of whole prompts (a prompt's pairs
  move together), seed :data:`SEED`, over every kind.

  - **raise to 0.5**: the CI's lower bound is above 0 *and*
    ``on_point(near) <= on_point(above) / 2``;
  - **keep 0.4**: the CI includes 0;
  - anything else fits neither branch, and the report says so rather than
    rounding it into one.

- *Secondary, reported and not deciding injectAt.* The same ``d`` on typed
  prompts alone; and ``on_point(typed) - on_point(notice)`` with its CI over
  both bands. **Skip notices** (a recommendation that the reflex not run on
  machine notices) when that CI's lower bound is above 0 and
  ``on_point(notice) <= on_point(typed) / 2``.

- *The repeat detector.* Two rules, each reported (:func:`repeaters`). By
  score, the shape the issue describes: a rule injected in at least
  :data:`REPEAT_SESSIONS` sessions at a median judge score under 0.5. By kind:
  as many sessions, with at least :data:`REPEAT_NOTICE_SHARE` of its
  injections on notices. The second was added before any label was read,
  when the first turned out not to flag the 83-injection rule: over this
  window its median is 0.53 and its maximum 0.72, but all 80 of its
  injections are on notices. Either is worth a ``doctor`` row when it flags
  at most :data:`REPEAT_MAX_FLAGS` rules and at least 80% of the flagged
  rules' labelled pairs are not on-point.

**The result on the maintainer's Windows vault (2026-10-28).** The bars
above were committed (1ac3b04, the rubric in 84e4ca6) before the rater read
a chunk. Population 2026-10-13 to 10-28: 540 injected prompts, 814 judged
rule injections, 230 (28%) in the near band carrying 28% of the 1.75 MB
injected; the prompt was found for 787. **351 of those 787 (45%) are
notices**, not typed prompts: 230 ``<task-notification>``, 52
``<cross-session-message>``, 48 ``<mnemo-child-finished>``, 17
``<mnemo-pr-follow>``, 4 ``<agent-message>``. 240 pairs labelled blind
(``claude-fable-5-1``, four raters, one per chunk):

- on-point near **32%**, above **62%**: ``d`` +30.8 pp, 95% CI
  [+18.5, +42.8]. The CI excludes 0, but 32.5% is not at most half of 61.7%:
  **neither branch** — not "raise to 0.5", not "keep 0.4";
- typed prompts alone, 46% against 68%: +21.3 pp [+4.2, +38.1], neither;
- typed against notices, 58% against 35%: +23.6 pp [+10.6, +35.9],
  neither. The weakest cell is notices in the near band, 20% on-point of 66;
- the score detector flags 2 rules and the kind detector 7 (the 83-injection
  rule among them, 4 of its 28 sampled pairs on-point, 24 marginal), with
  67% and 68% of their labelled pairs not on-point: **not a doctor row**
  for either.

Read beyond the bars, and labelled as such: raising ``injectAt`` to 0.5
would cut 28% of injected bytes and about 17% of the on-point injections
(230 × 32% ≈ 74, against about 360 kept above). This rater is far more
lenient than #411's: 2% noise overall, where #411 measured 15% at the same
bar. A stricter rater would move both bands, and nothing here says which way
the ratio would go.

**Limits** to carry with any number from here: one rater, a model, blind,
never the developer; the prompt alone with no earlier turn, as in #411, so a
terse "sim, pode rodar" reads as noise against any rule; the rule as its page
reads today, not as it read on the day; one vault, two weeks.

Files, under ``<vault>/.mnemo/inject-band/``:

- ``population.json`` — every injected pair, no text; ``--build`` refuses to
  overwrite it once a label exists.
- ``sample.json`` — the frozen draw the labels cover, with its texts.
- ``labels-<rater>.json`` — one rater's 0/1/2 per pair.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: #411's blind chunks and label import, so this rater sees what that one saw.
mrg = _sibling("measure_reflex_gate")

OUT_DIR = "inject-band"
POPULATION_NAME = "population.json"
SAMPLE_NAME = "sample.json"

LOG_NAMES = ("reflex-log.jsonl.1", "reflex-log.jsonl")

#: The shipped ``injectAt`` and the bar the issue asks about.
INJECT_AT = 0.4
BAND_TOP = 0.5

NEAR = "near"
ABOVE = "above"
BELOW = "below"

TYPED = "typed"
NOTICE = "notice"

#: Envelopes a prompt opens with when nobody typed it. Claude Code's own
#: (background task done, a peer session's message, a teammate's) and mnemo's
#: (``detector.SYNTHETIC_PREFIXES``' child notices and wakes).
NOTICE_TAGS = (
    "<task-notification",
    "<cross-session-message",
    "<agent-message",
    "<mnemo-child-finished",
    "<mnemo-resume",
    "<mnemo-pr-follow",
)

PER_BAND = 120
SAMPLE_SEED = 764

ON_POINT = mrg.ON_POINT
MARGINAL = mrg.MARGINAL
NOISE = mrg.NOISE

DEFAULT_RATER = "claude-fable-5-1"

#: What the rater is told, written next to the chunks by ``--export-blind``.
#: #411 kept only its scale ("0 noise / 1 marginal / 2 inject"); this is that
#: scale spelled out, so the next rater reads the same words as this one.
RUBRIC = """\
Each chunk file holds messages a developer's coding agent received, each with
one or more rules that a memory system injected into the agent's context right
after that message. Some messages were typed by the developer; others are
machine notices (a background task finished, another session sent a message).

For every rule, answer one number:

  2  inject   - the rule bears on what this message asks for or reports; an
                agent handling this message is better off having read it now.
  1  marginal - same area or vocabulary, but it would not change what the agent
                does with this message.
  0  noise    - nothing to do with this message.

Judge each rule on the message alone; you will not see the earlier turns. Do
not reward a rule for being true or well written, only for bearing on this
message. Answer chunk-NNN.json with chunk-NNN.labels.json next to it:
{"<id>": 0|1|2, ...}, one entry per rule id, nothing else.
"""

BOOTSTRAP = 10000
SEED = 764

REPEAT_SESSIONS = 10
REPEAT_NOTICE_SHARE = 0.9
REPEAT_MAX_FLAGS = 10
REPEAT_NOT_ON_POINT = 0.8


# --- the pure part: everything above the vault -------------------------------

def band_of(score: float) -> str:
    if score >= BAND_TOP:
        return ABOVE
    if score >= INJECT_AT:
        return NEAR
    return BELOW


def kind_of(text: str) -> str:
    """``notice`` when the prompt opens with a machine envelope, else ``typed``."""
    head = text.lstrip()
    return NOTICE if any(head.startswith(tag) for tag in NOTICE_TAGS) else TYPED


def notice_tag(text: str) -> Optional[str]:
    head = text.lstrip()
    for tag in NOTICE_TAGS:
        if head.startswith(tag):
            return tag[1:]
    return None


def read_rows(lines: Iterable[str]) -> List[Dict[str, Any]]:
    """Log rows, oldest file first; a torn or blank line is skipped, and a row
    present in both generations (the same session, prompt, moment and slugs)
    counts once."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        key = json.dumps([row.get("session_id"), row.get("prompt_hash"),
                          row.get("ts"), row.get("emitted")])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def row_id(row: Dict[str, Any]) -> str:
    """One injected prompt: its session, its hash and its moment."""
    return mrg.unit_id(str(row.get("session_id")), row.get("ts"), str(row.get("prompt_hash")))


def injected_pairs(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One pair per rule the judge let through, with the score it gave.

    A row the judge did not decide (off, no key, a timeout: the lexical
    fallback injected) carries no score to band, and is not in the population.
    ``bytes`` is what the hook wrote for that rule (``rule_bytes``, in
    ``emitted`` order), when the row says.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        judge = row.get("judge") or {}
        if judge.get("status") != "ok":
            continue
        scores = {}
        for entry in judge.get("scores") or []:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                scores[entry[0]] = entry[1]
        emitted = row.get("emitted") or []
        sizes = row.get("rule_bytes") or []
        for i, slug in enumerate(emitted):
            score = scores.get(slug)
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            out.append({
                "row": row_id(row),
                "session_id": row.get("session_id"),
                "project": row.get("project"),
                "ts": row.get("ts"),
                "prompt_hash": row.get("prompt_hash"),
                "slug": slug,
                "score": float(score),
                "band": band_of(float(score)),
                "bytes": sizes[i] if i < len(sizes) and isinstance(sizes[i], int) else None,
            })
    return out


def transcript_prompts(lines: Iterable[str], hash_of: Callable[[str], str]) -> Dict[str, str]:
    """``{prompt_hash: text}`` for every prompt the hook could have seen in one
    transcript: typed user turns, and prompts queued while the agent was busy.

    Each text is filed under its hash and the hash of it with a trailing
    newline, which is how Claude Code hands some prompts to the hook.
    """
    from mnemo.core.transcript import plain_user_text

    out: Dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        text: Any = None
        kind = entry.get("type")
        if kind == "user" and not entry.get("isSidechain"):
            message = entry.get("message")
            if isinstance(message, dict):
                text = plain_user_text(message.get("content"))
        elif kind == "queue-operation":
            text = entry.get("content")
        elif kind == "attachment":
            attachment = entry.get("attachment") or {}
            if attachment.get("type") == "queued_command":
                text = attachment.get("prompt")
        if isinstance(text, str) and text:
            for variant in (text, text + "\n"):
                out.setdefault(hash_of(variant), text)
    return out


def draw(pairs: Sequence[Dict[str, Any]], *, per_band: int = PER_BAND,
         seed: int = SAMPLE_SEED) -> List[Dict[str, Any]]:
    """``per_band`` pairs from each of ``near`` and ``above``, uniform within
    the band, seeded. Pairs come back grouped by prompt, prompts shuffled, so a
    rater meets the two bands interleaved and one prompt's pairs together."""
    rng = random.Random(seed)
    chosen: List[Dict[str, Any]] = []
    for band in (NEAR, ABOVE):
        pool = sorted((p for p in pairs if p["band"] == band),
                      key=lambda p: (p["row"], p["slug"]))
        rng.shuffle(pool)
        chosen.extend(pool[:per_band])
    rows = sorted({p["row"] for p in chosen})
    rng.shuffle(rows)
    order = {row: i for i, row in enumerate(rows)}
    return sorted(chosen, key=lambda p: (order[p["row"]], p["slug"]))


def as_units(sample: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sample in ``measure_reflex_gate``'s unit shape: one prompt, its
    sampled rules as candidates, so its blind chunks apply unchanged."""
    units: List[Dict[str, Any]] = []
    by_row: Dict[str, Dict[str, Any]] = {}
    for pair in sample:
        unit = by_row.get(pair["row"])
        if unit is None:
            unit = {"uid": pair["row"], "prompt": pair["prompt"], "candidates": []}
            by_row[pair["row"]] = unit
            units.append(unit)
        unit["candidates"].append({"slug": pair["slug"], "text": pair["rule"]})
    return units


def labelled(sample: Sequence[Dict[str, Any]],
             labels: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    out = []
    for pair in sample:
        value = (labels.get(pair["row"]) or {}).get(pair["slug"])
        if value in (NOISE, MARGINAL, ON_POINT):
            out.append(dict(pair, label=value))
    return out


def on_point(pairs: Sequence[Dict[str, Any]]) -> Optional[float]:
    return (sum(1 for p in pairs if p["label"] == ON_POINT) / len(pairs)) if pairs else None


def share(pairs: Sequence[Dict[str, Any]], value: int) -> Optional[float]:
    return (sum(1 for p in pairs if p["label"] == value) / len(pairs)) if pairs else None


def diff_ci(pairs: Sequence[Dict[str, Any]], high: Callable[[Dict[str, Any]], bool],
            low: Callable[[Dict[str, Any]], bool], *, draws: int = BOOTSTRAP,
            seed: int = SEED) -> Dict[str, Any]:
    """``on_point(high) - on_point(low)`` with a 95% CI over whole prompts.

    A prompt's pairs are resampled together: two rules injected into one prompt
    share whatever made that prompt easy or hard to be on-point for. A draw
    in which one side is empty is skipped, and counted.
    """
    a = [p for p in pairs if high(p)]
    b = [p for p in pairs if low(p)]
    point = None
    if a and b:
        point = on_point(a) - on_point(b)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        if high(p) or low(p):
            groups[p["row"]].append(p)
    keys = sorted(groups)
    rng = random.Random(seed)
    values: List[float] = []
    skipped = 0
    for _ in range(draws if keys else 0):
        hi = [0, 0]
        lo = [0, 0]
        for _k in range(len(keys)):
            for p in groups[keys[rng.randrange(len(keys))]]:
                side = hi if high(p) else lo
                side[0] += p["label"] == ON_POINT
                side[1] += 1
        if not hi[1] or not lo[1]:
            skipped += 1
            continue
        values.append(hi[0] / hi[1] - lo[0] / lo[1])
    values.sort()
    ci = None
    if values:
        ci = [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]
    return {"high_n": len(a), "low_n": len(b),
            "high": on_point(a), "low": on_point(b),
            "diff": point, "ci": ci, "skipped_draws": skipped}


def verdict(cmp: Dict[str, Any], *, yes: str, no: str) -> str:
    """The pre-registered reading of one comparison: ``yes`` when the CI is
    above 0 and the low side is at most half the high one, ``no`` when the CI
    includes 0, ``neither`` otherwise."""
    ci = cmp.get("ci")
    if ci is None or cmp.get("high") is None or cmp.get("low") is None:
        return "no data"
    if ci[0] > 0 and cmp["low"] <= cmp["high"] / 2.0:
        return yes
    if ci[0] <= 0 <= ci[1]:
        return no
    return "neither"


def repeaters(pairs: Sequence[Dict[str, Any]], *, by: str = "score",
              sessions: int = REPEAT_SESSIONS) -> List[Dict[str, Any]]:
    """Rules injected in at least ``sessions`` sessions that look like the
    83-injection rule, found without reading a label. Most-injected first.

    ``by="score"``: a median judge score under :data:`BAND_TOP`. ``by="kind"``:
    at least :data:`REPEAT_NOTICE_SHARE` of the injections whose prompt was
    found landed on a machine notice.
    """
    by_slug: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        by_slug[p["slug"]].append(p)
    out = []
    for slug, here in by_slug.items():
        seen = {p["session_id"] for p in here}
        if len(seen) < sessions:
            continue
        median = statistics.median(p["score"] for p in here)
        kinds = [p.get("kind") for p in here if p.get("kind")]
        notices = sum(1 for k in kinds if k == NOTICE)
        if by == "score":
            hit = median < BAND_TOP
        else:
            hit = bool(kinds) and notices / len(kinds) >= REPEAT_NOTICE_SHARE
        if hit:
            out.append({
                "slug": slug, "injections": len(here), "sessions": len(seen),
                "median": round(median, 3), "max": max(p["score"] for p in here),
                "near": sum(1 for p in here if p["band"] == NEAR),
                "notices": notices, "with_kind": len(kinds),
            })
    out.sort(key=lambda r: (-r["injections"], r["slug"]))
    return out


def detector(population: Sequence[Dict[str, Any]], rated: Sequence[Dict[str, Any]],
             by: str) -> Dict[str, Any]:
    """One repeat rule read against the labels: what it flags, and how often
    the flagged rules' labelled pairs were on-point."""
    flagged = repeaters(population, by=by)
    by_slug: Dict[str, List[int]] = defaultdict(list)
    for p in rated:
        by_slug[p["slug"]].append(p["label"])
    values = [v for r in flagged for v in by_slug.get(r["slug"], [])]
    for r in flagged:
        r["labels"] = sorted(by_slug.get(r["slug"], []))
    not_on_point = (sum(1 for v in values if v != ON_POINT) / len(values)) if values else None
    if flagged and not values:
        reading = "no data"
    elif (flagged and len(flagged) <= REPEAT_MAX_FLAGS
          and not_on_point is not None and not_on_point >= REPEAT_NOT_ON_POINT):
        reading = "doctor row"
    else:
        reading = "not a doctor row"
    rule = ("median score < %g" % BAND_TOP if by == "score"
            else ">= %d%% on notices" % round(100 * REPEAT_NOTICE_SHARE))
    return {"rule": "sessions >= %d and %s" % (REPEAT_SESSIONS, rule), "flagged": flagged,
            "labelled_pairs": len(values), "not_on_point": not_on_point, "verdict": reading}


def population_summary(pairs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """What the population is, and what raising the bar would cut from it."""
    rows = {p["row"] for p in pairs}
    out: Dict[str, Any] = {"prompts": len(rows), "pairs": len(pairs), "bands": {}, "kinds": {}}
    total_bytes = sum(p["bytes"] or 0 for p in pairs)
    for band in (BELOW, NEAR, ABOVE):
        here = [p for p in pairs if p["band"] == band]
        out["bands"][band] = {
            "pairs": len(here),
            "bytes": sum(p["bytes"] or 0 for p in here),
        }
    out["bytes"] = total_bytes
    out["near_byte_share"] = (out["bands"][NEAR]["bytes"] / total_bytes) if total_bytes else None
    by_tenth: Dict[str, int] = defaultdict(int)
    for p in pairs:
        by_tenth["%.1f" % (int(p["score"] * 10) / 10.0)] += 1
    out["by_tenth"] = dict(sorted(by_tenth.items()))
    for kind in (TYPED, NOTICE, None):
        here = [p for p in pairs if p.get("kind") == kind]
        out["kinds"][kind or "unmatched"] = {
            "pairs": len(here),
            "near": sum(1 for p in here if p["band"] == NEAR),
            "above": sum(1 for p in here if p["band"] == ABOVE),
        }
    tags: Dict[str, int] = defaultdict(int)
    for p in pairs:
        if p.get("tag"):
            tags[p["tag"]] += 1
    out["notice_tags"] = dict(sorted(tags.items(), key=lambda kv: -kv[1]))
    days = sorted(str(p["ts"])[:10] for p in pairs if p.get("ts"))
    out["window"] = [days[0], days[-1]] if days else None
    return out


def report(population: Sequence[Dict[str, Any]], sample: Sequence[Dict[str, Any]],
           labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    rated = labelled(sample, labels)
    out: Dict[str, Any] = {
        "population": population_summary(population),
        "sampled": len(sample),
        "labelled": len(rated),
        "cells": {},
    }
    for kind in ("all", TYPED, NOTICE):
        for band in (NEAR, ABOVE, "both"):
            here = [p for p in rated
                    if (kind == "all" or p["kind"] == kind) and (band == "both" or p["band"] == band)]
            out["cells"]["%s/%s" % (kind, band)] = {
                "n": len(here), "prompts": len({p["row"] for p in here}),
                "on_point": on_point(here), "marginal": share(here, MARGINAL),
                "noise": share(here, NOISE),
            }
    is_above = lambda p: p["band"] == ABOVE  # noqa: E731
    is_near = lambda p: p["band"] == NEAR  # noqa: E731
    primary = diff_ci(rated, is_above, is_near)
    typed_only = diff_ci([p for p in rated if p["kind"] == TYPED], is_above, is_near)
    kinds = diff_ci(rated, lambda p: p["kind"] == TYPED, lambda p: p["kind"] == NOTICE)
    out["primary"] = dict(primary, verdict=verdict(primary, yes="raise to 0.5", no="keep 0.4"))
    out["typed_only"] = dict(typed_only, verdict=verdict(typed_only, yes="raise to 0.5", no="keep 0.4"))
    out["kinds"] = dict(kinds, verdict=verdict(kinds, yes="skip notices", no="notices as typed"))

    out["repeaters"] = {by: detector(population, rated, by) for by in ("score", "kind")}
    return out


def _pct(value: Optional[float]) -> str:
    return "  n/a" if value is None else "%4.0f%%" % (100 * value)


def _pp(value: Optional[float]) -> str:
    return "n/a" if value is None else "%+.1f" % (100 * value)


def _cmp_line(name: str, cmp: Dict[str, Any]) -> str:
    ci = cmp.get("ci")
    return "  %-34s %s pp, 95%% CI [%s, %s]  (n %d vs %d)  -> %s" % (
        name, _pp(cmp.get("diff")), _pp(ci[0]) if ci else "n/a",
        _pp(ci[1]) if ci else "n/a", cmp["high_n"], cmp["low_n"], cmp["verdict"])


def format_report(data: Dict[str, Any], *, rater: str) -> str:
    pop = data["population"]
    lines = [
        "population %s: %d injected prompts, %d judged rule injections"
        % (" to ".join(pop["window"]) if pop["window"] else "(empty)", pop["prompts"], pop["pairs"]),
        "  by score tenth: " + ", ".join("%s: %d" % kv for kv in pop["by_tenth"].items()),
        "  near [%.2f, %.2f): %d injections, %s of the %d injected bytes"
        % (INJECT_AT, BAND_TOP, pop["bands"][NEAR]["pairs"], _pct(pop["near_byte_share"]).strip(),
           pop["bytes"]),
        "  kind: " + ", ".join("%s %d (near %d, above %d)" % (k, v["pairs"], v["near"], v["above"])
                              for k, v in pop["kinds"].items()),
        "  notices: " + (", ".join("%s %d" % kv for kv in pop["notice_tags"].items()) or "none"),
        "",
        "sample: %d pairs, %d labelled by %s (blind)" % (data["sampled"], data["labelled"], rater),
        "%-16s %6s %8s %7s %9s %7s" % ("", "pairs", "prompts", "noise", "marginal", "on-point"),
    ]
    for key, cell in data["cells"].items():
        lines.append("  %-14s %6d %8d %7s %9s %7s" % (
            key, cell["n"], cell["prompts"], _pct(cell["noise"]), _pct(cell["marginal"]),
            _pct(cell["on_point"])))
    lines += [
        "",
        "pre-registered (#764):",
        _cmp_line("on-point above - near (primary)", data["primary"]),
        _cmp_line("same, typed prompts only", data["typed_only"]),
        _cmp_line("on-point typed - notice", data["kinds"]),
    ]
    for by in ("score", "kind"):
        det = data["repeaters"][by]
        lines.append("")
        lines.append("repeat detector by %s (%s): %d rule(s) flagged, %d labelled pairs, "
                     "%s not on-point -> %s"
                     % (by, det["rule"], len(det["flagged"]), det["labelled_pairs"],
                        _pct(det["not_on_point"]).strip(), det["verdict"]))
        for r in det["flagged"]:
            lines.append("  %-50s %3d inj / %3d sessions, median %.2f, max %.2f, near %d, "
                         "notices %d/%d, labels %s"
                         % (r["slug"][:50], r["injections"], r["sessions"], r["median"],
                            r["max"], r["near"], r["notices"], r["with_kind"],
                            r.get("labels") or "-"))
    return "\n".join(lines)


# --- the vault side: everything below reads mnemo, nothing above does -------

def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _projects_roots(vault: Path, given: Sequence[str]) -> List[Path]:
    """``--projects`` when given; else every account's transcripts, the default
    one first (``accounts.remembered``), since a second Claude config dir keeps
    its own ``projects/``."""
    if given:
        return [Path(p) for p in given]
    from mnemo.core.sessions import accounts

    out = [Path.home() / ".claude" / "projects"]
    for home in accounts.remembered(vault):
        out.append(Path(home) / "projects")
    return [p for p in out if p.is_dir()]


def _rule_texts(vault: Path, slugs: Iterable[str]) -> Tuple[Dict[str, str], Dict[str, bool]]:
    """Each slug's text as #411's rater read it, and whether it is disabled now.

    Read from the page itself, not the activation index: a rule vetoed since
    (``mnemo disable-rule``) is gone from the index, and dropping it would drop
    exactly the 83-injection rule this issue is about. The reflex index's
    ``path`` names the page when it still lists the slug; otherwise the pages
    are walked for a stem or derived slug that matches.
    """
    from mnemo.core.filters import derive_rule_slug, is_disabled, iter_shared_pages, parse_frontmatter
    from mnemo.core.mcp import rerank
    from mnemo.core.mcp.tools import _extract_body
    from mnemo.core.reflex.index import load_index

    wanted = set(slugs)
    found: Dict[str, Path] = {}
    index = load_index(vault) or {}
    for slug in wanted:
        rel = ((index.get("docs") or {}).get(slug) or {}).get("path")
        if rel and (vault / rel).is_file():
            found[slug] = vault / rel
    missing = wanted - set(found)
    if missing:
        for md in iter_shared_pages(vault):
            try:
                fm = parse_frontmatter(md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            for name in (md.stem, derive_rule_slug(fm or {}, md.stem)):
                if name in missing and name not in found:
                    found[name] = md
    texts: Dict[str, str] = {}
    disabled: Dict[str, bool] = {}
    for slug, path in found.items():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        texts[slug] = rerank.rule_text(_extract_body(text))
        disabled[slug] = is_disabled(parse_frontmatter(text) or {})
    return texts, disabled


def _build(vault: Path, projects: Sequence[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from mnemo.core.mcp.recall_sessions import _transcripts_by_session
    from mnemo.hooks.user_prompt_submit import _prompt_hash

    lines: List[str] = []
    for name in LOG_NAMES:
        path = vault / ".mnemo" / name
        if path.is_file():
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    rows = read_rows(lines)
    pairs = injected_pairs(rows)

    transcripts: Dict[str, Path] = {}
    for root in _projects_roots(vault, projects):
        for sid, path in _transcripts_by_session(root).items():
            transcripts.setdefault(sid, path)
    prompts: Dict[str, Dict[str, str]] = {}
    for sid in sorted({p["session_id"] for p in pairs}):
        path = transcripts.get(sid)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        prompts[sid] = transcript_prompts(text.splitlines(), _prompt_hash)

    texts, disabled = _rule_texts(vault, {p["slug"] for p in pairs})
    full: Dict[str, str] = {}
    for p in pairs:
        prompt = (prompts.get(p["session_id"]) or {}).get(p["prompt_hash"])
        p["kind"] = kind_of(prompt) if prompt is not None else None
        p["tag"] = notice_tag(prompt) if prompt is not None else None
        p["disabled_now"] = disabled.get(p["slug"])
        if prompt is not None:
            full[p["row"]] = prompt
    built = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "log_rows": len(rows),
        "judged_rows": sum(1 for r in rows if (r.get("judge") or {}).get("status") == "ok"),
        "prompt_found": sum(1 for p in pairs if p["kind"] is not None),
        "rule_found": sum(1 for p in pairs if p["slug"] in texts),
    }
    eligible = [p for p in pairs if p["kind"] is not None and p["slug"] in texts]
    sample = []
    for p in draw(eligible):
        sample.append(dict(p, prompt=mrg.judge_state(full[p["row"]]), rule=texts[p["slug"]]))
    return pairs, {"built": built, "sample": sample}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", action="store_true",
                        help="read the log and transcripts into the population and the sample (local)")
    parser.add_argument("--force", action="store_true",
                        help="with --build: overwrite files a label is keyed to")
    parser.add_argument("--projects", action="append", default=[],
                        help="transcript root (repeatable; default: every account's)")
    parser.add_argument("--export-blind", metavar="DIR",
                        help="write blind chunk files and the id map for a rater (local)")
    parser.add_argument("--import-labels", metavar="DIR",
                        help="read the rater's *.labels.json back from DIR (local)")
    parser.add_argument("--rater", default=DEFAULT_RATER, help="whose labels to read or write")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli

    vault = cli._resolve_vault()
    out = vault / ".mnemo" / OUT_DIR
    population_path = out / POPULATION_NAME
    sample_path = out / SAMPLE_NAME
    labels_path = out / ("labels-%s.json" % mrg._safe_rater(args.rater))

    if args.build:
        if any(out.glob("labels-*.json")) and not args.force:
            print("error: %s holds labels keyed to the sample; --force to rebuild" % out,
                  file=sys.stderr)
            return 1
        pairs, built = _build(vault, args.projects)
        _write_json(population_path, {"built": built["built"], "pairs": pairs})
        _write_json(sample_path, {"seed": SAMPLE_SEED, "per_band": PER_BAND,
                                  "pairs": built["sample"]})
        info = built["built"]
        print("%d log rows, %d judged; %d injected pairs, prompt found for %d, rule for %d; "
              "%d sampled -> %s" % (info["log_rows"], info["judged_rows"], len(pairs),
                                    info["prompt_found"], info["rule_found"],
                                    len(built["sample"]), out))
        return 0

    if not population_path.is_file() or not sample_path.is_file():
        print("error: no population at %s; run with --build first" % out, file=sys.stderr)
        return 1
    population = json.loads(population_path.read_text(encoding="utf-8"))["pairs"]
    sample = json.loads(sample_path.read_text(encoding="utf-8"))["pairs"]
    labels_file = (json.loads(labels_path.read_text(encoding="utf-8"))
                   if labels_path.is_file() else {})
    labels = labels_file.get("labels") or {}

    if args.export_blind:
        target = Path(args.export_blind)
        target.mkdir(parents=True, exist_ok=True)
        chunks, ids = mrg.blind_chunks(as_units(sample), labels)
        for i, chunk in enumerate(chunks, start=1):
            _write_json(target / ("chunk-%03d.json" % i), chunk)
        _write_json(target / "ids.json", {"sample_file": SAMPLE_NAME, "ids": ids})
        (target / "RUBRIC.txt").write_text(RUBRIC, encoding="utf-8")
        print("%d rules in %d chunk file(s) -> %s\n"
              "the rater answers each chunk-NNN.json with a chunk-NNN.labels.json "
              "next to it: {\"<id>\": 0|1|2}" % (len(ids), len(chunks), target))
        return 0

    if args.import_labels:
        source = Path(args.import_labels)
        id_file = source / "ids.json"
        if not id_file.is_file():
            print("error: no ids.json in %s; --export-blind writes it" % source, file=sys.stderr)
            return 1
        ids = json.loads(id_file.read_text(encoding="utf-8"))["ids"]
        given: Dict[str, Any] = {}
        for path in sorted(source.glob("*.labels.json")):
            given.update(json.loads(path.read_text(encoding="utf-8")))
        folded, problems = mrg.imported_labels(ids, given)
        if problems:
            print("error: %d problem(s) with the labels in %s; nothing written"
                  % (len(problems), source), file=sys.stderr)
            for problem in problems[:10]:
                print("  " + problem, file=sys.stderr)
            return 1
        for uid, pairs in folded.items():
            labels.setdefault(uid, {}).update(pairs)
        _write_json(labels_path, {
            "rater": args.rater, "blind": True,
            "scale": "0 noise / 1 marginal / 2 inject",
            "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample_file": SAMPLE_NAME, "labels": labels})
        print("%d labels over %d prompts -> %s"
              % (sum(len(v) for v in labels.values()), len(labels), labels_path))
        return 0

    data = report(population, sample, labels)
    data["rater"] = labels_file.get("rater", args.rater)
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=2))
    else:
        print(_provenance.line(prov))
        print(format_report(data, rater=data["rater"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
