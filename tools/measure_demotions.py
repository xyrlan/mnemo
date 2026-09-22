"""What would the reference judge say about the pages the evidence gate demoted? (#429)

Usage:
    PYTHONPATH=src python3 tools/measure_demotions.py --score     # ask the judge (model calls)
    PYTHONPATH=src python3 tools/measure_demotions.py             # report, local

#429 lets a page the reference judge held (G/N) expire from ``shared/_inbox/``
after ``inbox.heldExpiryDays``, and left the evidence-gate demotions out: they
are most of the queue (130 of 144 staged pages on 2026-09-22) and nobody had
measured them. This asks the same judge the same question about them —
:data:`reference_gate.SYSTEM_PROMPT`, :func:`reference_gate.view`,
:func:`reference_gate.build_prompt`, :func:`reference_gate.parse_verdicts`,
the stage's own code, ten pages a call — so the answer is what the stage would
do if a demotion went through it.

The sample is frozen on first run into ``<vault>/.mnemo/demotion-judge/sample.json``
(every staged page carrying ``demoted_from: feedback``, as the judge would see
it); later runs read that file, not the moving queue. Answers land in
``scores.json`` next to it under ``<model>@<prompt hash>`` and resume: a page
already answered is not asked again. ``--resample`` refuses once any answer
exists, so the report never mixes two samples.

The judge's verdict is not the truth. On the 2026-09-22 audit's held-out half
it staged 30 of 31 pages both raters called junk and 3 of 41 they called good
(``tools/measure_reference_gate.py``); read "would hold" with that error bar.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_SIBLINGS = Path(__file__).resolve().parent

OUT_DIR = "demotion-judge"
SAMPLE_NAME = "sample.json"
SCORES_NAME = "scores.json"
NO_ANSWER = "none"


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_sample(vault: Path) -> List[Dict[str, Any]]:
    """Every staged demotion, oldest first, as the judge would read it.

    The text is :func:`reference_gate.view` of the name and the body with the
    graph section and advisory notes stripped — the body extraction would
    hand the judge, not the file a human opens.
    """
    from mnemo.core import inbox
    from mnemo.core.extract import reference_gate as gate
    from mnemo.core.extract.scanner import parse_frontmatter
    from mnemo.core.text_utils import retrieval_body

    rows = []
    for page in inbox.staged_pages(vault):
        if page.reason != "demotion":
            continue
        _, body = parse_frontmatter(page.path.read_text(encoding="utf-8", errors="replace"))
        rows.append({
            "id": page.key,
            "text": gate.view(page.name, retrieval_body(body)),
            "projects": list(page.projects),
            "mtime": page.mtime,
        })
    return rows


def tally(verdicts: Dict[str, Optional[str]], ids: Sequence[str]) -> Dict[str, int]:
    """Category counts over *ids*; a missing or empty answer is ``none``."""
    out: Dict[str, int] = {}
    for i in ids:
        cat = verdicts.get(i) or NO_ANSWER
        out[cat] = out.get(cat, 0) + 1
    return out


def would_hold(verdict: Optional[str], keep) -> bool:
    """The stage's rule: held unless the judge said a category it keeps."""
    return verdict not in set(keep)


def report_lines(sample: List[Dict[str, Any]], verdicts: Dict[str, Optional[str]],
                 keep, *, days: int, now: float) -> List[str]:
    """The numbers a decision about demotions needs, from answered rows only."""
    rows = [r for r in sample if r["id"] in verdicts]
    if not rows:
        return ["no answers yet — run with --score"]
    n = len(rows)
    counts = tally(verdicts, [r["id"] for r in rows])
    held = [r for r in rows if would_hold(verdicts[r["id"]], keep)]
    old = [r for r in held if (now - r["mtime"]) // 86400 >= days]
    lines = [
        "answered %d of %d staged demotions" % (n, len(sample)),
        "verdicts: " + ", ".join(
            "%s %d (%.0f%%)" % (c, counts[c], 100.0 * counts[c] / n) for c in sorted(counts)),
        "would hold (G/N/no answer): %d of %d (%.0f%%); would keep (T/S): %d"
        % (len(held), n, 100.0 * len(held) / n, n - len(held)),
        "held and already %d+ days old — what one sweep would archive if demotions "
        "were in scope: %d" % (days, len(old)),
        "",
        "by project (held / answered):",
    ]
    per: Dict[str, List[int]] = {}
    for r in rows:
        for p in r["projects"] or ["(none)"]:
            h_n = per.setdefault(p, [0, 0])
            h_n[1] += 1
            h_n[0] += int(would_hold(verdicts[r["id"]], keep))
    for p, (h, t) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        lines.append("  %-28s %3d / %-3d" % (p, h, t))
    kept = [r for r in rows if not would_hold(verdicts[r["id"]], keep)]
    if kept:
        lines += ["", "the judge would keep (T/S) — candidates for `mnemo inbox --promote`:"]
        for r in kept:
            lines.append("  %s %-50s %s" % (verdicts[r["id"]], r["id"], r["text"][:90]))
    return lines


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, inbox, llm, paths
    from mnemo.core.extract import reference_gate as gate

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--score", action="store_true", help="ask the judge (model calls)")
    ap.add_argument("--model", default=None, help="model to score with (default: config)")
    ap.add_argument("--resample", action="store_true",
                    help="freeze a new sample from the current queue (refused once answers exist)")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    out = vault / ".mnemo" / OUT_DIR
    sample_path, scores_path = out / SAMPLE_NAME, out / SCORES_NAME
    scores: Dict[str, Dict[str, Optional[str]]] = (
        _read_json(scores_path) if scores_path.exists() else {}
    )

    if args.resample and any(scores.values()):
        print("refusing --resample: %s already holds answers for the frozen sample" % scores_path)
        return 1
    if args.resample or not sample_path.exists():
        out.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(json.dumps(build_sample(vault), indent=1), encoding="utf-8")
    sample = _read_json(sample_path)

    mrg = _sibling("measure_reference_gate")
    model = args.model or cfg["extraction"]["referenceGate"]["model"]
    col = mrg.column(model, gate.SYSTEM_PROMPT)

    if args.score:
        provider = llm.resolve(cfg)
        spent = {"calls": 0, "usd": 0.0, "subscription": True}

        def ask(prompt: str) -> str:
            resp = provider(prompt, system=gate.SYSTEM_PROMPT, model=model,
                            timeout=int(cfg["extraction"]["subprocessTimeout"]))
            spent["calls"] += 1
            spent["usd"] += float(resp.total_cost_usd or 0.0)
            spent["subscription"] = spent["subscription"] and resp.api_key_source == "none"
            # Saved after every call, so an interrupted run resumes where it stopped.
            return resp.text

        answered = dict(scores.get(col, {}))
        for batch in mrg.chunks([r for r in sample if r["id"] not in answered], mrg.CHUNK):
            answered = mrg.score(batch, answered, ask, gate)
            scores[col] = answered
            scores_path.write_text(json.dumps(scores, indent=1, sort_keys=True), encoding="utf-8")
        print("scored %d rows under %s in %d calls, $%.4f%s -> %s" % (
            len(answered), col, spent["calls"], spent["usd"],
            " (subscription)" if spent["subscription"] and spent["calls"] else "",
            scores_path))

    print("judge %s, sample %s\n" % (col, sample_path))
    for line in report_lines(sample, scores.get(col, {}), gate.KEEP,
                             days=inbox.held_expiry_days(cfg), now=time.time()):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
