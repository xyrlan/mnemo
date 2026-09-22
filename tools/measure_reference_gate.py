"""How much junk does the reference gate stop, and how many good pages does it cost? (#417)

Usage:
    PYTHONPATH=src python3 tools/measure_reference_gate.py --score --model claude-sonnet-5
    PYTHONPATH=src python3 tools/measure_reference_gate.py --score --model claude-haiku-4-5
    PYTHONPATH=src python3 tools/measure_reference_gate.py            # report, local

The labelled set is the 2026-09-22 vault audit (``<vault>/.mnemo/audit-2026-09-22``,
``--audit DIR`` for another): 150 live ``reference``+``feedback`` rules drawn
uniformly at random, each rated blind by two raters into G (generic aphorism),
T (transferable technique), S (knowledge about one system) or N (narrative).
Files read from it:

- ``sample.json`` — ``[{"id", "text"}]``, the name + body each rater saw;
- ``labels-A.json``, ``labels-B.json`` — ``{id: {"cat": "G|T|S|N"}}``;
- ``key.json`` — ``[[id, slug]]``, only to tell reference rows from feedback.

A row counts as **junk** when both raters said G or N and as **good** when
both said T or S; the 8 rows the raters split on are left out, because
neither answer is the truth for them.

``--score`` asks the gate — :data:`reference_gate.SYSTEM_PROMPT`,
:func:`reference_gate.build_prompt`, :func:`reference_gate.parse_verdicts`,
the stage's own code, not a copy — through ``core.llm``'s configured provider
(the same ``claude`` binary extraction uses), ten rules a call as a chunk of
extraction would batch them. Answers land in ``reference-gate-scores.json`` in
the audit dir under ``<model>@<prompt hash>``: a changed prompt starts a new
column instead of mixing with the old one. It resumes: rows already answered
are not asked again.

The split is by the id's parity (``int(id, 16) % 2 == 0`` is dev). The G and
T wording in the prompt was sharpened reading dev rows only; test was scored
after, once. Read the test column as the claim and dev as how it was reached.

Two baselines are printed next to the judge, so it is graded against
something: staging every inferred reference page (what routing all of them to
``_inbox`` costs), and the local ``named_things`` count from
``measure_generic_rules`` (a rule that names nothing is held).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent

AUDIT_DIR = "audit-2026-09-22"
SCORES_NAME = "reference-gate-scores.json"
CHUNK = 10
JUNK, GOOD = "junk", "good"


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt_hash(system_prompt: str) -> str:
    """Eight hex chars naming the question asked; a new wording is a new column."""
    return hashlib.sha1(system_prompt.encode("utf-8")).hexdigest()[:8]


def column(model: str, system_prompt: str) -> str:
    return "%s@%s" % (model, prompt_hash(system_prompt))


def part_of(id_: str) -> str:
    """dev or test, by the id's parity alone: a locked split, not a seed."""
    return "dev" if int(id_, 16) % 2 == 0 else "test"


def _labels(raw: Any) -> Dict[str, str]:
    """``{id: cat}`` from a rater file, either ``{id: {...}}`` or ``[[id, {...}]]``."""
    items = raw.items() if isinstance(raw, dict) else raw
    return {str(i): str(v["cat"]).upper() for i, v in items}


def consensus(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """junk when both raters said G/N, good when both said T/S, else None."""
    if a is None or b is None:
        return None
    if a in "GN" and b in "GN":
        return JUNK
    if a in "TS" and b in "TS":
        return GOOD
    return None


def confusion(truth: Dict[str, str], held: Dict[str, bool],
              ids: Iterable[str]) -> Dict[str, int]:
    """Counts of junk/good rows held back and let through, over *ids*."""
    out = {"junk_held": 0, "junk_live": 0, "good_held": 0, "good_live": 0}
    for i in ids:
        t = truth.get(i)
        if t is None or i not in held:
            continue
        out["%s_%s" % (t, "held" if held[i] else "live")] += 1
    return out


def summarize(c: Dict[str, int]) -> Dict[str, Optional[float]]:
    """Recall on junk, share of good pages lost, precision of a hold, junk left live."""
    junk = c["junk_held"] + c["junk_live"]
    good = c["good_held"] + c["good_live"]
    held = c["junk_held"] + c["good_held"]
    live = c["junk_live"] + c["good_live"]
    return {
        "junk_recall": c["junk_held"] / junk if junk else None,
        "good_lost": c["good_held"] / good if good else None,
        "hold_precision": c["junk_held"] / held if held else None,
        "live_junk_share": c["junk_live"] / live if live else None,
        "base_junk_share": junk / (junk + good) if junk + good else None,
    }


def held_by_verdict(verdicts: Dict[str, Optional[str]], keep: Iterable[str]) -> Dict[str, bool]:
    """The stage's rule: a row is held unless its verdict is in *keep*."""
    keep = set(keep)
    return {i: (v not in keep) for i, v in verdicts.items()}


def chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for k in range(0, len(items), size):
        yield items[k:k + size]


def score(sample: List[Dict[str, str]], existing: Dict[str, Optional[str]],
          ask, gate) -> Dict[str, Optional[str]]:
    """Ask *ask* for every row not in *existing*, CHUNK rows a call.

    ``ask(prompt) -> text``; the prompt and the parse are *gate*'s own.
    Returns the merged answers; a row the judge gave no answer for is stored
    as ``None`` and counts as held, the stage's failure direction.
    """
    out = dict(existing)
    todo = [r for r in sample if r["id"] not in out]
    for batch in chunks(todo, CHUNK):
        text = ask(gate.build_prompt([r["text"] for r in batch]))
        for r, cat in zip(batch, gate.parse_verdicts(text, len(batch))):
            out[r["id"]] = cat
    return out


def _fmt(x: Optional[float]) -> str:
    return "   -" if x is None else "%3.0f%%" % (100 * x)


def report_lines(truth: Dict[str, str], signals: Dict[str, Dict[str, bool]],
                 parts: Dict[str, str], reference_ids: Optional[set] = None) -> List[str]:
    """One table: a row per (signal, slice), counts and rates."""
    slices: List[Tuple[str, Iterable[str]]] = [
        ("test", [i for i in truth if parts[i] == "test"]),
        ("dev", [i for i in truth if parts[i] == "dev"]),
        ("all", list(truth)),
    ]
    if reference_ids:
        slices.append(("all, reference only", [i for i in truth if i in reference_ids]))
    lines = [
        "%-34s %-20s %9s %9s %6s %6s %6s %9s" % (
            "signal", "slice", "junk held", "good held", "recall", "lost",
            "prec", "live junk"),
    ]
    for name, held in signals.items():
        for label, ids in slices:
            c = confusion(truth, held, ids)
            s = summarize(c)
            lines.append("%-34s %-20s %4d/%-4d %4d/%-4d %6s %6s %6s %4s->%s" % (
                name, label,
                c["junk_held"], c["junk_held"] + c["junk_live"],
                c["good_held"], c["good_held"] + c["good_live"],
                _fmt(s["junk_recall"]), _fmt(s["good_lost"]), _fmt(s["hold_precision"]),
                _fmt(s["base_junk_share"]).strip(), _fmt(s["live_junk_share"]).strip(),
            ))
    return lines


# --------------------------------------------------------------- vault side

def _vault_root() -> Path:
    from mnemo.core import config, paths
    return paths.vault_root(config.load_config())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load(audit: Path):
    sample = _read_json(audit / "sample.json")
    a = _labels(_read_json(audit / "labels-A.json"))
    b = _labels(_read_json(audit / "labels-B.json"))
    truth = {}
    for r in sample:
        t = consensus(a.get(r["id"]), b.get(r["id"]))
        if t is not None:
            truth[r["id"]] = t
    return sample, truth


def _reference_ids(audit: Path, vault: Path) -> set:
    key_path = audit / "key.json"
    if not key_path.exists():
        return set()
    key = _read_json(key_path)
    items = key.items() if isinstance(key, dict) else key
    return {i for i, slug in items
            if (vault / "shared" / "reference" / (slug + ".md")).exists()}


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm
    from mnemo.core.extract import reference_gate as gate

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--audit", type=Path, help="labelled audit directory")
    ap.add_argument("--score", action="store_true", help="ask the judge (model calls)")
    ap.add_argument("--model", default=None, help="model to score with (default: config)")
    args = ap.parse_args(argv)

    vault = _vault_root()
    audit = args.audit or vault / ".mnemo" / AUDIT_DIR
    sample, truth = _load(audit)
    scores_path = audit / SCORES_NAME
    scores: Dict[str, Dict[str, Optional[str]]] = (
        _read_json(scores_path) if scores_path.exists() else {}
    )

    if args.score:
        cfg = config.load_config()
        model = args.model or cfg["extraction"]["referenceGate"]["model"]
        provider = llm.resolve(cfg)
        col = column(model, gate.SYSTEM_PROMPT)

        def ask(prompt: str) -> str:
            return provider(prompt, system=gate.SYSTEM_PROMPT, model=model,
                            timeout=int(cfg["extraction"]["subprocessTimeout"])).text

        scores[col] = score(sample, scores.get(col, {}), ask, gate)
        scores_path.write_text(json.dumps(scores, indent=1, sort_keys=True), encoding="utf-8")
        print("scored %d rows under %s -> %s" % (len(scores[col]), col, scores_path))

    mgr = _sibling("measure_generic_rules")
    text = {r["id"]: r["text"] for r in sample}
    signals: Dict[str, Dict[str, bool]] = {
        "stage every inferred page": {i: True for i in truth},
        "named_things == 0": {i: not mgr.named_things(text[i]) for i in truth},
    }
    current = prompt_hash(gate.SYSTEM_PROMPT)
    for col in sorted(scores):
        tag = "" if col.endswith("@" + current) else "  (old prompt)"
        signals["judge " + col + tag] = held_by_verdict(scores[col], gate.KEEP)

    parts = {i: part_of(i) for i in truth}
    n_junk = sum(1 for t in truth.values() if t == JUNK)
    print("labelled: %d rows, %d junk (both raters G/N), %d good (both T/S), "
          "%d split and left out" % (len(truth), n_junk, len(truth) - n_junk,
                                     len(sample) - len(truth)))
    print("held = staged in shared/_inbox/; recall = junk held / junk; "
          "lost = good held / good; prec = junk / held; live junk = junk share "
          "before -> after the gate\n")
    for line in report_lines(truth, signals, parts, _reference_ids(audit, vault)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
