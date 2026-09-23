"""Is a judge reading the (prompt, rule) pair a better reflex gate than BM25F? (#411, #412)

Usage:
    PYTHONPATH=src python3 tools/measure_reflex_gate.py                        # the report, local
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --json                 # the same as data
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --build                # replay every prompt (local)
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --sample               # draw 220 fired + 80 silenced
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --export-blind DIR      # chunks for a rater, local
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --import-labels DIR     # fold the labels back, local
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --score                # dry run: pairs, tokens, cost
    PYTHONPATH=src python3 tools/measure_reflex_gate.py --score --send         # ask the judge

**Only ``--score --send`` leaves the machine.** It posts each sampled prompt
(first 1,200 characters) and the first 800 characters of its top-3 candidate
rules to TypeSafe's ``/v1/systemone``, a third party, and needs
``TYPESAFE_API_KEY``. Everything else — the replay, the sample, exporting,
importing, the whole report — reads files on disk and calls nothing.

The question, and why it is not ``measure_rerank_filter.py``. That tool grades
the list an agent asked for; this one grades the injection nobody asked for.
Since ``relativeGap`` went to 1.0 the reflex fires on roughly every second
prompt, and the ranking it fires on cannot tell "shares vocabulary with the
prompt" from "bears on what the prompt asks for". So the population here is
every prompt typed on this machine, replayed through ``reflex.decide``, and
the unit is one prompt with the top three rules **the vault already held when
it was typed** (``replay.rule_facts().learned_at`` — a candidate extracted
after the prompt could not have been injected into it).

The report puts four gates side by side on the same labelled pairs: the
shipped one, injecting the whole top 3, a BM25F bar, and the judge at each
``--at``. For each: how many rules it injects, how many prompts it fires on,
what share of what it injects is noise / marginal / on-point, and how many of
the on-point rules in the pool it keeps. Splits are locked by id
(``int(uid, 16) % 3 == 0`` is test), the two loose bars are fitted on dev at
90% recall and applied unchanged to test, and the AUC says how well each
signal separates on-point pairs from the rest before any bar is chosen.

The judge's rows are computed by ``mnemo.core.reflex.judge``'s own
:func:`~mnemo.core.reflex.judge.question` and
:func:`~mnemo.core.reflex.judge.chosen`, so what is graded here is what the
hook ships, and a test pins that they cannot drift apart.

Limits to carry with any number from here. The labels are a model's
(``claude-fable-5-1``, blind, one 0/1/2 per pair), never the developer whose
prompt it was. There are 64 on-point pairs in the sample and 25 of them in the
test third, so "21 of 25" is a small number kept, not a rate with a tight
interval. One question wording was tried. And the cost is real: ~1 s inside
the ``UserPromptSubmit`` hook, on up to every second prompt.

Files, all under ``<vault>/.mnemo``:

- ``reflex-units.json`` — the population, keyed by ``uid``. ``--build``
  writes it and refuses to overwrite one that exists: every label and every
  score is filed under those ids.
- ``reflex-sample.json`` — the seeded draw the labels cover.
- ``reflex-labels-<rater>.json`` — one rater's 0/1/2 per pair.
- ``reflex-scores.json`` — the judge's probabilities, under a key per question
  variant; this tool reads and writes ``v0_reflex``, the question
  ``judge.question`` ships.

On a python.org macOS build ``urllib`` has no CA bundle;
``SSL_CERT_FILE=/etc/ssl/cert.pem`` fixes it. Do not disable verification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The cost rate comes from the first judged-ranking tool: one definition, so
#: two tools cannot price the same pair two ways.
mrj = _sibling("measure_recall_judged")

UNITS_NAME = "reflex-units.json"
SAMPLE_NAME = "reflex-sample.json"
SCORES_NAME = "reflex-scores.json"

#: The question ``judge.question`` ships. The file keeps one key per variant
#: tried; a report must not mix them.
SCORES_KEY = "v0_reflex"

#: The label set the 2026-09-20 measurement used. A person's labels would be
#: ``--rater human``, and are a different file.
DEFAULT_RATER = "claude-fable-5-1"

#: How deep the pool goes. The stage's own default, and what was labelled.
TOP = 3

#: The seeded draw: enough fired prompts to see what the shipped gate injects,
#: and enough silenced ones to see what it drops. Not proportional on purpose —
#: silences are the cheaper half to be wrong about.
SAMPLE_FIRED = 220
SAMPLE_SILENCED = 80
SAMPLE_SEED = 3

#: The label scale, as the blind rubric states it.
ON_POINT = 2
MARGINAL = 1
NOISE = 0

#: Rules per request when scoring. ``measure_recall_judged``'s, so a pair costs
#: the same in both.
CHUNK = mrj.CHUNK

#: Rules per blind chunk file. A unit is never split across two of them: a
#: rater must see one prompt with all of its candidates.
BLIND_CHUNK = 60

#: What the report grades the judge at unless ``--at`` says otherwise. 0.4 is
#: the shipped ``reflex.judge.injectAt`` (0.6 until #461); the other two are
#: the stricter bars it was chosen over.
DEFAULT_AT = (0.4, 0.6, 0.7)

#: The recall a fitted bar has to keep on dev. Loosest bar that keeps this
#: share of the dev on-point pairs, then applied unchanged to test.
FIT_RECALL = 0.9


# --- the pure part: everything above the vault -------------------------------

def unit_id(session_id: str, ts: float, text: str) -> str:
    """The id a label and a score are filed under.

    The identity of one typed prompt — the session, the moment, and enough of
    the text to tell two prompts in the same second apart — so re-building
    from the same transcripts re-derives the same ids and the labels still
    fit. Changing this orphans every label on disk.
    """
    return hashlib.md5(
        json.dumps([session_id, ts, text[:200]]).encode("utf-8")).hexdigest()[:10]


def part_of(uid: str) -> str:
    """dev or test, by the id alone: a locked split, not a seed.

    A unit lands in the same part whoever runs the tool and whenever, so the
    test third cannot quietly be re-drawn after a bar has been read off dev.
    """
    return "test" if int(uid, 16) % 3 == 0 else "dev"


def draw(units: Sequence[Dict[str, Any]], *, fired: int = SAMPLE_FIRED,
         silenced: int = SAMPLE_SILENCED, seed: int = SAMPLE_SEED) -> List[str]:
    """The seeded sample: ``fired`` prompts the shipped gate injected on, plus
    ``silenced`` it did not, shuffled together.

    Stratified rather than uniform because the two halves answer different
    questions — what the gate injects, and what it drops — and a uniform draw
    over a corpus that fires on 58% of prompts would spend most of its labels
    on the first.
    """
    rng = random.Random(seed)
    yes = [u for u in units if u.get("fired")]
    no = [u for u in units if not u.get("fired")]
    rng.shuffle(yes)
    rng.shuffle(no)
    chosen = yes[:fired] + no[:silenced]
    rng.shuffle(chosen)
    return [u["uid"] for u in chosen]


def shipped_slugs(unit: Dict[str, Any]) -> List[str]:
    """What the shipped gate injected: the candidates it accepted."""
    return [c["slug"] for c in unit["candidates"] if c.get("accepted")]


def all_slugs(unit: Dict[str, Any]) -> List[str]:
    return [c["slug"] for c in unit["candidates"]]


def over_bar(unit: Dict[str, Any], values: Dict[str, float], at: float) -> List[str]:
    """Candidates whose score clears ``at``, best first — the judge's own rule.

    :func:`mnemo.core.reflex.judge.chosen` is what the hook calls, and this
    calls it, so a threshold graded here is a threshold that ships.
    """
    from mnemo.core.reflex import judge

    here = {c["slug"]: float(values.get(c["slug"], 0.0)) for c in unit["candidates"]}
    return judge.chosen(here, at)


def held_at(scores: Sequence[Tuple[str, float]], learned_at: Dict[str, Optional[float]],
            ts: float, *, top: int = TOP) -> List[Tuple[str, float]]:
    """The top ``top`` ranked rules the vault had already learned by ``ts``.

    Today's vault firing on a rule extracted from the answer to that very
    prompt is hindsight, not retrieval (``replay``'s own distinction), so a
    candidate learned after the prompt is dropped *before* the top is taken —
    otherwise a rule that could not have been injected would push down one
    that could. A rule with no date is dropped too: undatable is not evidence
    of having been there.
    """
    return [(slug, score) for slug, score in scores
            if learned_at.get(slug) is not None and learned_at[slug] <= ts][:top]


def bm25f_of(unit: Dict[str, Any]) -> Dict[str, float]:
    return {c["slug"]: float(c["bm25f"]) for c in unit["candidates"]}


def gate_row(name: str, units: Sequence[Dict[str, Any]],
             labels: Dict[str, Dict[str, int]], keep) -> Dict[str, Any]:
    """One line of the table: what a gate would put in front of the user.

    ``noise``/``marginal``/``on_point`` are shares of the *labelled* rules it
    injects — what the user would read. ``on_point_kept`` is out of every
    on-point pair in the pool, which is the other half of the trade: a gate
    can buy a clean share by injecting almost nothing.
    """
    injected = labelled = 0
    counts = {NOISE: 0, MARGINAL: 0, ON_POINT: 0}
    prompts = 0
    kept = total = 0
    for unit in units:
        label = labels.get(unit["uid"], {})
        chosen = keep(unit)
        injected += len(chosen)
        prompts += bool(chosen)
        for slug in chosen:
            if slug in label:
                labelled += 1
                counts[label[slug]] = counts.get(label[slug], 0) + 1
        total += sum(1 for slug, value in label.items() if value == ON_POINT)
        kept += sum(1 for slug in chosen if label.get(slug) == ON_POINT)
    denominator = labelled or 1
    return {
        "gate": name,
        "injected": injected,
        "prompts": prompts,
        "of_prompts": len(units),
        "labelled": labelled,
        "noise": round(counts[NOISE] / denominator, 4),
        "marginal": round(counts[MARGINAL] / denominator, 4),
        "on_point": round(counts[ON_POINT] / denominator, 4),
        "on_point_kept": kept,
        "on_point_total": total,
    }


def pairs_of(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
             values: Dict[str, Dict[str, float]]) -> List[Tuple[float, bool]]:
    """``(score, is_on_point)`` for every labelled pair — the AUC's input."""
    out: List[Tuple[float, bool]] = []
    for unit in units:
        label = labels.get(unit["uid"], {})
        here = values.get(unit["uid"], {})
        for candidate in unit["candidates"]:
            slug = candidate["slug"]
            if slug in label:
                out.append((float(here.get(slug, 0.0)), label[slug] == ON_POINT))
    return out


def auc(scored: Sequence[Tuple[float, bool]]) -> Optional[float]:
    """Area under the ROC for "on-point vs the rest", ties at half credit.

    The rank formulation (Mann-Whitney U over average ranks), so a signal that
    hands out the same number to many pairs — BM25F does not, the judge does —
    is neither rewarded nor punished for it. ``None`` when one class is empty.
    """
    positives = [value for value, hit in scored if hit]
    negatives = [value for value, hit in scored if not hit]
    if not positives or not negatives:
        return None
    values = positives + negatives
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    total = sum(ranks[:len(positives)])
    n = len(positives)
    return (total - n * (n + 1) / 2.0) / (n * len(negatives))


def fit_bar(scored: Sequence[Tuple[float, bool]], *, recall: float = FIT_RECALL) -> Optional[float]:
    """The loosest bar on this split that keeps ``recall`` of the on-point pairs.

    Loosest rather than best-by-anything: the failure this gate is being asked
    to avoid is dropping a rule that mattered, and a bar chosen to maximise a
    combined score would trade those away silently. Only values a pair
    actually scored are candidates, so the bar is reachable.
    """
    positives = sorted(value for value, hit in scored if hit)
    if not positives:
        return None
    need = int(math.ceil(recall * len(positives)))
    best = None
    for candidate in sorted(set(positives)):
        if sum(1 for value in positives if value >= candidate) >= need:
            best = candidate
    return best


def report(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
           scores: Dict[str, Dict[str, float]], *,
           bars: Sequence[float] = DEFAULT_AT) -> Dict[str, Any]:
    """Every gate on dev and on test, plus the dev-fitted bars and the AUC."""
    dev = [u for u in units if part_of(u["uid"]) == "dev"]
    test = [u for u in units if part_of(u["uid"]) == "test"]
    local = {u["uid"]: bm25f_of(u) for u in units}
    judged = {u["uid"]: {s: float(v) for s, v in (scores.get(u["uid"]) or {}).items()}
              for u in units}

    fitted = {
        "jev": fit_bar(pairs_of(dev, labels, judged)),
        "bm25f": fit_bar(pairs_of(dev, labels, local)),
    }
    out: Dict[str, Any] = {
        "units": len(units),
        "pairs": sum(len(u["candidates"]) for u in units),
        "labelled": sum(len(v) for v in labels.values()),
        "scored": sum(len(v) for v in judged.values()),
        "scores_key": SCORES_KEY,
        "dev": len(dev), "test": len(test),
        "bars": list(bars),
        "fitted": fitted,
        "fit_recall": FIT_RECALL,
        "auc": {},
        "parts": {},
    }
    for name, chosen_units in (("all", units), ("dev", dev), ("test", test)):
        rows = [
            gate_row("shipped", chosen_units, labels, shipped_slugs),
            gate_row("every top %d" % TOP, chosen_units, labels, all_slugs),
        ]
        if fitted["bm25f"] is not None:
            rows.append(gate_row(
                "bm25f >= %g (dev)" % fitted["bm25f"], chosen_units, labels,
                lambda u, at=fitted["bm25f"]: over_bar(u, local[u["uid"]], at)))
        if fitted["jev"] is not None:
            rows.append(gate_row(
                "judge >= %g (dev)" % fitted["jev"], chosen_units, labels,
                lambda u, at=fitted["jev"]: over_bar(u, judged[u["uid"]], at)))
        for bar in bars:
            # The fitted bar is already a row; a `--at` that lands on it would
            # print the same numbers twice under two names.
            if fitted["jev"] is not None and bar == fitted["jev"]:
                continue
            rows.append(gate_row(
                "judge >= %g" % bar, chosen_units, labels,
                lambda u, at=bar: over_bar(u, judged[u["uid"]], at)))
        out["parts"][name] = rows
        out["auc"][name] = {
            "jev": auc(pairs_of(chosen_units, labels, judged)),
            "bm25f": auc(pairs_of(chosen_units, labels, local)),
        }
    return out


def label_counts(labels: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    return {str(k): sum(1 for pairs in labels.values() for v in pairs.values() if v == k)
            for k in (NOISE, MARGINAL, ON_POINT)}


def format_report(data: Dict[str, Any], *, rater: str, blind: bool) -> str:
    lines = [
        "%d prompts sampled, %d (prompt, rule) pairs; labels: %s%s"
        % (data["units"], data["pairs"], rater, ", blind" if blind else ""),
        "%d pairs labelled, %d scored (%s); dev %d / test %d, split by id"
        % (data["labelled"], data["scored"], data["scores_key"], data["dev"], data["test"]),
        "bars fitted on dev at %d%% recall: judge %s, bm25f %s"
        % (round(100 * data["fit_recall"]),
           _bar(data["fitted"]["jev"]), _bar(data["fitted"]["bm25f"])),
    ]
    for part in ("all", "dev", "test"):
        rows = data["parts"][part]
        lines.append("")
        lines.append("%-22s  injected  prompts   noise  marginal  on-point   on-point kept"
                     % ("--- %s (%d prompts)" % (part, rows[0]["of_prompts"])))
        for row in rows:
            lines.append("  %-20s %8d  %4d/%-4d  %4.0f%%     %4.0f%%     %4.0f%%   %8s" % (
                row["gate"], row["injected"], row["prompts"], row["of_prompts"],
                100 * row["noise"], 100 * row["marginal"], 100 * row["on_point"],
                "%d/%d" % (row["on_point_kept"], row["on_point_total"])))
        auc_here = data["auc"][part]
        lines.append("  AUC on-point vs rest: judge %s, bm25f %s"
                     % (_auc(auc_here["jev"]), _auc(auc_here["bm25f"])))
    lines.append("")
    lines.append("the dev rows fitted the two \"(dev)\" bars; the test rows were read once "
                 "with those bars unchanged.")
    return "\n".join(lines)


def _bar(value: Optional[float]) -> str:
    return "n/a" if value is None else "%g" % value


def _auc(value: Optional[float]) -> str:
    return "n/a" if value is None else "%.3f" % value


def blind_chunks(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
                 *, limit: int = BLIND_CHUNK) -> Tuple[List[List[Dict[str, Any]]], Dict[str, List[str]]]:
    """Chunk files for a rater that is not on this machine, and the id map.

    A chunk carries the prompt and the rule text and nothing else: no slug, no
    uid, no BM25F score, no whether the shipped gate fired. The rater answers
    with ids, and only the id map on this machine turns one back into a pair.
    Pairs a rater has already labelled are left out, so an interrupted round
    continues instead of being re-asked.
    """
    chunks: List[List[Dict[str, Any]]] = []
    ids: Dict[str, List[str]] = {}
    current: List[Dict[str, Any]] = []
    size = 0
    for unit in units:
        done = labels.get(unit["uid"], {})
        rules = [c for c in unit["candidates"] if c.get("text") and c["slug"] not in done]
        if not rules:
            continue
        if current and size + len(rules) > limit:
            chunks.append(current)
            current, size = [], 0
        entry: Dict[str, Any] = {"message": unit["prompt"], "rules": []}
        for candidate in rules:
            key = str(len(ids))
            ids[key] = [unit["uid"], candidate["slug"]]
            entry["rules"].append({"id": int(key), "rule": candidate["text"]})
        current.append(entry)
        size += len(rules)
    if current:
        chunks.append(current)
    return chunks, ids


def imported_labels(ids: Dict[str, List[str]],
                    given: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, int]], List[str]]:
    """Fold ``{id: 0|1|2}`` back onto (uid, slug), or say what is wrong with it.

    Everything or nothing. A missing rule or a 3 would quietly become a
    different measurement than the one the chunks asked for, so the caller
    writes nothing while any problem stands.
    """
    problems: List[str] = []
    labels: Dict[str, Dict[str, int]] = {}
    answers = {str(key): value for key, value in given.items()}
    for key in sorted(ids, key=lambda k: int(k)):
        if key not in answers:
            problems.append("no label for id %s" % key)
    for key in sorted(answers, key=lambda k: int(k) if k.isdigit() else -1):
        value = answers[key]
        if key not in ids:
            problems.append("id %s is not in the export" % key)
        elif isinstance(value, bool) or value not in (NOISE, MARGINAL, ON_POINT):
            problems.append("id %s: %r is not 0, 1 or 2" % (key, value))
        else:
            uid, slug = ids[key]
            labels.setdefault(uid, {})[slug] = int(value)
    return labels, problems


def pending_chunks(unit: Dict[str, Any], done: Dict[str, Any],
                   *, size: int = CHUNK) -> List[List[str]]:
    """The candidates of one unit still to score, in stable order.

    A slug that already has a number is never asked again: a run interrupted
    after 200 prompts resumes at 201 instead of paying for all 300. Membership,
    not truthiness — a judge's honest ``0.0`` is a number, and re-asking for it
    every run would pay for the same pair forever.
    """
    todo = [c["slug"] for c in unit["candidates"]
            if c.get("text") and c["slug"] not in done]
    return [todo[i:i + size] for i in range(0, len(todo), size)]


# --- the vault side: everything below reads mnemo, nothing above does -------


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_rater(rater: str) -> str:
    """``label_recall_pairs.labels_name``'s spelling, so every tool names one
    rater's files the same way."""
    return "".join(c for c in rater.lower() if c.isalnum() or c in "-_") or "human"


def _labels_path(vault: Path, rater: str) -> Path:
    return vault / ".mnemo" / ("reflex-labels-%s.json" % _safe_rater(rater))


def _build(vault: Path, projects: Optional[str]) -> List[Dict[str, Any]]:
    """Replay every typed prompt on disk through the hook's own decision.

    Three things make this the population and not a convenient sample. The
    prompts are :func:`replay.collect_prompts`'s — the same reader ``mnemo
    replay`` uses, so hook context and slash-command output are not mistaken
    for something a person typed. The decision is ``reflex.decide``'s, on the
    global config with no per-project calibration, so one set of thresholds
    grades every prompt. And a candidate is dropped unless the vault had
    already learned it when the prompt was typed: today's vault firing on a
    rule extracted from the answer to that very prompt is hindsight, not
    retrieval.

    A prompt the token pre-gate rejected, or one left with no eligible
    candidate, is not a unit — there was nothing for a judge to be better than.
    """
    from mnemo.core import config as cfg_mod
    from mnemo.core.mcp import rerank, tools as mcp_tools
    from mnemo.core.reflex import replay
    from mnemo.core.reflex.decide import decide, doc_token_sets
    from mnemo.core.reflex.index import load_index

    cfg = cfg_mod.load_config()
    reflex_cfg = cfg.get("reflex") or {}
    index = load_index(vault)
    if index is None:
        raise SystemExit("error: no reflex index in %s; run `mnemo index` first" % vault)
    learned_at = {slug: facts.learned_at
                  for slug, facts in replay.rule_facts(vault).items()}
    tokens = doc_token_sets(index)
    prompts = replay.collect_prompts(
        vault, projects_root=Path(projects) if projects else None)

    texts: Dict[str, str] = {}
    units: List[Dict[str, Any]] = []
    for prompt in prompts:
        decision = decide(index, project=prompt.project, prompt=prompt.text,
                          reflex_cfg=reflex_cfg, doc_tokens=tokens)
        if not decision.scores:
            continue
        held = held_at(decision.scores, learned_at, prompt.ts)
        if not held:
            continue
        accepted = set(decision.accepted)
        candidates = []
        for slug, score in held:
            if slug not in texts:
                page = mcp_tools.read_mnemo_rule(vault, slug, scope="vault") or {}
                texts[slug] = rerank.rule_text(page.get("body") or "")
            candidates.append({"slug": slug, "bm25f": round(float(score), 4),
                               "accepted": slug in accepted, "text": texts[slug]})
        units.append({
            "uid": unit_id(prompt.session_id, prompt.ts, prompt.text),
            "project": prompt.project, "ts": prompt.ts,
            "session_id": prompt.session_id,
            # The same text the judge would be sent, and no more of it.
            "prompt": judge_state(prompt.text),
            "prompt_chars": len(prompt.text),
            "silence_reason": decision.silence_reason,
            "fired": any(c["accepted"] for c in candidates),
            "floor": decision.thresholds.get("absolute_floor"),
            "candidates": candidates,
        })
    return units


def judge_state(text: str) -> str:
    """The prompt as the stage would send it — ``judge.state``'s own text.

    Stored on the unit rather than the raw prompt so that what a rater reads,
    what the judge is sent and what the report grades are one string.
    """
    from mnemo.core.reflex import judge

    return judge.state(text)["developer_message"]


def _load_units(vault: Path) -> List[Dict[str, Any]]:
    path = vault / ".mnemo" / UNITS_NAME
    if not path.is_file():
        raise SystemExit("error: no units at %s; run with --build first" % path)
    return json.loads(path.read_text(encoding="utf-8"))["units"]


def _load_sample(vault: Path, units: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    path = vault / ".mnemo" / SAMPLE_NAME
    if not path.is_file():
        raise SystemExit("error: no sample at %s; run with --sample first" % path)
    wanted = json.loads(path.read_text(encoding="utf-8"))["uids"]
    by_uid = {u["uid"]: u for u in units}
    missing = [uid for uid in wanted if uid not in by_uid]
    if missing:
        raise SystemExit(
            "error: %d of %d sampled uids are not in %s — the units were rebuilt "
            "under different ids and every label is orphaned" % (len(missing), len(wanted), UNITS_NAME))
    return [by_uid[uid] for uid in wanted]


def _normalise_scores(raw: Any) -> Dict[str, Dict[str, float]]:
    """``{uid: {slug: score}}``, from either shape the file has carried.

    An early run filed a list per slug (one entry per request); the shipped
    shape is one number. Both read as the last number given.
    """
    out: Dict[str, Dict[str, float]] = {}
    for uid, pairs in (raw or {}).items():
        if not isinstance(pairs, dict):
            continue
        here: Dict[str, float] = {}
        for slug, value in pairs.items():
            if isinstance(value, list):
                value = value[-1] if value else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                here[slug] = float(value)
        out[uid] = here
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", action="store_true",
                        help="replay every typed prompt into units (local)")
    parser.add_argument("--force", action="store_true",
                        help="with --build/--sample: overwrite the file every label is keyed to")
    parser.add_argument("--projects", default=None,
                        help="transcript root (default: ~/.claude/projects)")
    parser.add_argument("--sample", action="store_true",
                        help="draw the seeded %d fired + %d silenced sample (local)"
                             % (SAMPLE_FIRED, SAMPLE_SILENCED))
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--export-blind", metavar="DIR",
                        help="write blind chunk files and the id map for a rater (local)")
    parser.add_argument("--import-labels", metavar="DIR",
                        help="read the rater's *.labels.json back from DIR (local)")
    parser.add_argument("--score", action="store_true",
                        help="ask the judge for a probability per pair (dry run without --send)")
    parser.add_argument("--send", action="store_true",
                        help="with --score: post prompts and rule texts to TypeSafe (third party)")
    parser.add_argument("--rater", default=DEFAULT_RATER, help="whose labels to read or write")
    parser.add_argument("--at", default="",
                        help="judge thresholds for the table (default: %s)"
                             % ",".join("%g" % b for b in DEFAULT_AT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli

    vault = cli._resolve_vault()
    units_path = vault / ".mnemo" / UNITS_NAME
    sample_path = vault / ".mnemo" / SAMPLE_NAME
    scores_path = vault / ".mnemo" / SCORES_NAME
    labels_path = _labels_path(vault, args.rater)

    if args.build:
        if units_path.is_file() and not args.force:
            print("error: %s exists and every label is keyed to its uids; --force to rebuild"
                  % units_path, file=sys.stderr)
            return 1
        units = _build(vault, args.projects)
        _write_json(units_path, {
            "built_from": "typed prompts in session transcripts, replayed through "
                          "reflex.decide with today's index and config",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "top": TOP, "units": units})
        print("%d units (%d fired), %d (prompt, rule) pairs -> %s"
              % (len(units), sum(1 for u in units if u["fired"]),
                 sum(len(u["candidates"]) for u in units), units_path))
        return 0

    units = _load_units(vault)

    if args.sample:
        if sample_path.is_file() and not args.force:
            print("error: %s exists and the labels cover exactly its uids; --force to redraw"
                  % sample_path, file=sys.stderr)
            return 1
        uids = draw(units, seed=args.seed)
        _write_json(sample_path, {"seed": args.seed, "fired": SAMPLE_FIRED,
                                  "silenced": SAMPLE_SILENCED, "uids": uids})
        print("%d prompts sampled -> %s" % (len(uids), sample_path))
        return 0

    sample = _load_sample(vault, units)
    labels_file = (json.loads(labels_path.read_text(encoding="utf-8"))
                   if labels_path.is_file() else {})
    labels = labels_file.get("labels") or {}

    if args.export_blind:
        out = Path(args.export_blind)
        out.mkdir(parents=True, exist_ok=True)
        chunks, ids = blind_chunks(sample, labels)
        for i, chunk in enumerate(chunks, start=1):
            _write_json(out / ("chunk-%03d.json" % i), chunk)
        _write_json(out / "ids.json", {"units_file": UNITS_NAME, "ids": ids})
        print("%d rules in %d chunk file(s) -> %s\n"
              "the rater answers each chunk-NNN.json with a chunk-NNN.labels.json "
              "next to it: {\"<id>\": 0|1|2}" % (len(ids), len(chunks), out))
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
        folded, problems = imported_labels(ids, given)
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
            "units_file": UNITS_NAME, "labels": labels})
        print("%d labels over %d prompts -> %s"
              % (sum(len(v) for v in labels.values()), len(labels), labels_path))
        return 0

    saved = json.loads(scores_path.read_text(encoding="utf-8")) if scores_path.is_file() else {}
    scores = _normalise_scores(saved.get(SCORES_KEY))

    if args.score:
        from mnemo.core.mcp import rerank
        from mnemo.core.reflex import judge

        jobs = [(u, chunk) for u in sample
                for chunk in pending_chunks(u, scores.get(u["uid"], {}))]
        pairs = sum(len(chunk) for _, chunk in jobs)
        if not args.send:
            tokens = sum(len(u["prompt"]) + sum(
                len(c["text"]) + 420 for c in u["candidates"] if c["slug"] in chunk)
                for u, chunk in jobs) // 4
            print("dry run: %d pairs still to score in %d request(s), ~%d tokens (~$%.4f). "
                  "--send posts them to %s" % (pairs, len(jobs), tokens,
                                               tokens * mrj.USD_PER_MTOK / 1e6,
                                               rerank.TYPESAFE_URL))
            return 0
        chosen = judge.settings(None)
        key = os.environ.get(chosen["keyEnv"])
        if not key:
            print("error: --score --send needs %s" % chosen["keyEnv"], file=sys.stderr)
            return 1
        client = rerank.typesafe_client(key, model=chosen["model"], timeout=120.0)
        failed = got = 0
        for unit, chunk in jobs:
            texts = {c["slug"]: c["text"] for c in unit["candidates"]}
            try:
                answered = judge.scores(unit["prompt"], texts, chunk, client)
            except Exception:
                # One failed request must not lose the answers already paid
                # for: the file is written after every chunk and a re-run asks
                # only for what is still missing.
                failed += 1
                continue
            scores.setdefault(unit["uid"], {}).update(answered)
            got += len(answered)
            saved[SCORES_KEY] = scores
            _write_json(scores_path, saved)
        print("scored %d of %d pending pairs, %d failed request(s) -> %s"
              % (got, pairs, failed, scores_path))
        return 1 if failed else 0

    if not labels:
        print("error: no labels at %s; --export-blind then --import-labels" % labels_path,
              file=sys.stderr)
        return 1
    if not scores:
        print("error: no %s scores at %s; run with --score" % (SCORES_KEY, scores_path),
              file=sys.stderr)
        return 1
    bars = [float(p) for p in args.at.split(",") if p.strip()] or list(DEFAULT_AT)
    data = report(sample, labels, scores, bars=bars)
    data["label_counts"] = label_counts(labels)
    data["rater"] = labels_file.get("rater", args.rater)
    data["blind"] = bool(labels_file.get("blind"))
    print(json.dumps(data, indent=2) if args.json
          else format_report(data, rater=data["rater"], blind=data["blind"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
