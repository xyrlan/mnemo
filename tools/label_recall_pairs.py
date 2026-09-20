"""Label (task, rule) pairs by hand, blind, and grade both judges against the labels.

Usage:
    PYTHONPATH=src python3 tools/label_recall_pairs.py --sample     # draw the pairs once, local
    PYTHONPATH=src python3 tools/label_recall_pairs.py --serve      # the form, on 127.0.0.1
    PYTHONPATH=src python3 tools/label_recall_pairs.py              # report, local

**Nothing here leaves the machine.** The form is served on the loopback
interface by the standard library and calls nothing.

``measure_recall_judged.py`` grades the ranking with one model's labels and
``measure_rerank_judges.py`` crosses them with a second model's. Both judges
are models. The only check that did not come from one was 60 pairs labelled
blind on 2026-09-19 — and those labels lived in a session scratchpad and went
with it. This tool makes that check repeatable and keeps it where the other
two label sets are: ``<vault>/.mnemo/recall-labels-human.json``.

The file is written after **every** answer, through a rename, so closing the
tab, the terminal or the machine loses nothing. ``--sample`` refuses to
overwrite a file that already holds labels.

The sample is stratified by the first judge's score, because the question is
whether that score means what it says at each level; a uniform draw would be
mostly pairs the judge puts near zero. Each pair freezes the task and the rule
text the rater saw and the score it was drawn on, so a label stays readable
after the vault moves on and the judgments are rebuilt.

The form shows the task and the rule. It never shows a judge's score, the
stratum, or whether the rule was read — the page is built from
:func:`blind`, and a test pins what that function lets through. The rater
answers the second judge's question, word for word, on its 0/1/2 scale.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import random
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_SIBLING = Path(__file__).resolve().with_name("measure_rerank_judges.py")
_spec = importlib.util.spec_from_file_location("measure_rerank_judges", _SIBLING)
mrk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrk)
mrj = mrk.mrj

HUMAN_NAME = "recall-labels-human.json"


def labels_name(rater: str) -> str:
    """One file per rater, and the rater's name in it: a model's labels are
    a third judge, never to be read as a person's."""
    safe = "".join(c for c in rater.lower() if c.isalnum() or c in "-_") or "human"
    return "recall-labels-%s.json" % safe

#: Upper edges of the first judge's score strata; the last one is closed.
STRATA = (0.2, 0.4, 0.6, 0.8, 1.0)
PER_STRATUM = 12
SEED = 1

#: What a pair may show the rater. Everything else stays in the file.
VISIBLE = ("id", "task", "rule")

LEVELS = (
    (2, "should read", "addresses the same problem, component or pitfall the task involves "
                       "and would change how the developer does it"),
    (1, "related", "same area of the system or same kind of work, but not this task's problem"),
    (0, "not relevant", "about something else, or too general to change anything in this task"),
)


def stratum(score: float) -> int:
    for i, edge in enumerate(STRATA):
        if score < edge:
            return i
    return len(STRATA) - 1


def draw(units: Sequence[Dict[str, Any]], texts: Sequence[Dict[str, str]], *,
         per_stratum: int = PER_STRATUM, seed: int = SEED) -> List[Dict[str, Any]]:
    """``per_stratum`` pairs from each score stratum, shuffled, deterministic.

    Only pairs the first judge scored and that still have a rule text are
    eligible. A stratum with fewer pairs than asked gives what it has.
    """
    rng = random.Random(seed)
    pools: List[List[Dict[str, Any]]] = [[] for _ in STRATA]
    for unit, unit_texts in zip(units, texts):
        for slug in sorted(unit["noul"]):
            if not unit_texts.get(slug):
                continue
            pools[stratum(unit["noul"][slug])].append({
                "unit": mrk._unit_key(unit), "slug": slug, "first": unit["noul"][slug],
                "task": unit["query"], "rule": unit_texts[slug]})
    picked: List[Dict[str, Any]] = []
    for pool in pools:
        rng.shuffle(pool)
        picked.extend(pool[:per_stratum])
    rng.shuffle(picked)
    for i, pair in enumerate(picked):
        pair["id"] = i
        pair["label"] = None
    return picked


def blind(pair: Dict[str, Any]) -> Dict[str, Any]:
    return {k: pair[k] for k in VISIBLE}


def next_pending(pairs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((p for p in pairs if p.get("label") is None), None)


def record(pairs: Sequence[Dict[str, Any]], pair_id: int, label: int) -> bool:
    """Set one label. ``False`` for an unknown pair or a value off the scale."""
    if isinstance(label, bool) or label not in (0, 1, 2):
        return False
    for pair in pairs:
        if pair["id"] == pair_id:
            pair["label"] = label
            pair["labelled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return True
    return False


def save(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def holds_labels(path: Path) -> bool:
    """Whether drawing again would throw a rater's work away."""
    if not path.is_file():
        return False
    pairs = json.loads(path.read_text(encoding="utf-8")).get("pairs") or []
    return any(p.get("label") is not None for p in pairs)


def report(pairs: Sequence[Dict[str, Any]], second: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """Each judge against the hand labels, on the pairs labelled so far.

    The first judge's score is the one frozen with the pair — the score the
    stratum was drawn from — so a later re-judging cannot move this report.
    """
    done = [p for p in pairs if p.get("label") is not None]
    scored = [(p["first"], p["label"]) for p in done]
    both = [(second[p["unit"]][p["slug"]], p["label"]) for p in done
            if p["slug"] in second.get(p["unit"], {})]
    return {
        "labelled": len(done), "of": len(pairs),
        "human_counts": {str(k): sum(1 for p in done if p["label"] == k) for k in (0, 1, 2)},
        "first": {
            "pairs": len(scored),
            "auc_should_read": mrk.auc([x for x, l in scored if l == 2], [x for x, l in scored if l < 2]),
            "auc_any": mrk.auc([x for x, l in scored if l >= 1], [x for x, l in scored if l == 0]),
            "high_but_irrelevant": sum(1 for x, l in scored if x >= mrj.SHOULD_READ and l == 0),
            "low_but_should_read": sum(1 for x, l in scored if x < 0.2 and l == 2),
        },
        "second": {
            "pairs": len(both),
            "exact": sum(1 for x, l in both if x == l),
            "auc_should_read": mrk.auc([x for x, l in both if l == 2], [x for x, l in both if l < 2]),
            "auc_any": mrk.auc([x for x, l in both if l >= 1], [x for x, l in both if l == 0]),
            "confusion": {"%d->%d" % (h, j): sum(1 for x, l in both if l == h and x == j)
                          for h in (0, 1, 2) for j in (0, 1, 2)},
        },
    }


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else "%.3f" % value


def format_report(r: Dict[str, Any]) -> str:
    f, s = r["first"], r["second"]
    return "\n".join([
        f"labelled {r['labelled']}/{r['of']}  (0: {r['human_counts']['0']}, "
        f"1: {r['human_counts']['1']}, 2: {r['human_counts']['2']})",
        f"first judge   {f['pairs']} pairs  AUC should-read {_fmt(f['auc_should_read'])}  "
        f"AUC any {_fmt(f['auc_any'])}  scored >= {mrj.SHOULD_READ} but labelled 0: "
        f"{f['high_but_irrelevant']}  scored < 0.2 but labelled 2: {f['low_but_should_read']}",
        f"second judge  {s['pairs']} pairs  AUC should-read {_fmt(s['auc_should_read'])}  "
        f"AUC any {_fmt(s['auc_any'])}  exact {s['exact']}/{s['pairs']}",
        "  rater->second  " + "  ".join(f"{k}: {v}" for k, v in s["confusion"].items() if v),
    ])


_PAGE = """<!doctype html><meta charset="utf-8"><title>label recall pairs</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 16px;
background:#fbfaf7;color:#1c1b19}}
@media(prefers-color-scheme:dark){{body{{background:#171614;color:#e9e6df}}
.box{{background:#211f1c!important;border-color:#3a3732!important}}button{{background:#2b2925!important;
color:inherit;border-color:#4a463f!important}}}}
h2{{font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;opacity:.6;margin:1.4rem 0 .3rem}}
.box{{background:#fff;border:1px solid #ddd8cd;border-radius:8px;padding:12px 16px;white-space:pre-wrap}}
form{{display:grid;gap:8px;margin-top:1.4rem}}
button{{font:inherit;text-align:left;padding:10px 14px;border:1px solid #cfc9bc;border-radius:8px;
background:#fff;cursor:pointer}}button:hover,button:focus{{outline:2px solid #c2571a}}
kbd{{font:600 .9rem ui-monospace,monospace;margin-right:.6rem}}small{{opacity:.65}}
</style>
<p><small>{done} of {total} labelled · saved after every answer · keys 0 1 2</small></p>
{body}
<script>addEventListener('keydown',e=>{{const b=document.getElementById('k'+e.key);if(b)b.click()}})</script>
"""


def render(pairs: Sequence[Dict[str, Any]]) -> str:
    done = sum(1 for p in pairs if p.get("label") is not None)
    pending = next_pending(pairs)
    if pending is None:
        body = "<h2>done</h2><p>Every pair is labelled. Stop the server and run the tool with no flag.</p>"
    else:
        seen = blind(pending)
        buttons = "".join(
            f'<button id="k{value}" name="label" value="{value}"><kbd>{value}</kbd>'
            f"<b>{html.escape(name)}</b> <small>— {html.escape(meaning)}</small></button>"
            for value, name, meaning in LEVELS)
        body = (f"<h2>task</h2><div class=box>{html.escape(seen['task'])}</div>"
                f"<h2>rule</h2><div class=box>{html.escape(seen['rule'])}</div>"
                f'<form method="post"><input type="hidden" name="id" value="{seen["id"]}">{buttons}</form>')
    return _PAGE.format(done=done, total=len(pairs), body=body)


def make_handler(path: Path, data: Dict[str, Any]) -> type:
    class Handler(BaseHTTPRequestHandler):
        def _page(self) -> None:
            page = render(data["pairs"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_GET(self) -> None:  # noqa: N802
            self._page()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            try:
                ok = record(data["pairs"], int(form["id"][0]), int(form["label"][0]))
            except (KeyError, ValueError):
                ok = False
            if ok:
                save(path, data)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def _labels_by_unit(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("labels") or {}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true", help="draw the pairs to label (once)")
    parser.add_argument("--serve", action="store_true", help="serve the form on 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rater", default="human",
                        help="whose labels: one file per rater (default: human)")
    parser.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--export-blind", metavar="PATH",
                        help="write the unlabelled pairs, blind, for a rater that is not at the form")
    parser.add_argument("--import-labels", metavar="PATH",
                        help='read {"<pair id>": 0|1|2} and record it')
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli
    vault = cli._resolve_vault()
    human_path = vault / ".mnemo" / labels_name(args.rater)
    qrels_path = vault / ".mnemo" / mrj.QRELS_NAME

    if args.sample:
        if holds_labels(human_path):
            print(f"error: {human_path} already holds labels; move it away to draw again", file=sys.stderr)
            return 1
        if not qrels_path.is_file():
            print(f"error: no judgments at {qrels_path}; the sample is stratified by them", file=sys.stderr)
            return 1
        qrels = json.loads(qrels_path.read_text(encoding="utf-8"))
        units = qrels["units"]
        pairs = draw(units, [mrj._bodies(vault, u) for u in units],
                     per_stratum=args.per_stratum, seed=args.seed)
        save(human_path, {"rater": args.rater, "first_model": qrels["model"], "first_judged_at": qrels["judged_at"],
                          "seed": args.seed, "per_stratum": args.per_stratum,
                          "sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "pairs": pairs})
        print(f"drew {len(pairs)} pairs -> {human_path}")
        return 0

    if not human_path.is_file():
        print(f"error: no sample at {human_path}; run with --sample first", file=sys.stderr)
        return 1
    data = json.loads(human_path.read_text(encoding="utf-8"))

    if args.export_blind:
        todo = [blind(p) for p in data["pairs"] if p.get("label") is None]
        Path(args.export_blind).write_text(json.dumps(todo, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(todo)} blind pairs -> {args.export_blind}")
        return 0

    if args.import_labels:
        given = json.loads(Path(args.import_labels).read_text(encoding="utf-8"))
        took = sum(1 for pair_id, label in given.items()
                   if str(pair_id).isdigit() and record(data["pairs"], int(pair_id), label))
        save(human_path, data)
        print(f"recorded {took} of {len(given)} labels -> {human_path}")
        return 0

    if args.serve:
        server = HTTPServer(("127.0.0.1", args.port), make_handler(human_path, data))
        print(f"http://127.0.0.1:{args.port}/  — Ctrl-C to stop; every answer is already saved")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    result = report(data["pairs"], _labels_by_unit(vault / ".mnemo" / mrk.SECOND_NAME))
    print(json.dumps(result, indent=2) if args.json else format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
