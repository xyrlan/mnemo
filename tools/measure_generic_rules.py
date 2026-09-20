"""Can a judge tell a generic aphorism from a rule about this system? (#410)

Usage:
    PYTHONPATH=src python3 tools/measure_generic_rules.py --sample           # draw the rules once, local
    PYTHONPATH=src python3 tools/measure_generic_rules.py --export-blind DIR # chunks for a rater, local
    PYTHONPATH=src python3 tools/measure_generic_rules.py --import-labels DIR --rater NAME
    PYTHONPATH=src python3 tools/measure_generic_rules.py --score            # dry run: rules, tokens, cost
    PYTHONPATH=src python3 tools/measure_generic_rules.py --score --send     # ask the judge
    PYTHONPATH=src python3 tools/measure_generic_rules.py                    # report, local

**Only ``--score --send`` leaves the machine.** It posts the name and the
first 800 characters of each sampled rule to TypeSafe's ``/v1/systemone``, a
third party, through ``mnemo.core.mcp.rerank``'s own client and key. Drawing
the sample, exporting it, importing labels and the whole report read files on
disk and call nothing.

What is being measured. The 2026-09-01 audit put ~40% of the vault as generic
aphorisms — "measure before optimizing", "verify, don't assume" — and a hand
count on 2026-09-19, after ``reclassify``, still found 21 of 99 ``mnemo``
rules generic. #404 measured that a queried ``list_rules_by_topic`` answer is
three quarters rules irrelevant to the task; generic rules are a standing
share of that, and unlike an off-topic rule they are irrelevant to *every*
task. A detector that works is an extraction gate and a ``reclassify`` fast
path; a detector that does not work must be known not to work before either
is built.

On 2026-09-19 a calibrated judge (``jev-1.13.0``) was reported to reach AUC
0.936 generic-vs-rest on a held-out n=60 with the "what would an engineer
lose" question, 0.885 with an earlier wording and 0.74-0.80 with "names a
concrete artifact"; as a filter it flagged 8 of 60, 6 truly generic and no
project-specific rule. **Not one of those numbers can be re-run**: the
scripts, the blind labels and the scores lived in a session scratchpad that
was deleted. They are quoted here as the claim this tool exists to re-measure,
never as a result. Nothing in this repo may cite them until a run of this tool
produces them again.

How it is set up so the answer can be believed:

- the sample is **stratified by project** (``--per-project``), because a
  detector graded on one big project's rules is graded on one writer's habits,
  and four projects hold 76% of the vault's live rules;
- the rater and the judge read **the same thing** — :func:`rule_view` of the
  name and the body — so a difference between them is not a difference in
  what they were shown;
- the blind view is :func:`blind`, which carries the id, the name and the
  rule and nothing else: no project, no path, no score, no slug. A test pins
  what that function lets through;
- the split is **by the rule's id**, an md5 of its slug, so the test half
  cannot be re-drawn after a threshold has been read off the dev half;
- a **local baseline** is reported next to the judge, so the judge is graded
  against something rather than against nothing. Generic rules name nothing,
  so the baseline simply counts the concrete things a rule names
  (:func:`named_things`). A judge that does not beat counting backticks is not
  worth a request;
- when two raters have labelled, their **agreement** is reported, because
  that is the error bar on every other number here.

Every signal is mapped to a *genericness* in [0, 1], ascending, by
:func:`genericness`. The transforms are monotone, so an AUC is unchanged by
them, and one threshold grid reads on the judge and on the baseline alike.

Files, all under ``<vault>/.mnemo``:

- ``generic-sample.json`` — the rules a rater and the judge see, frozen with
  the text they were shown. ``--sample`` refuses to redraw once any rater
  file holds a label.
- ``generic-labels-<rater>.json`` — one rater's 0/1/2 per rule id.
- ``generic-scores.json`` — the judge's raw answers, one key per question
  variant, so the wordings can be compared instead of argued about.

On a python.org macOS build ``urllib`` has no CA bundle;
``SSL_CERT_FILE=/etc/ssl/cert.pem`` fixes it. Do not disable verification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
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


#: ``auc`` comes from the first judged-ranking tool and the cost rate from the
#: second: one definition of each, so two tools cannot report the same number
#: two ways.
mrj = _sibling("measure_recall_judged")
mrk = _sibling("measure_rerank_judges")

SAMPLE_NAME = "generic-sample.json"
SCORES_NAME = "generic-scores.json"

#: The pages a rater is drawn from: what ``list_rules_by_topic`` can return.
#: ``user`` pages are the maintainer's own notes and ``project`` pages are not
#: rules, so neither is a candidate.
LIVE_TYPES = ("feedback", "reference")

#: Bump when a question below changes: old answers answer an old question, and
#: a report that mixes the two measures nothing.
QUESTION_VERSION = 1

#: Rules drawn per project. 17 projects hold live rules, so the default is a
#: sample of about a hundred — twice the n=60 the lost measurement had, and
#: still one sitting for a rater.
PER_PROJECT = 6
SEED = 1

#: Rules per blind chunk file, and per request to the judge. A rule is at most
#: 800 characters, so 40 of them sit well inside the provider's 64k-token
#: request; questions are judged in isolation, so a chunk boundary cannot
#: change an answer.
BLIND_CHUNK = 40
CHUNK = mrj.CHUNK

GENERIC, PRACTICE, PROJECT_SPECIFIC = 0, 1, 2

#: The scale, word for word as the blind export states it.
LEVELS = (
    (GENERIC, "generic", "true of any codebase; deleting it loses nothing"),
    (PRACTICE, "specific practice", "a real technique, not tied to this project"),
    (PROJECT_SPECIFIC, "project-specific",
     "names this system's components, data or incidents"),
)

#: What a sampled rule may show a rater. Everything else stays in the sample
#: file: the project it came from, its slug, its path, its type.
VISIBLE = ("id", "name", "rule")

#: Where a flag would be set, on the genericness scale every signal is mapped
#: onto. 0.75 is the ``loss`` judge answering "a technique" or lower.
THRESHOLDS = (0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.90)

#: The local signal the judge has to beat, named in the tables.
BASELINE = "named-things"


def rule_view(name: str, text: str) -> str:
    """The one thing a rater and the judge both read.

    Name and body together, because that is what an agent is offered: the list
    carries slugs and names, and the body is read after. A comparison between
    a rater and a judge is only a comparison when neither saw more.
    """
    joined = "%s. %s" % (name.strip().rstrip("."), text.strip())
    return " ".join(joined.split())


#: The pre-registered wording, and the levels it is answered on. Three ordered
#: levels rather than a probability, so the answer is the decision a rater
#: makes and not a number that has to be calibrated against one; and the levels
#: are :data:`LEVELS` said from the other side — what is lost, rather than what
#: the rule is — so a judge's answer and a rater's label are the same scale.
LOSS_INSTRUCTIONS = (
    "What would an engineer lose if this stored engineering rule were deleted "
    "from the vault? Rule: "
)
LOSS_CRITERIA = (
    "Nothing would be lost: it states something true of any codebase, and an "
    "engineer who never read it would work the same way",
    "A technique would be lost: a real practice worth knowing, but not tied to "
    "any one system",
    "Knowledge about this system that exists nowhere else would be lost: its "
    "components, its data, or an incident it had",
)

#: The cheaper wording the lost run put at 0.74-0.80, kept so the comparison
#: between the two is a measurement and not a memory.
ARTIFACT_INSTRUCTIONS = (
    "This stored engineering rule names a concrete artifact of the system it "
    "came from — a file, a symbol, a command, a table, a number, an incident — "
    "rather than describing engineering in general. Rule: "
)
ARTIFACT_CRITERIA = {
    "true": "The rule names things that exist in one particular system",
    "false": "The rule is general advice that would read the same in any codebase",
}


def _loss_question(view: str) -> Dict[str, Any]:
    return {"type": "score", "instructions": LOSS_INSTRUCTIONS + view,
            "criteria": list(LOSS_CRITERIA)}


def _artifact_question(view: str) -> Dict[str, Any]:
    return {"type": "noul", "instructions": ARTIFACT_INSTRUCTIONS + view,
            "criteria": dict(ARTIFACT_CRITERIA)}


#: The question variants, by the key their answers are filed under. ``answer``
#: is the field the provider returns and ``generic`` turns it into a
#: genericness in [0, 1]: a variant is a wording *and* the direction it points.
VARIANTS = {
    "loss": {"build": _loss_question, "answer": "score", "scale": 2.0},
    "artifact": {"build": _artifact_question, "answer": "noul", "scale": 1.0},
}

#: The one the report leads with, and the one a threshold would be taken from.
PRIMARY = "loss"

#: What the judge is told it is looking at. The rules ride in the questions,
#: so a request carries many of them and the state stays the same.
STATE = {
    "vault": "A vault of engineering rules extracted from one developer's past "
             "sessions and offered back to an agent before it writes code. Each "
             "question quotes one rule as the agent would be shown it.",
}


def question(variant: str, view: str) -> Dict[str, Any]:
    return VARIANTS[variant]["build"](view)


def genericness(variant: str, raw: float) -> float:
    """One variant's raw answer as a genericness in [0, 1], ascending.

    Both wordings point the other way — they score how much a rule is *worth*
    — so both are flipped. The map is monotone, which is why an AUC computed
    on it is the AUC of the raw answer and one threshold grid reads on every
    signal in the report.
    """
    scale = float(VARIANTS[variant]["scale"])
    return 1.0 - max(0.0, min(scale, float(raw))) / scale


#: A backticked span, a path, or a file name with a known extension. Not a
#: parser: the point is to count how much of a rule is *a name of something*,
#: and to do it the way a reader would at a glance.
_NAMED = re.compile(
    r"`[^`\n]+`"
    r"|\b[\w.-]+/[\w./-]*[\w]"
    r"|\b[\w-]+\.(?:py|ts|tsx|js|jsx|json|md|ya?ml|sh|rs|sql|toml|ini|cfg|lock)\b"
)


def named_things(text: str) -> List[str]:
    """The concrete things a rule names, deduplicated, in the order they appear.

    The local baseline's whole idea: a generic aphorism names nothing, so
    counting the names is a detector that costs no request. If the judge
    cannot beat this, the judge is not worth its key.
    """
    out: List[str] = []
    for match in _NAMED.findall(text):
        token = match.strip("`")
        if token and token not in out:
            out.append(token)
    return out


def baseline_signal(text: str) -> float:
    """:func:`named_things` as a genericness in [0, 1], ascending.

    ``1 / (1 + n)``: names nothing at all is 1.0, one name is 0.5, three names
    is 0.25. Monotone in the count and nothing more — the shape carries no
    claim, only the ordering does.
    """
    return 1.0 / (1.0 + len(named_things(text)))


def rule_id(slug: str) -> str:
    """The id a label and a score are filed under.

    The slug, hashed: re-drawing the sample re-derives the same id for a rule
    that is still in the vault, so a redraw does not orphan a label. Changing
    this orphans every label on disk.
    """
    return hashlib.md5(slug.encode("utf-8")).hexdigest()[:10]


def part_of(id_: str) -> str:
    """dev or test, by the id's parity alone: a locked split, not a seed.

    A rule lands in the same half whoever runs the tool and whenever, so a
    threshold read off dev cannot be checked against a test half that was
    quietly re-drawn around it.
    """
    return "test" if int(id_, 16) % 2 else "dev"


def stratum(candidate: Dict[str, Any]) -> str:
    """The project a rule is drawn under: the first of its projects, sorted.

    A rule can belong to several projects. One of them has to be the stratum
    or the strata overlap, and taking the first sorted one makes the choice a
    function of the rule rather than of the order the index was walked. A rule
    with no project — a universal one — is its own stratum.
    """
    projects = sorted(candidate.get("projects") or [])
    return projects[0] if projects else "-"


def draw(candidates: Sequence[Dict[str, Any]], *, per_project: int = PER_PROJECT,
         seed: int = SEED) -> List[Dict[str, Any]]:
    """``per_project`` rules from each project, shuffled together, deterministic.

    Shuffled *together* on purpose: a rater handed one project's rules in a
    block would learn the stratification from the reading alone, and the
    blindness the export is built for would be gone by the third card. A
    project with fewer rules than asked gives what it has.
    """
    rng = random.Random(seed)
    pools: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in sorted(candidates, key=lambda c: c["slug"]):
        if not candidate.get("rule"):
            continue
        pools.setdefault(stratum(candidate), []).append(candidate)
    picked: List[Dict[str, Any]] = []
    for key in sorted(pools):
        pool = list(pools[key])
        rng.shuffle(pool)
        picked.extend(pool[:per_project])
    rng.shuffle(picked)
    return [{
        "id": rule_id(c["slug"]), "slug": c["slug"], "project": stratum(c),
        "projects": sorted(c.get("projects") or []), "type": c.get("type", ""),
        "name": c["name"], "rule": c["rule"],
    } for c in picked]


def blind(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What leaves for a rater. A test pins this to :data:`VISIBLE`."""
    return {k: entry[k] for k in VISIBLE}


def blind_chunks(entries: Sequence[Dict[str, Any]], done: Dict[str, Any],
                 *, limit: int = BLIND_CHUNK) -> List[List[Dict[str, Any]]]:
    """Chunk files for a rater, leaving out what this rater already answered.

    An interrupted round continues instead of being asked again, and a second
    rater — whose file is empty — gets the whole sample.
    """
    todo = [blind(e) for e in entries if e["id"] not in done]
    return [todo[i:i + limit] for i in range(0, len(todo), limit)]


def imported_labels(asked: Sequence[str], given: Dict[str, Any],
                    known: Sequence[str]) -> Tuple[Dict[str, int], List[str]]:
    """Fold ``{id: 0|1|2}`` back onto rule ids, or say what is wrong with it.

    Everything or nothing. A rule the chunks asked about and the rater skipped,
    or a 3, would quietly make this a different measurement than the one the
    chunks asked for, so the caller writes nothing while any problem stands.
    """
    problems: List[str] = []
    labels: Dict[str, int] = {}
    answers = {str(key): value for key, value in given.items()}
    for id_ in asked:
        if id_ not in answers:
            problems.append("no label for %s" % id_)
    for id_ in sorted(answers):
        value = answers[id_]
        if id_ not in known:
            problems.append("%s is not a rule in the sample" % id_)
        elif isinstance(value, bool) or value not in (GENERIC, PRACTICE, PROJECT_SPECIFIC):
            problems.append("%s: %r is not 0, 1 or 2" % (id_, value))
        else:
            labels[id_] = int(value)
    return labels, problems


def pending(entries: Sequence[Dict[str, Any]], done: Dict[str, Any],
            *, size: int = CHUNK) -> List[List[Dict[str, Any]]]:
    """The rules still to score under one variant, in stable order, ``size`` at a time.

    A rule that already has an answer is never asked again: a run that died
    after 60 rules resumes at 61 instead of paying for all of them twice.
    """
    todo = [e for e in sorted(entries, key=lambda e: e["id"]) if e["id"] not in done]
    return [todo[i:i + size] for i in range(0, len(todo), size)]


def ask(variant: str, batch: Sequence[Dict[str, Any]], client: Any) -> Dict[str, float]:
    """One request for one chunk; the raw answers by rule id.

    A rule the provider did not answer for is left out — absent means "not
    scored", which the report counts apart and never reads as "generic".
    """
    if not batch:
        return {}
    answer_key = str(VARIANTS[variant]["answer"])
    out = client(dict(STATE), {
        "r%d" % i: question(variant, rule_view(e["name"], e["rule"]))
        for i, e in enumerate(batch)})
    answers = out.get("answers") or {}
    found: Dict[str, float] = {}
    for i, entry in enumerate(batch):
        value = (answers.get("r%d" % i) or {}).get(answer_key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found[entry["id"]] = float(value)
    return found


def estimate(variant: str, batches: Sequence[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    """Rules, requests, tokens and cost of a ``--send``, from characters / 4.

    A bound to read before paying, not a bill: the question's own text rides
    with every rule and is counted too.
    """
    rules = sum(len(batch) for batch in batches)
    chars = len(json.dumps(STATE)) * max(1, len(batches))
    for batch in batches:
        for entry in batch:
            chars += len(json.dumps(question(variant, rule_view(entry["name"], entry["rule"]))))
    tokens = chars // 4
    return {"rules": rules, "requests": len(batches), "tokens": tokens,
            "usd": round(tokens * mrj.USD_PER_MTOK / 1e6, 4)}


# --- grading -----------------------------------------------------------------


def signals(entries: Sequence[Dict[str, Any]],
            scores: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Every signal as a genericness per rule id: the judge's variants, then the
    local baseline, which needs no request and is always there."""
    out: Dict[str, Dict[str, float]] = {}
    for variant in sorted(VARIANTS):
        raw = scores.get(variant) or {}
        out[variant] = {id_: genericness(variant, value)
                        for id_, value in raw.items()
                        if isinstance(value, (int, float)) and not isinstance(value, bool)}
    out[BASELINE] = {e["id"]: baseline_signal(rule_view(e["name"], e["rule"]))
                     for e in entries}
    return out


def auc_row(values: Dict[str, float], labels: Dict[str, int],
            ids: Sequence[str]) -> Dict[str, Any]:
    """One signal's separation of each class from the rest, on ``ids``.

    Both columns read the same way — 1.0 is perfect, 0.5 is a coin flip — so
    the project-specific one is computed on the signal *flipped*: the scale is
    genericness, and a rule that names this system's own components sits at
    the bottom of it. Reporting the raw AUC there would print 0.000 for a
    perfect detector, which is a number nobody reads twice.

    Only rules that have both a label and a number count: a rule the judge
    never answered for is not evidence either way.
    """
    graded = [(values[i], labels[i]) for i in ids if i in values and i in labels]

    def split(positive: int, sign: float) -> Optional[float]:
        return mrk.auc([sign * v for v, l in graded if l == positive],
                       [sign * v for v, l in graded if l != positive])

    return {
        "rules": len(graded),
        "auc_generic": split(GENERIC, 1.0),
        "auc_project_specific": split(PROJECT_SPECIFIC, -1.0),
    }


def threshold_row(values: Dict[str, float], labels: Dict[str, int],
                  ids: Sequence[str], at: float) -> Dict[str, Any]:
    """What a filter at ``at`` would do: what it flags, and what it gets wrong.

    ``project_flagged`` is the expensive error — a rule that names this
    system's own components thrown away as an aphorism — and it is reported
    beside the catch, never under it.
    """
    graded = [(values[i], labels[i]) for i in ids if i in values and i in labels]
    flagged = [l for v, l in graded if v >= at]
    return {
        "threshold": at, "rules": len(graded), "flagged": len(flagged),
        "generic_flagged": sum(1 for l in flagged if l == GENERIC),
        "generic": sum(1 for _, l in graded if l == GENERIC),
        "project_flagged": sum(1 for l in flagged if l == PROJECT_SPECIFIC),
        "project": sum(1 for _, l in graded if l == PROJECT_SPECIFIC),
    }


def agreement(first: Dict[str, int], second: Dict[str, int]) -> Dict[str, Any]:
    """Two raters on the rules both labelled: the error bar on everything else.

    ``off_by_two`` is the disagreement that matters — one rater calls a rule a
    generic aphorism and the other calls it knowledge about the system — and
    no AUC computed from these labels can be read as tighter than this number.
    """
    both = [(first[i], second[i]) for i in sorted(first) if i in second]
    return {
        "rules": len(both),
        "exact": sum(1 for a, b in both if a == b),
        "off_by_two": sum(1 for a, b in both if abs(a - b) == 2),
        "confusion": {"%d->%d" % (a, b): sum(1 for x, y in both if (x, y) == (a, b))
                      for a in (0, 1, 2) for b in (0, 1, 2)},
    }


def report(entries: Sequence[Dict[str, Any]], raters: Dict[str, Dict[str, int]],
           scores: Dict[str, Dict[str, Any]], *, rater: str,
           thresholds: Sequence[float] = THRESHOLDS,
           variant: str = PRIMARY) -> Dict[str, Any]:
    """The whole local report: who labelled what, how each signal separates it,
    and what a threshold fixed on dev then does on test."""
    labels = raters.get(rater) or {}
    values = signals(entries, scores)
    parts = {"dev": [], "test": [], "all": []}  # type: Dict[str, List[str]]
    for entry in entries:
        parts[part_of(entry["id"])].append(entry["id"])
        parts["all"].append(entry["id"])
    table = {name: {part: auc_row(values[name], labels, ids)
                    for part, ids in parts.items()}
             for name in sorted(values)}
    pairs = sorted(raters)
    return {
        "rules": len(entries),
        "projects": len({e["project"] for e in entries}),
        "question_version": QUESTION_VERSION,
        "variant": variant,
        "dev": len(parts["dev"]), "test": len(parts["test"]),
        "rater": rater,
        "labelled": {name: len(v) for name, v in sorted(raters.items())},
        "label_counts": {str(k): sum(1 for v in labels.values() if v == k)
                         for k in (0, 1, 2)},
        "scored": {name: len(values[name]) for name in sorted(values)},
        "auc": table,
        "thresholds": {
            part: [threshold_row(values[variant], labels, parts[part], at)
                   for at in thresholds]
            for part in ("dev", "test")},
        "agreement": (agreement(raters[pairs[0]], raters[pairs[1]])
                      if len(pairs) >= 2 else None),
        "agreement_between": pairs[:2] if len(pairs) >= 2 else [],
    }


def _fmt(value: Optional[float]) -> str:
    return "  n/a" if value is None else "%.3f" % value


def format_report(r: Dict[str, Any]) -> str:
    lines = [
        "%d rules over %d projects, question version %d; split dev %d / test %d"
        % (r["rules"], r["projects"], r["question_version"], r["dev"], r["test"]),
        "labels: %s; grading with %s (0 generic: %s, 1 practice: %s, 2 project-specific: %s)"
        % (", ".join("%s %d" % (k, v) for k, v in r["labelled"].items()) or "none",
           r["rater"], r["label_counts"]["0"], r["label_counts"]["1"],
           r["label_counts"]["2"]),
        "scored: %s" % ", ".join("%s %d" % (k, v) for k, v in r["scored"].items()),
        "",
        "%-17s%-20s%s" % ("", "AUC generic-vs-rest", "AUC project-specific-vs-rest"),
        "%-17s%5s %5s %5s   %5s %5s %5s   n"
        % ("signal", "dev", "test", "all", "dev", "test", "all"),
    ]
    for name in sorted(r["auc"]):
        row = r["auc"][name]
        lines.append("%-17s%s %s %s   %s %s %s   %d" % (
            "  " + name + (" *" if name == BASELINE else ""),
            _fmt(row["dev"]["auc_generic"]), _fmt(row["test"]["auc_generic"]),
            _fmt(row["all"]["auc_generic"]),
            _fmt(row["dev"]["auc_project_specific"]),
            _fmt(row["test"]["auc_project_specific"]),
            _fmt(row["all"]["auc_project_specific"]), row["all"]["rules"]))
    lines.append("* = local, needs no request; 0.500 is a coin flip both ways")
    lines.append("")
    lines.append("flagging generic with %r: threshold fixed on dev, read once on test."
                 % r["variant"])
    lines.append("\"wrongly\" counts project-specific rules the flag would throw away.")
    lines.append("             dev: flagged  generic  wrongly | test: flagged  generic  wrongly")
    for dev, test in zip(r["thresholds"]["dev"], r["thresholds"]["test"]):
        lines.append("  >= %.2f %14d %8s %8d | %12d %8s %8d" % (
            dev["threshold"], dev["flagged"],
            "%d/%d" % (dev["generic_flagged"], dev["generic"]), dev["project_flagged"],
            test["flagged"], "%d/%d" % (test["generic_flagged"], test["generic"]),
            test["project_flagged"]))
    if r["agreement"]:
        a = r["agreement"]
        lines.append("")
        lines.append("raters %s and %s on the %d rules both labelled: exact %d, off by two %d"
                     % (r["agreement_between"][0], r["agreement_between"][1],
                        a["rules"], a["exact"], a["off_by_two"]))
        lines.append("  " + "  ".join("%s %d" % (k, v)
                                      for k, v in a["confusion"].items() if v))
        lines.append("  no number above is tighter than this.")
    return "\n".join(lines)


# --- the vault side: everything below reads mnemo, nothing above does -------


def labels_name(rater: str) -> str:
    """One file per rater, and the rater's name in it: a model's labels are a
    judge, never to be read as a person's. ``label_recall_pairs``'s spelling."""
    safe = "".join(c for c in rater.lower() if c.isalnum() or c in "-_") or "human"
    return "generic-labels-%s.json" % safe


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _rater_files(vault: Path) -> Dict[str, Dict[str, int]]:
    """Every rater's labels on disk, by the name each file carries."""
    out: Dict[str, Dict[str, int]] = {}
    for path in sorted((vault / ".mnemo").glob("generic-labels-*.json")):
        data = _read_json(path)
        labels = data.get("labels") or {}
        if labels:
            out[str(data.get("rater") or path.stem)] = {
                str(k): int(v) for k, v in labels.items()}
    return out


def _candidates(vault: Path) -> List[Dict[str, Any]]:
    """Every live rule a rater could be shown, with the text it would be shown as.

    Read through ``read_mnemo_rule`` at ``scope="vault"`` rather than off the
    index's ``body_preview``, because the preview is truncated at a different
    length than :func:`~mnemo.core.mcp.rerank.rule_text` and a rater must see
    what the judge sees. A retired rule is not a candidate: it is already gone
    from every list.
    """
    from mnemo.core import rule_activation
    from mnemo.core.mcp import rerank, tools

    index = rule_activation.load_index(vault)
    if index is None or "rules" not in index:
        index = rule_activation.build_index(vault)
    out: List[Dict[str, Any]] = []
    for slug, rule in sorted(index.get("rules", {}).items()):
        if rule.get("type") not in LIVE_TYPES:
            continue
        page = tools.read_mnemo_rule(vault, slug, scope="vault")
        if page is None or page.get("retired"):
            continue
        text = rerank.rule_text(page.get("body") or "")
        if not text:
            continue
        out.append({"slug": slug, "name": str(page.get("name") or slug),
                    "type": rule.get("type"), "projects": rule.get("projects") or [],
                    "rule": text})
    return out


def _load_sample(vault: Path) -> List[Dict[str, Any]]:
    data = _read_json(vault / ".mnemo" / SAMPLE_NAME)
    if not data:
        raise SystemExit("error: no sample at %s; run with --sample first"
                         % (vault / ".mnemo" / SAMPLE_NAME))
    if data.get("question_version") != QUESTION_VERSION:
        raise SystemExit("error: %s was drawn for question version %r, this tool is %d"
                         % (SAMPLE_NAME, data.get("question_version"), QUESTION_VERSION))
    return data["rules"]


def _do_sample(vault: Path, args: argparse.Namespace) -> int:
    path = vault / ".mnemo" / SAMPLE_NAME
    held = {name: len(labels) for name, labels in _rater_files(vault).items()}
    if held:
        print("error: %s already holds labels (%s); move them away to draw again"
              % (vault / ".mnemo", ", ".join("%s %d" % kv for kv in sorted(held.items()))),
              file=sys.stderr)
        return 1
    candidates = _candidates(vault)
    rules = draw(candidates, per_project=args.per_project, seed=args.seed)
    if not rules:
        print("error: no live rules in %s" % vault, file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {
        "drawn_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed, "per_project": args.per_project,
        "question_version": QUESTION_VERSION,
        "types": list(LIVE_TYPES),
        "scale": "; ".join("%d %s: %s" % level for level in LEVELS),
        "candidates": len(candidates),
        "rules": rules})
    per_project: Dict[str, int] = {}
    for rule in rules:
        per_project[rule["project"]] = per_project.get(rule["project"], 0) + 1
    print("%d of %d live rules, %d projects -> %s"
          % (len(rules), len(candidates), len(per_project), path))
    print("  " + "  ".join("%s %d" % kv for kv in sorted(per_project.items())))
    return 0


def _do_export(vault: Path, rules: List[Dict[str, Any]], args: argparse.Namespace) -> int:
    out = Path(args.export_blind)
    out.mkdir(parents=True, exist_ok=True)
    done = _rater_files(vault).get(args.rater, {})
    chunks = blind_chunks(rules, done)
    for i, chunk in enumerate(chunks, start=1):
        _write_json(out / ("chunk-%03d.json" % i), chunk)
    _write_json(out / "scale.json", [
        {"label": value, "name": name, "means": meaning} for value, name, meaning in LEVELS])
    print("%d rules in %d chunk file(s) -> %s\n"
          "the rater answers each chunk-NNN.json with a chunk-NNN.labels.json next to "
          "it: {\"<id>\": 0|1|2}; scale.json holds the three levels"
          % (sum(len(c) for c in chunks), len(chunks), out))
    return 0


def _do_import(vault: Path, rules: List[Dict[str, Any]], args: argparse.Namespace) -> int:
    source = Path(args.import_labels)
    asked: List[str] = []
    for path in sorted(source.glob("chunk-*.json")):
        if path.name.endswith(".labels.json"):
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            asked.append(str(item["id"]))
    if not asked:
        print("error: no chunk-*.json in %s; --export-blind writes them" % source,
              file=sys.stderr)
        return 1
    given: Dict[str, Any] = {}
    for path in sorted(source.glob("*.labels.json")):
        given.update(json.loads(path.read_text(encoding="utf-8")))
    folded, problems = imported_labels(asked, given, [r["id"] for r in rules])
    if problems:
        print("error: %d problem(s) with the labels in %s; nothing written"
              % (len(problems), source), file=sys.stderr)
        for problem in problems[:10]:
            print("  " + problem, file=sys.stderr)
        return 1
    path = vault / ".mnemo" / labels_name(args.rater)
    labels = (_read_json(path).get("labels") or {})
    labels.update(folded)
    _write_json(path, {
        "rater": args.rater, "blind": True,
        "scale": "; ".join("%d %s: %s" % level for level in LEVELS),
        "question_version": QUESTION_VERSION,
        "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_file": SAMPLE_NAME, "labels": labels})
    print("%d labels (%d new) -> %s" % (len(labels), len(folded), path))
    return 0


def _do_score(vault: Path, rules: List[Dict[str, Any]], args: argparse.Namespace) -> int:
    from mnemo.core.mcp import rerank

    path = vault / ".mnemo" / SCORES_NAME
    saved = _read_json(path)
    done = saved.get(args.variant) or {}
    batches = pending(rules, done, size=CHUNK)
    cost = estimate(args.variant, batches)
    if not args.send:
        print("dry run: %d of %d rules still to score under %r in %d request(s), "
              "~%d tokens (~$%.4f). --send posts them to %s"
              % (cost["rules"], len(rules), args.variant, cost["requests"],
                 cost["tokens"], cost["usd"], rerank.TYPESAFE_URL))
        return 0
    chosen = rerank.settings(None)
    key, source = rerank.resolve_key(chosen)
    if not key:
        print("error: --score --send found no key: set %s or run `mnemo rerank --setup`"
              % chosen["keyEnv"], file=sys.stderr)
        return 1
    client = rerank.typesafe_client(key, model=chosen["model"], timeout=120.0)
    got = failed = 0
    for batch in batches:
        try:
            answered = ask(args.variant, batch, client)
        except Exception:  # noqa: BLE001 — one dead request must not lose what is paid for
            failed += 1
            continue
        done.update(answered)
        got += len(answered)
        saved[args.variant] = done
        # Under its own key, never beside the variants: the file is read back
        # as {variant: {id: answer}} and a stray string there would be a
        # variant nobody asked a question for.
        saved["_meta"] = {"model": chosen["model"], "question_version": QUESTION_VERSION,
                          "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _write_json(path, saved)
    print("scored %d of %d pending rules under %r (key from %s), %d failed request(s) -> %s"
          % (got, cost["rules"], args.variant, source, failed, path))
    return 1 if failed else 0


def thresholds(raw: str) -> Tuple[float, ...]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(float(p) for p in parts) if parts else THRESHOLDS


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true",
                        help="draw the rules to label and score (once, local)")
    parser.add_argument("--per-project", type=int, default=PER_PROJECT,
                        help="rules drawn per project (default: %d)" % PER_PROJECT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--export-blind", metavar="DIR",
                        help="write blind chunk files for a rater (local)")
    parser.add_argument("--import-labels", metavar="DIR",
                        help="read the rater's *.labels.json back from DIR (local)")
    parser.add_argument("--rater", default="human", help="whose labels to read or write")
    parser.add_argument("--score", action="store_true",
                        help="ask the judge for an answer per rule (dry run without --send)")
    parser.add_argument("--send", action="store_true",
                        help="with --score: post the rules to TypeSafe (third party)")
    parser.add_argument("--variant", default=PRIMARY, choices=sorted(VARIANTS),
                        help="which question wording (default: %s)" % PRIMARY)
    parser.add_argument("--thresholds", default="",
                        help="comma-separated genericness bars for the flag table")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli

    vault = cli._resolve_vault()
    if args.sample:
        return _do_sample(vault, args)

    rules = _load_sample(vault)
    if args.export_blind:
        return _do_export(vault, rules, args)
    if args.import_labels:
        return _do_import(vault, rules, args)
    if args.score:
        return _do_score(vault, rules, args)

    raters = _rater_files(vault)
    if not raters:
        print("error: no labels in %s; --export-blind then --import-labels"
              % (vault / ".mnemo"), file=sys.stderr)
        return 1
    if args.rater not in raters:
        print("error: no labels from %r; have %s"
              % (args.rater, ", ".join(sorted(raters))), file=sys.stderr)
        return 1
    scores = _read_json(vault / ".mnemo" / SCORES_NAME)
    result = report(rules, raters, scores, rater=args.rater,
                    thresholds=thresholds(args.thresholds), variant=args.variant)
    print(json.dumps(result, indent=2) if args.json else format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
