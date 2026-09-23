"""What would the normal extraction gates do with backfill pages, and is what they let through good? (#471)

Usage:
    PYTHONPATH=src python3 tools/measure_backfill_routes.py --dry-run   # routes, sample, calls, cost; calls nothing
    PYTHONPATH=src python3 tools/measure_backfill_routes.py --send      # both raters label what is pending (model calls)
    PYTHONPATH=src python3 tools/measure_backfill_routes.py             # report, local
    PYTHONPATH=src python3 tools/measure_backfill_routes.py --json

Front B of the second-user round. **Measure only**: nothing here promotes,
stamps or edits a page. The vault is read, never written; every write goes
under ``--out``.

The vault. By default the arm (a) vault of ``tools/measure_day_one.py``
(#467): a fresh install whose backfill harvested 44 prior sessions and whose
first extraction staged every page it wrote, because the origin stamp
(``core/backfill/origin.py``) forces staging. That extraction still ran every
*other* gate and left its answer on the page: ``demoted_from: feedback`` where
the evidence gate found no user quote, ``reference_gate: <verdict>`` where the
reference gate (#425) judged it, and the page's ``sources``.

The routes. For each staged page, :func:`route_page` asks the real routing code —
``inbox.paths._target_path_for_page`` for cluster pages,
``promote._target_path`` for project pages — where the page would land with
the origin stamp taken off and every other gate's answer kept. It runs
against an empty scratch root, so "already staged" cannot keep a page staged.
No model is called for this part.

The sample. Every page the normal gates would make live, drawn uniformly
with :data:`SEED` if there are more than :data:`SAMPLE_SIZE`; frozen with the
text raters see into ``sample.json``. The raters, rubric, blind view, batch
order and statistics are ``tools/measure_demoted_keeps.py``'s (#465), called,
not copied: a letter per page from the reference gate's own categories plus
W (wrong), good = S/T.

**The bar, declared in #471 before any label existed: the route changes only
if >= 85% of the sample is labelled good by both raters.** It is :data:`BAR`
and it reads the point estimate; the 95% Wilson interval is printed beside it.

Budget: at most :data:`MAX_CALLS` model calls, counted across reruns in
``labels.json``.

First run, 2026-09-23, over the arm (a) vault #467 left (clubinho, 44
sessions of history, 20 harvested, 59 memory files): 56 staged backfill pages.
Routes with the stamp off: project 32 live (the project path has no gate);
reference 15 live on the reference gate (13 system, 2 technique; it held
none), 6 staged by the evidence gate (every feedback page was demoted), 3
staged as multi-source. So 47 live, 9 staged. All 47 were labelled, 10
calls, API-price equivalent $1.01 (dry run said ~$1.69). Good per rater:
``claude-opus-5-5`` 40/47 = 85.1% [72.3, 92.6], ``claude-fable-5-1`` 43/47 =
91.5% [80.1, 96.6]; under both **40/47 = 85.1% [72.3, 92.6]**; exact
agreement 44/47, kappa 0.69. **Verdict: PASS against the 85% bar**, by one
page: 39/47 would have failed, and the interval's floor is 72%. By route,
after the fact: project 26/32 = 81.2% [64.7, 91.1], reference 14/15. Junk was
G 2, N 1 and W 4 (Opus) / W 1 (Fable): a stale or self-contradicting project
fact, more than generic advice.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
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


dk = _sibling("measure_demoted_keeps")

DEFAULT_VAULT = Path.home() / ".cache" / "mnemo" / "day-one" / "arm-a" / "vault"
DEFAULT_OUT = Path.home() / ".cache" / "mnemo" / "day-one" / "backfill-routes"
SAMPLE_NAME = "sample.json"
LABELS_NAME = "labels.json"

#: Declared in #471 before any page was labelled. Do not move it.
BAR = 0.85
#: Arm (a) makes fewer live candidates than this, so the sample is all of them.
SAMPLE_SIZE = 60
SEED = 471
#: The issue's budget, over every run of this tool against one sample.
MAX_CALLS = 30

LIVE, STAGED = "live", "staged"


# --- routing: the real code, the stamp taken off ------------------------------

def _judged(fm: Dict[str, Any]) -> Optional[str]:
    """The reference gate's letter from a page's stamp.

    No stamp on an inferred reference page means the judge gave no answer —
    ``""``, which the gate holds (``reference_gate.cleared``). Any other page
    type is never judged: ``None``.
    """
    from mnemo.core.extract import reference_gate

    if str(fm.get("type") or "") != "reference":
        return None
    if str(fm.get("confidence") or "") == "verified":
        return None
    label = str(fm.get(reference_gate.HELD_KEY) or "").strip().lower()
    for letter, word in reference_gate.LABELS.items():
        if word == label:
            return letter
    return ""


def _sources(fm: Dict[str, Any]) -> List[str]:
    src = fm.get("sources")
    if isinstance(src, list):
        return [str(s) for s in src]
    return [str(src)] if src else []


def route_page(fm: Dict[str, Any], slug: str, page_type: str,
               scratch: Path) -> Tuple[str, str]:
    """(route, reason) for one staged page with ``origin_backfill`` off.

    ``scratch`` is an empty directory standing in for the vault root, so the
    reference gate's "already staged stays staged" rule cannot answer for a
    page that is only staged because of the stamp.
    """
    from mnemo.core.extract.demotion import is_demoted_frontmatter
    from mnemo.core.extract.inbox.paths import _target_path_for_page, _is_auto_promoted_target
    from mnemo.core.extract.inbox.types import ExtractedPage

    if page_type == "project":
        # ``promote_projects`` has no gate: with the stamp off every project
        # page is written 1:1 to shared/project/. ``promote._target_path`` is
        # asked all the same, so a gate added there later shows up here.
        from mnemo.core.extract import promote

        class _File:  # the two fields ``_project_slug`` reads
            pass
        f = _File()
        f.agent, f.slug = "", slug
        target = promote._target_path(scratch, f, False)
        live = _is_auto_promoted_target(target, scratch)
        return (LIVE, "project: no gate") if live else (STAGED, "project: staged")

    demoted = is_demoted_frontmatter(fm)
    judged = _judged(fm)
    page = ExtractedPage(
        slug=slug, type=page_type, name=str(fm.get("name") or slug),
        description=str(fm.get("description") or ""), body="",
        source_files=_sources(fm), source_hash="",
        confidence=str(fm.get("confidence") or "inferred"),
        unverified_feedback=demoted, judged=judged, origin_backfill=False,
    )
    target = _target_path_for_page(page, scratch)
    if _is_auto_promoted_target(target, scratch):
        if judged:
            return LIVE, "reference gate: %s" % _label(judged)
        return LIVE, "%s: single source" % page_type
    if demoted:
        return STAGED, "evidence gate: no user quote"
    if judged is not None and judged not in ("T", "S"):
        return STAGED, "reference gate: %s" % (_label(judged) or "no answer")
    if len(page.source_files) != 1:
        return STAGED, "multi-source (%d)" % len(page.source_files)
    return STAGED, "other"


def _label(letter: Optional[str]) -> str:
    from mnemo.core.extract import reference_gate
    return reference_gate.LABELS.get(letter or "", "")


# --- the vault ---------------------------------------------------------------------

def population(vault: Path) -> List[Dict[str, Any]]:
    """Every backfill page staged in *vault*, with its route and rater view.

    Read-only. A page without the origin stamp is not this measurement's and
    is skipped; so is a ``.proposed.md`` sibling.
    """
    from mnemo.core.backfill.origin import is_backfill_frontmatter
    from mnemo.core.extract import reference_gate
    from mnemo.core.extract.scanner import parse_frontmatter as split_frontmatter
    from mnemo.core.filters import is_proposed_sibling, iter_staged_pages, parse_frontmatter
    from mnemo.core.text_utils import retrieval_body

    rows = []
    with tempfile.TemporaryDirectory(prefix="mnemo-backfill-routes-") as tmp:
        scratch = Path(tmp)
        for path in iter_staged_pages(Path(vault)):
            if is_proposed_sibling(path) or path.suffix != ".md":
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = parse_frontmatter(raw) or {}
            if not is_backfill_frontmatter(fm):
                continue
            page_type = path.parent.name
            slug = str(fm.get("slug") or path.stem)
            where, why = route_page(fm, slug, page_type, scratch)
            _, body = split_frontmatter(raw)
            rows.append({
                "id": "%s/%s" % (page_type, path.stem),
                "type": page_type,
                "route": where,
                "reason": why,
                "text": reference_gate.view(str(fm.get("name") or path.stem),
                                            retrieval_body(body)),
            })
    return sorted(rows, key=lambda r: r["id"])


# --- the pure part -----------------------------------------------------------------

def route_counts(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """``{type: {"<route>: <reason>": n}}`` — the per-route table the issue asks for."""
    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        key = "%s: %s" % (r["route"], r["reason"])
        out.setdefault(r["type"], {})
        out[r["type"]][key] = out[r["type"]].get(key, 0) + 1
    return out


def draw(rows: Sequence[Dict[str, Any]], *, size: int = SAMPLE_SIZE,
         seed: int = SEED) -> List[Dict[str, Any]]:
    """The live-route pages, all of them if they fit, else a seeded uniform draw."""
    return dk.draw([r for r in rows if r["route"] == LIVE], size=size, seed=seed)


def report(sample: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, str]],
           cols: Sequence[str], *, bar: float = BAR) -> Dict[str, Any]:
    """Good rates per rater and under both, the verdict, and both-good by route."""
    base = dk.report(sample, labels, cols, bar=bar)
    base["verdict"] = base["verdict"].replace(
        "promotion may go ahead", "backfill pages may take the normal gates").replace(
        "do not promote on the gate's verdict", "routing does not change")
    a, b = (labels.get(c, {}) for c in cols[:2])
    by_route: Dict[str, Any] = {}
    for r in sample:
        if r["id"] not in a or r["id"] not in b:
            continue
        key = "%s (%s)" % (r["type"], r["reason"])
        good = dk.LETTERS[a[r["id"]]] == dk.GOOD and dk.LETTERS[b[r["id"]]] == dk.GOOD
        k, n = by_route.get(key, (0, 0))
        by_route[key] = (k + good, n + 1)
    base["by_route"] = {key: dk._rate(k, n) for key, (k, n) in sorted(by_route.items())}
    base.pop("by_gate", None)
    for row in base["not_both_good"]:
        row["gate"] = next(r["type"] for r in sample if r["id"] == row["id"])
    return base


def report_lines(data: Dict[str, Any], routes: Dict[str, Dict[str, int]]) -> List[str]:
    lines = ["routes with the origin stamp off (every other gate's answer kept):"]
    for page_type in sorted(routes):
        for key, n in sorted(routes[page_type].items()):
            lines.append("  %-10s %-40s %d" % (page_type, key, n))
    lines += [
        "",
        "sample: %d live-route pages, seed %d" % (data["sample"], SEED),
        "",
        "good (S/T) per rater, 95% Wilson interval:",
    ]
    for col, r in data["raters"].items():
        lines.append("  %-30s %s   %s" % (col, dk._rate_line(r), " ".join(
            "%s %d" % kv for kv in sorted(r["cats"].items()))))
    ag = data["agreement"]
    lines += [
        "good under both raters:          %s" % dk._rate_line(data["both_good"]),
        "agreement on good/junk: %d pages, exact %s, kappa %s"
        % (ag["n"], dk._pct(ag["exact"]), "n/a" if ag["kappa"] is None else "%.2f" % ag["kappa"]),
        "",
        "bar (declared in #471): >= %.0f%% good under both raters" % (100 * data["bar"]),
        "verdict: " + data["verdict"],
        "",
        "after the fact — both-good by route (never shown to a rater):",
    ]
    for key, r in data["by_route"].items():
        lines.append("  %-40s %s" % (key, dk._rate_line(r)))
    if data["not_both_good"]:
        lines += ["", "not good under both (rater A / rater B / type):"]
        for row in data["not_both_good"]:
            lines.append("  %s/%s %-9s %-55s %s" % (row["a"], row["b"], row["gate"], row["id"],
                                                    row["text"]))
    return lines


def freeze(out: Path, vault: Path, store: Dict[str, Any]) -> Dict[str, Any]:
    """The frozen routes and sample, computed now if they do not exist yet."""
    path = out / SAMPLE_NAME
    if path.exists():
        return dk._read(path, {})
    if any(store.get("labels", {}).values()):
        raise SystemExit("error: %s holds labels but %s is gone; refusing to redraw"
                         % (out / LABELS_NAME, path))
    rows = population(vault)
    frozen = {
        "drawn_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vault": str(vault), "seed": SEED, "population_size": len(rows),
        "routes": route_counts(rows),
        "sample": draw(rows),
    }
    out.mkdir(parents=True, exist_ok=True)
    dk._write(path, frozen)
    return frozen


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", type=Path, default=DEFAULT_VAULT,
                    help="vault whose staged backfill pages are routed (read only)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="where the frozen sample and labels live")
    ap.add_argument("--dry-run", action="store_true",
                    help="routes, sample, pending calls and cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="label every pending page (model calls)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    labels_path = args.out / LABELS_NAME
    store: Dict[str, Any] = dk._read(labels_path, {"calls": 0, "usd": 0.0, "labels": {}})
    frozen = freeze(args.out, args.vault, store)
    sample = frozen["sample"]
    todo = dk.plan(sample, store.get("labels", {}))

    if args.dry_run:
        e = dk.estimate(todo)
        print("vault: %s — %d staged backfill pages (frozen %s)"
              % (frozen["vault"], frozen["population_size"], frozen["drawn_at"]))
        for page_type in sorted(frozen["routes"]):
            for key, n in sorted(frozen["routes"][page_type].items()):
                print("  %-10s %-40s %d" % (page_type, key, n))
        print("sample: %d live-route pages, seed %d; raters: %s"
              % (len(sample), frozen["seed"], ", ".join(dk.RATERS)))
        print("pending: %d page labels in %d call(s) of <= %d pages; %d of %d budgeted calls used"
              % (e["pages"], e["calls"], dk.BATCH, store.get("calls", 0), MAX_CALLS))
        print("tokens: ~%d in, ~%d out (thinking allowance included); API-price equivalent "
              "~$%.2f — subscription usage on a Max plan, not money"
              % (e["input_tokens"], e["output_tokens"], e["usd"]))
        if store.get("calls", 0) + e["calls"] > MAX_CALLS:
            print("WARNING: pending calls exceed the %d-call budget; --send stops at it" % MAX_CALLS)
        return 0

    if args.send:
        cfg = config.load_config()
        provider = llm.resolve(cfg)
        timeout = max(300, int(cfg["extraction"]["subprocessTimeout"]))
        mrl = _sibling("measure_rule_lift")

        def ask(prompt: str, system: str, model: str) -> Tuple[str, float]:
            resp = provider(prompt, system=system, model=model, timeout=timeout)
            return resp.text, float(resp.total_cost_usd or 0.0)

        # A scratch cwd: the rater's session must not land in a project's history.
        with tempfile.TemporaryDirectory(prefix="mnemo-backfill-routes-") as scratch:
            with mrl._chdir(scratch):
                for line in dk.send(todo, store, ask, lambda s: dk._write(labels_path, s),
                                    max_calls=MAX_CALLS):
                    print(line, file=sys.stderr)
        print("%d call(s) used of %d, API-price equivalent $%.2f"
              % (store.get("calls", 0), MAX_CALLS, store.get("usd", 0.0)))

    labels = store.get("labels", {})
    if not any(labels.values()):
        print("no labels yet in %s; --dry-run, then --send" % labels_path, file=sys.stderr)
        return 1
    data = report(sample, labels, [dk.column(m) for m in dk.RATERS])
    data["population"] = frozen["population_size"]
    data["routes"] = frozen["routes"]
    if args.json:
        print(json.dumps(data, indent=1))
        return 0
    print("labels %s\n" % labels_path)
    for line in report_lines(data, frozen["routes"]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
