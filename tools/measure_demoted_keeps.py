"""Does the reference gate's system/technique verdict hold on the pages the evidence gate demoted? (#465)

Usage:
    PYTHONPATH=src python3 tools/measure_demoted_keeps.py --dry-run   # population, sample, calls, cost; calls nothing
    PYTHONPATH=src python3 tools/measure_demoted_keeps.py --send      # both raters label what is pending (model calls)
    PYTHONPATH=src python3 tools/measure_demoted_keeps.py             # report, local
    PYTHONPATH=src python3 tools/measure_demoted_keeps.py --json

Measurement A1 of the second-user round (PR #464). **Measure only**: nothing
here promotes, stamps or edits a page; every write goes under
``<vault>/.mnemo/demoted-keeps/``.

The population. A page staged in ``shared/_inbox/`` that carries
``demoted_from: feedback`` (the extractor called it feedback, the evidence
gate found no user quote for it) and ``reference_gate: system`` or
``technique`` (the reference gate, #432, would have let it go live had it
been emitted as reference). A reference page emitted directly with the same
verdict goes live (#425); these wait for a human indefinitely. Promoting them
*as reference* makes no "the user said this" claim — but first, does the S/T
verdict hold on this population? On the 2026-09-22 audit's held-out half the
gate let 1 of 31 junk pages through and kept 38 of 41 good ones
(``tools/measure_reference_gate.py``); demotions may be a harder population.

**The bar, declared in #465 before any label existed: promotion goes ahead
only if >= 85% of the sample is labelled good by both raters.** It is
:data:`BAR`, it reads the point estimate of the both-good rate, and it is not
to move after labels are seen. The 95% interval is printed next to it so a
reader can see how much the sample size leaves open.

How it is set up:

- the population is read from frontmatter, plain ``.md`` in
  ``shared/_inbox/<type>/`` (no ``.proposed.md`` sibling); its size is printed;
- :data:`SAMPLE_SIZE` pages are drawn uniformly with :data:`SEED` from the
  population sorted by id, and frozen with the text raters see into
  ``sample.json``. A redraw is refused once any label exists;
- the text is :func:`reference_gate.view` of the name and the retrieval body
  — the view the gate itself judged and the 09-22 raters labelled, so the
  raters and the gate disagree about a page, not about what they were shown.
  Nothing in it names the gate, its verdict, the demotion or the other rater;
- two raters, :data:`RATERS`, each a separate ``claude --print`` call with no
  shared context; the second reads the pages in reverse order, as the 09-22
  audit's second rater did. Neither is the gate's own model
  (``claude-sonnet-5``), so a rater is not the gate asked twice. Both are
  Claude models: their agreement bounds reliability, not truth;
- the definitions are the gate's (:data:`reference_gate.CATEGORIES`): junk is
  G (generic) or N (narrative); good is S (system) or T (technique) **and
  correct as far as the page shows** — a page whose own text shows it wrong
  is W, junk. The raters answer a letter, so the report can say *why* junk;
- at most :data:`MAX_CALLS` model calls ever, counted across reruns in
  ``labels.json``; labels are saved after every call and a rerun asks only
  for what is still missing.

Report: good rate per rater and under both, each with a 95% Wilson interval
(no finite-population correction: the sample is 40 of ~67, so the true
interval is narrower than printed), Cohen's kappa on good/junk, the verdict
against :data:`BAR`, and — after the fact, never shown to a rater — the both-
good rate split by the gate's own S/T verdict.

First run, 2026-09-23: population 67, sample 40 (32 system, 8 technique), 8
calls, API-price equivalent $0.81 (dry run said ~$1.37). Good per rater:
``claude-opus-5-5`` 32/40 = 80.0% [65.2, 89.5], ``claude-fable-5-1`` 33/40 =
82.5% [68.0, 91.3]; under both **29/40 = 72.5% [57.2, 83.9]**; exact
agreement 33/40, kappa 0.43. **Verdict: FAIL against the 85% bar** — and the
interval's top is under the bar too. Junk was almost all G (generic, 7 per
rater), one W, no N. By the gate's own verdict: system 24/32, technique 5/8.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OUT_DIR = "demoted-keeps"
SAMPLE_NAME = "sample.json"
LABELS_NAME = "labels.json"

#: Declared in #465 before any page was labelled. Do not move it.
BAR = 0.85
SAMPLE_SIZE = 40
SEED = 465
#: The issue's budget, over every run of this tool against one sample.
MAX_CALLS = 20
#: Pages per rater call: 40 pages, two raters, 8 calls — 12 left for retries.
BATCH = 10

#: The gate verdicts that define the population.
KEEP_VERDICTS = ("system", "technique")
DEMOTED_FROM = "feedback"

#: Two raters, neither the gate's own model. The second reads in reverse order.
RATERS = ("claude-opus-5-5", "claude-fable-5-1")

GOOD, JUNK = "good", "junk"
#: A rater's letter and what it counts as. W is the one thing the gate's
#: categories do not ask: the page's own text shows it to be wrong.
LETTERS = {"S": GOOD, "T": GOOD, "G": JUNK, "N": JUNK, "W": JUNK}

#: API list price per MTok (in, out), for the dry run only; a real run prints
#: the CLI's own ``total_cost_usd``.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-fable-5-1": (10.0, 50.0)}
#: What ``claude --print`` adds to every call's input (measure_rule_lift).
CLI_OVERHEAD_TOKENS = 1000
#: Output per call: the JSON (~12 tokens a page) plus room for thinking,
#: which both raters do and cannot turn off. A ceiling, not a guess.
OUT_TOKENS_PER_PAGE = 12
THINKING_TOKENS_PER_CALL = 4000


def _gate() -> Any:
    from mnemo.core.extract import reference_gate
    return reference_gate


def rater_system() -> str:
    """The rater's instruction, built from the gate's own category text."""
    cats = _gate().CATEGORIES
    return (
        "You audit entries in an engineering knowledge vault. Each entry was "
        "written by a model from one coding session. Decide whether each entry "
        "is worth keeping as a reference an engineer would look up. Answer one "
        "letter per entry:\n"
        "- S: %s\n"
        "- T: %s\n"
        "- G: %s\n"
        "- N: %s\n"
        "- W: specific, but the entry's own text shows it is wrong or "
        "contradicts itself\n"
        "S and T mean the entry is good: specific, and correct as far as the "
        "entry itself shows. G, N and W mean junk. Judge what the entry "
        "teaches, not its formatting: naming a file or a number does not make "
        "generic advice specific, and a **Why:** / **How to apply:** structure "
        "does not make a narrative into guidance.\n\n"
        "Output ONLY JSON: {\"labels\": [{\"i\": <entry number>, \"cat\": "
        "\"S|T|G|N|W\"}, ...]} with one label per entry. No prose."
        % (cats["S"], cats["T"], cats["G"], cats["N"])
    )


def column(model: str) -> str:
    """Where one rater's labels are filed: model plus a hash of the instruction."""
    return "%s@%s" % (model, hashlib.sha256(rater_system().encode("utf-8")).hexdigest()[:8])


# --- the pure part ---------------------------------------------------------------

def in_population(fm: Dict[str, Any]) -> bool:
    """``demoted_from: feedback`` and a gate verdict of system or technique."""
    demoted = str(fm.get("demoted_from") or "").strip().lower()
    verdict = str(fm.get("reference_gate") or "").strip().lower()
    return demoted == DEMOTED_FROM and verdict in KEEP_VERDICTS


def draw(population: Sequence[Dict[str, Any]], *, size: int = SAMPLE_SIZE,
         seed: int = SEED) -> List[Dict[str, Any]]:
    """A uniform sample without replacement, reproducible from *seed*.

    The population is sorted by id first, so the draw depends on which pages
    exist and not on the order a directory listing returned them in.
    """
    ordered = sorted(population, key=lambda r: r["id"])
    if len(ordered) <= size:
        return ordered
    return random.Random(seed).sample(ordered, size)


def blind(row: Dict[str, Any]) -> str:
    """The only thing a rater sees of a page."""
    return row["text"]


def rater_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    lines = ["Entries:"]
    for i, row in enumerate(batch, 1):
        lines.append("[%d] %s" % (i, blind(row)))
    return "\n\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_labels(text: str, batch: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """``{page id: letter}`` for every entry the answer labelled validly."""
    out: Dict[str, str] = {}
    match = _JSON_RE.search(text or "")
    if not match:
        return out
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return out
    labels = payload.get("labels") if isinstance(payload, dict) else None
    if not isinstance(labels, list):
        return out
    for item in labels:
        if not isinstance(item, dict):
            continue
        i, cat = item.get("i"), str(item.get("cat") or "").strip().upper()
        if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= len(batch) and cat in LETTERS:
            out[batch[i - 1]["id"]] = cat
    return out


def order_for(rater_index: int, sample: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The first rater reads the sample in order, the second in reverse."""
    return list(sample) if rater_index % 2 == 0 else list(reversed(sample))


def batches(rater_index: int, sample: Sequence[Dict[str, Any]], done: Dict[str, str],
            size: int = BATCH) -> List[List[Dict[str, Any]]]:
    todo = [r for r in order_for(rater_index, sample) if r["id"] not in done]
    return [todo[i:i + size] for i in range(0, len(todo), size)]


def plan(sample: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, str]],
         raters: Sequence[str] = RATERS) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Every pending call, rater by rater: (model, batch)."""
    out = []
    for k, model in enumerate(raters):
        for b in batches(k, sample, labels.get(column(model), {})):
            out.append((model, b))
    return out


def estimate(todo: Sequence[Tuple[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
    system_tokens = len(rater_system()) // 4
    calls, usd, tok_in, tok_out = 0, 0.0, 0, 0
    for model, batch in todo:
        t_in = len(rater_prompt(batch)) // 4 + system_tokens + CLI_OVERHEAD_TOKENS
        t_out = len(batch) * OUT_TOKENS_PER_PAGE + THINKING_TOKENS_PER_CALL
        p_in, p_out = PRICES.get(model, (10.0, 50.0))
        calls += 1
        tok_in += t_in
        tok_out += t_out
        usd += (t_in * p_in + t_out * p_out) / 1e6
    return {"calls": calls, "input_tokens": tok_in, "output_tokens": tok_out, "usd": usd,
            "pages": sum(len(b) for _, b in todo)}


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def kappa(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    """Cohen's kappa for two raters' good/junk over the same pages."""
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(x == GOOD for x in a) / n, sum(y == GOOD for y in b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected >= 1:
        return None
    return (observed - expected) / (1 - expected)


def _rate(k: int, n: int) -> Dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci": [lo, hi]}


def report(sample: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, str]],
           cols: Sequence[str], *, bar: float = BAR) -> Dict[str, Any]:
    """Every number #465 asks for, from saved labels only."""
    ids = [r["id"] for r in sample]
    per: Dict[str, Any] = {}
    for col in cols:
        got = labels.get(col, {})
        mine = [got[i] for i in ids if i in got]
        cats: Dict[str, int] = {}
        for c in mine:
            cats[c] = cats.get(c, 0) + 1
        per[col] = dict(_rate(sum(LETTERS[c] == GOOD for c in mine), len(mine)), cats=cats)
    a, b = (labels.get(c, {}) for c in cols[:2]) if len(cols) >= 2 else ({}, {})
    shared = [i for i in ids if i in a and i in b]
    ga = [LETTERS[a[i]] for i in shared]
    gb = [LETTERS[b[i]] for i in shared]
    both_good = [i for i in shared if LETTERS[a[i]] == GOOD and LETTERS[b[i]] == GOOD]
    both = _rate(len(both_good), len(shared))
    complete = len(shared) == len(ids) and bool(ids)
    if not complete:
        verdict = "incomplete: %d of %d pages labelled by both raters" % (len(shared), len(ids))
    elif both["rate"] >= bar:
        verdict = "PASS: %.1f%% >= %.0f%% — promotion may go ahead" % (100 * both["rate"], 100 * bar)
    else:
        verdict = "FAIL: %.1f%% < %.0f%% — do not promote on the gate's verdict" % (
            100 * both["rate"], 100 * bar)
    by_gate: Dict[str, Any] = {}
    for g in KEEP_VERDICTS:
        these = [i for i in shared if next(r for r in sample if r["id"] == i).get("gate") == g]
        by_gate[g] = _rate(sum(i in both_good for i in these), len(these))
    rows = {r["id"]: r for r in sample}
    return {
        "population": None, "sample": len(ids), "bar": bar,
        "raters": per, "both_good": both, "verdict": verdict, "complete": complete,
        "agreement": {"n": len(shared), "exact": (sum(x == y for x, y in zip(ga, gb)) / len(shared))
                      if shared else None, "kappa": kappa(ga, gb)},
        "by_gate": by_gate,
        "not_both_good": [{"id": i, "a": a[i], "b": b[i], "gate": rows[i].get("gate"),
                           "text": rows[i]["text"][:100]}
                          for i in shared if i not in both_good],
    }


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _rate_line(r: Dict[str, Any]) -> str:
    lo, hi = r["ci"]
    return "%d/%d = %s [%s, %s]" % (r["k"], r["n"], _pct(r["rate"]), _pct(lo), _pct(hi))


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = [
        "population: %s staged pages with demoted_from: feedback and reference_gate: "
        "system|technique" % data["population"],
        "sample: %d, uniform, seed %d" % (data["sample"], SEED),
        "",
        "good (S/T) per rater, 95% Wilson interval:",
    ]
    for col, r in data["raters"].items():
        lines.append("  %-30s %s   %s" % (col, _rate_line(r), " ".join(
            "%s %d" % kv for kv in sorted(r["cats"].items()))))
    ag = data["agreement"]
    lines += [
        "good under both raters:          %s" % _rate_line(data["both_good"]),
        "agreement on good/junk: %d pages, exact %s, kappa %s"
        % (ag["n"], _pct(ag["exact"]), "n/a" if ag["kappa"] is None else "%.2f" % ag["kappa"]),
        "",
        "bar (declared in #465): >= %.0f%% good under both raters" % (100 * data["bar"]),
        "verdict: " + data["verdict"],
        "",
        "after the fact — both-good by the gate's own verdict (never shown to a rater):",
    ]
    for g, r in data["by_gate"].items():
        lines.append("  %-10s %s" % (g, _rate_line(r)))
    if data["not_both_good"]:
        lines += ["", "not good under both (rater A / rater B / gate):"]
        for row in data["not_both_good"]:
            lines.append("  %s/%s %-9s %-55s %s" % (row["a"], row["b"], row["gate"], row["id"],
                                                    row["text"]))
    return lines


# --- the vault ----------------------------------------------------------------------

def population(vault: Path) -> List[Dict[str, Any]]:
    """Every staged page in the population, as a rater would read it.

    Read-only: this opens pages and never writes one.
    """
    from mnemo.core.filters import is_proposed_sibling, iter_staged_pages, parse_frontmatter
    from mnemo.core.extract.scanner import parse_frontmatter as split_frontmatter
    from mnemo.core.text_utils import retrieval_body

    gate = _gate()
    rows = []
    for path in iter_staged_pages(Path(vault)):
        if is_proposed_sibling(path) or path.suffix != ".md":
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(raw) or {}
        if not in_population(fm):
            continue
        _, body = split_frontmatter(raw)
        rows.append({
            "id": "%s/%s" % (path.parent.name, path.stem),
            "text": gate.view(str(fm.get("name") or path.stem), retrieval_body(body)),
            "gate": str(fm.get("reference_gate")).strip().lower(),
        })
    return sorted(rows, key=lambda r: r["id"])


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8")


def freeze(out: Path, vault: Path, store: Dict[str, Any]) -> Dict[str, Any]:
    """The frozen sample, drawn now if it does not exist yet."""
    path = out / SAMPLE_NAME
    if path.exists():
        return _read(path, {})
    if any(store.get("labels", {}).values()):
        raise SystemExit("error: %s holds labels but %s is gone; refusing to redraw"
                         % (out / LABELS_NAME, path))
    pop = population(vault)
    frozen = {
        "drawn_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": SEED, "population_size": len(pop),
        "population_ids": [r["id"] for r in pop],
        "sample": draw(pop),
    }
    out.mkdir(parents=True, exist_ok=True)
    _write(path, frozen)
    return frozen


Ask = Callable[[str, str, str], Tuple[str, float]]


def send(todo: Sequence[Tuple[str, List[Dict[str, Any]]]], store: Dict[str, Any], ask: Ask,
         save: Callable[[Dict[str, Any]], None], *, max_calls: int = MAX_CALLS) -> List[str]:
    """Ask each pending call until the budget is spent; save after every call.

    ``ask(prompt, system, model)`` returns (text, API-price usd). The call
    count lives in *store*, so the budget holds across reruns.
    """
    log = []
    system = rater_system()
    for model, batch in todo:
        if store.get("calls", 0) >= max_calls:
            log.append("budget spent: %d of %d calls used; stopping" % (store["calls"], max_calls))
            break
        text, usd = ask(rater_prompt(batch), system, model)
        store["calls"] = store.get("calls", 0) + 1
        store["usd"] = store.get("usd", 0.0) + usd
        got = parse_labels(text, batch)
        store.setdefault("labels", {}).setdefault(column(model), {}).update(got)
        save(store)
        log.append("call %d/%d %s: %d of %d labelled" % (
            store["calls"], max_calls, model, len(got), len(batch)))
    return log


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="population, sample, pending calls and cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="label every pending page (model calls)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    out = vault / ".mnemo" / OUT_DIR
    labels_path = out / LABELS_NAME
    store: Dict[str, Any] = _read(labels_path, {"calls": 0, "usd": 0.0, "labels": {}})
    frozen = freeze(out, vault, store)
    sample = frozen["sample"]
    todo = plan(sample, store.get("labels", {}))

    if args.dry_run:
        e = estimate(todo)
        print("population: %d pages (frozen %s); sample %d, seed %d"
              % (frozen["population_size"], frozen["drawn_at"], len(sample), frozen["seed"]))
        print("raters: %s (the gate is %s)" % (", ".join(RATERS),
                                                 cfg["extraction"]["referenceGate"]["model"]))
        print("pending: %d page labels in %d call(s) of <= %d pages; %d of %d budgeted calls used"
              % (e["pages"], e["calls"], BATCH, store.get("calls", 0), MAX_CALLS))
        print("tokens: ~%d in, ~%d out (thinking allowance included); API-price equivalent "
              "~$%.2f — subscription usage on a Max plan, not money"
              % (e["input_tokens"], e["output_tokens"], e["usd"]))
        if store.get("calls", 0) + e["calls"] > MAX_CALLS:
            print("WARNING: pending calls exceed the %d-call budget; --send stops at it" % MAX_CALLS)
        return 0

    if args.send:
        provider = llm.resolve(cfg)
        timeout = max(300, int(cfg["extraction"]["subprocessTimeout"]))
        mrl = _sibling("measure_rule_lift")

        def ask(prompt: str, system: str, model: str) -> Tuple[str, float]:
            resp = provider(prompt, system=system, model=model, timeout=timeout)
            return resp.text, float(resp.total_cost_usd or 0.0)

        # A scratch cwd: the rater's session must not land in a project's history.
        with tempfile.TemporaryDirectory(prefix="mnemo-demoted-keeps-") as scratch:
            with mrl._chdir(scratch):
                for line in send(todo, store, ask, lambda s: _write(labels_path, s)):
                    print(line, file=sys.stderr)
        print("%d call(s) used of %d, API-price equivalent $%.2f"
              % (store.get("calls", 0), MAX_CALLS, store.get("usd", 0.0)))

    labels = store.get("labels", {})
    if not any(labels.values()):
        print("no labels yet in %s; --dry-run, then --send" % labels_path, file=sys.stderr)
        return 1
    data = report(sample, labels, [column(m) for m in RATERS])
    data["population"] = frozen["population_size"]
    if args.json:
        print(json.dumps(data, indent=1))
        return 0
    print("labels %s\n" % labels_path)
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
