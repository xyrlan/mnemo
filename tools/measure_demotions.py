"""What would the reference judge say about the pages the evidence gate demoted? (#429)

Usage:
    PYTHONPATH=src python3 tools/measure_demotions.py --score     # ask the judge (model calls)
    PYTHONPATH=src python3 tools/measure_demotions.py             # report, local
    PYTHONPATH=src python3 tools/measure_demotions.py --stamp     # what --apply would write
    PYTHONPATH=src python3 tools/measure_demotions.py --stamp --apply

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

``--stamp`` is the one-time backfill for #432, which made extraction judge
demotions and stamp them: it writes the ``reference_gate:`` line each staged
demotion of the frozen sample would have been rendered with, from the answers
already in ``scores.json`` (no model calls). Dry run unless ``--apply``. Only
a page that still exists, still says ``demoted_from:`` and has no
``reference_gate:`` line yet is touched, and only by inserting that one line
after ``demoted_from:`` — every other byte stays, and the page's
``written_hash`` moves with it so extraction does not take the line for a
user edit (#470). The mtime moves on purpose:
it starts the page's ``inbox.heldExpiryDays`` at the stamp, not at a verdict
nobody could see.

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


_FENCE = b"---"
_DEMOTED = b"demoted_from:"
_GATE = b"reference_gate:"


def _stamped(raw: bytes, label: str) -> Optional[bytes]:
    """*raw* with ``reference_gate: <label>`` after ``demoted_from:``, or None.

    None when the file is not a demotion's frontmatter or already carries a
    stamp. Works on bytes, line by line, so the line ending the file uses is
    the one the new line gets and nothing else moves.
    """
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != _FENCE:
        return None
    at = None
    for i, line in enumerate(lines[1:], 1):
        if line.rstrip(b"\r\n") == _FENCE:
            break
        if line.startswith(_GATE):
            return None
        if line.startswith(_DEMOTED):
            at = i
    else:
        return None  # no closing fence
    if at is None:
        return None
    eol = lines[at][len(lines[at].rstrip(b"\r\n")):] or b"\n"
    new = b"%s %s%s" % (_GATE, label.encode("ascii"), eol)
    return b"".join(lines[: at + 1] + [new] + lines[at + 1:])


def plan_stamps(vault: Path, sample: List[Dict[str, Any]], verdicts: Dict[str, Optional[str]],
                labels: Dict[str, str]) -> Tuple[List[Tuple[Path, str]], Dict[str, int]]:
    """Which sampled pages get which label, and why the rest do not.

    Skips: ``no answer`` (the judge gave none, so nothing may expire it on the
    judge's word — the extractor's own rule), ``gone`` (promoted, dropped or
    expired since the sample froze), ``not a demotion or already stamped``.
    """
    todo: List[Tuple[Path, str]] = []
    skipped: Dict[str, int] = {}
    for row in sample:
        label = labels.get(verdicts.get(row["id"]) or "")
        page_type, _, slug = row["id"].partition("/")
        path = vault / "shared" / "_inbox" / page_type / (slug + ".md")
        if label is None:
            why = "no answer"
        elif not path.is_file():
            why = "gone"
        elif _stamped(path.read_bytes(), label) is None:
            why = "not a demotion or already stamped"
        else:
            todo.append((path, label))
            continue
        skipped[why] = skipped.get(why, 0) + 1
    return todo, skipped


def apply_stamps(todo: List[Tuple[Path, str]], vault: Path) -> int:
    """Write each planned line; a page that changed since the plan is skipped.

    Through :func:`machine_edits.edit_session`, so each stamped page's
    ``written_hash`` moves with the line (#470). A bare write left 127 staged
    pages reading as user-edited, and their next update went to a
    ``.proposed.md`` sibling instead. Raises ``VaultBusy`` while an extraction
    holds the vault.
    """
    from mnemo.core.extract.machine_edits import edit_session

    done = 0
    with edit_session(vault) as session:
        for path, label in todo:
            new = _stamped(path.read_bytes(), label)
            if new is not None:
                session.write(path, new)
                done += 1
    return done


def stamp_lines(todo: List[Tuple[Path, str]], skipped: Dict[str, int], *,
                applied: Optional[int]) -> List[str]:
    per: Dict[str, int] = {}
    for _, label in todo:
        per[label] = per.get(label, 0) + 1
    verb = "would stamp" if applied is None else "stamped"
    n = len(todo) if applied is None else applied
    lines = ["%s %d staged demotions: %s" % (
        verb, n, ", ".join("%s %d" % kv for kv in sorted(per.items())) or "none")]
    lines += ["skipped %d: %s" % (v, k) for k, v in sorted(skipped.items())]
    if applied is None and todo:
        lines.append("dry run — nothing written; add --apply to write")
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
    ap.add_argument("--stamp", action="store_true",
                    help="write each staged demotion's verdict on it (#432); dry run without --apply")
    ap.add_argument("--apply", action="store_true", help="with --stamp: write the lines")
    args = ap.parse_args(argv)
    if args.apply and not args.stamp:
        ap.error("--apply only goes with --stamp")

    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    out = vault / ".mnemo" / OUT_DIR
    sample_path, scores_path = out / SAMPLE_NAME, out / SCORES_NAME
    scores: Dict[str, Dict[str, Optional[str]]] = (
        _read_json(scores_path) if scores_path.exists() else {}
    )

    if args.stamp and not sample_path.exists():
        print("nothing to stamp: no frozen sample at %s — run --score first" % sample_path)
        return 1
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

    if args.stamp:
        todo, skipped = plan_stamps(vault, sample, scores.get(col, {}), gate.LABELS)
        from mnemo.core.extract.machine_edits import VaultBusy
        try:
            applied = apply_stamps(todo, vault) if args.apply else None
        except VaultBusy as exc:
            print("nothing stamped: %s" % exc)
            return 1
        print("judge %s, sample %s\n" % (col, sample_path))
        for line in stamp_lines(todo, skipped, applied=applied):
            print(line)
        return 0

    print("judge %s, sample %s\n" % (col, sample_path))
    for line in report_lines(sample, scores.get(col, {}), gate.KEEP,
                             days=inbox.held_expiry_days(cfg), now=time.time()):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
