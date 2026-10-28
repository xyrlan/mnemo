"""Size and load time of the reflex index, v1 on disk against v2 (#779), and
what the durable write that keeps ``.prev`` costs a v2 rebuild (#767).

Usage:
    PYTHONPATH=src python3 tools/measure_reflex_index_load.py [--vault ~/mnemo]
        [--docs 4078] [--terms 50346] [--postings 541875] [--repeats 5] [--hook] [--json]

Read-only on the vault: its index is read once, and both files are written to
a temporary directory.

``UserPromptSubmit`` loads ``.mnemo/reflex-index.json`` in a fresh process for
every prompt that reaches ranking. This writes one in-memory index twice — as
v1 wrote it (``json.dumps(indent=2)``, every posting a dict with the slug and
all six field counts) and as :func:`reflex.index.write_index` writes it now —
and for each reports:

- ``bytes``: the file's size;
- ``load_s``: the fastest of ``--repeats`` :func:`reflex.index.load_index`
  calls (read + parse), the cost each such prompt pays;
- ``decide_s``: the fastest of ``--repeats`` load + :func:`reflex.decide.decide`
  on one prompt, everything the hook does with the index;
- ``write_s``: the fastest serialize + atomic write, what a SessionStart
  rebuild pays on top of the build (v2 as ``write_index`` writes it: flushed
  and keeping ``.prev``, see ``write_cost``).

``write_cost`` takes the v2 write apart (#767): the fastest atomic write of
the v2 bytes as it was before (``plain_s``), with the flush to disk
(``durable_s``; ``flush_s`` is the difference), and with the flush and the
hard-linked ``.prev`` (``kept_s``, what ``write_index`` does), and the bytes
``.prev`` holds on disk once the index has been rebuilt over itself
(``prev_bytes``): the link copies nothing, but it keeps the replaced
version's bytes alive.

With ``--vault`` the index is that vault's own (either version); without it,
a synthetic one of ``--docs`` documents, ``--terms`` terms and about
``--postings`` postings, the sizes of the 2026-10-28 vault in #779, with
term frequencies drawn Zipf-like so a few terms are in most docs and most
terms in few. ``same_scores`` is whether both files rank the prompt the same.

``--hook`` also runs the ``UserPromptSubmit`` hook as a process on a
temporary vault holding each file (reflex on, no judge), and reports the
fastest end-to-end time on the prompt and on a prompt too short to rank.

Times are wall clock on this machine and move with it; the ratio between the
two versions is the number to read. What it cannot see: the interpreter start
and imports a hook process also pays, which do not depend on the index.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemo.core.atomic import atomic_write_bytes, previous_path  # noqa: E402
from mnemo.core.reflex import index as reflex_index  # noqa: E402
from mnemo.core.reflex.decide import decide  # noqa: E402

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

FIELDS = ("name", "topic_tags", "aliases", "description", "body", "evidence")
PROJECT = "proj"
REFLEX_CFG: Dict[str, Any] = {}


def synthetic_index(docs: int, terms: int, postings: int, *, seed: int = 779) -> Dict[str, Any]:
    """An in-memory index (what ``build_index`` returns) of the given sizes."""
    rng = random.Random(seed)
    vocab = [f"term{i}" for i in range(terms)]
    cum: List[float] = []
    total = 0.0
    for rank in range(1, terms + 1):
        total += 1.0 / rank
        cum.append(total)
    per_doc = max(1, postings // max(docs, 1))
    out_postings: Dict[str, List[Dict[str, Any]]] = {}
    out_docs: Dict[str, Dict[str, Any]] = {}
    totals = {f: 0 for f in FIELDS}
    for d in range(docs):
        slug = f"rule-{d:05d}-about-some-recurring-pattern"
        doc_terms = set(rng.choices(vocab, cum_weights=cum, k=per_doc))
        # Every term in the vocabulary is in at least one doc, as in a built index.
        doc_terms.update(vocab[i] for i in range(d, terms, docs))
        length = {f: 0 for f in FIELDS}
        for term in sorted(doc_terms):
            tf = {f: 0 for f in FIELDS}
            tf["body"] = rng.choice((1, 1, 1, 2, 3))
            if rng.random() < 0.08:
                tf[rng.choice(("name", "description", "topic_tags"))] = 1
            for f in FIELDS:
                length[f] += tf[f]
            out_postings.setdefault(term, []).append({"slug": slug, "tf": tf})
        for f in FIELDS:
            totals[f] += length[f]
        out_docs[slug] = {
            "field_length": length,
            "preview": f"Preview of rule {d}: " + "words " * 50,
            "path": f"shared/feedback/{slug}.md",
            "stability": "stable",
            "projects": [PROJECT],
            "universal": d % 7 == 0,
            "retired": False,
        }
    return {
        "schema_version": reflex_index.SCHEMA_VERSION,
        "generated_at": "2026-10-28T00:00:00Z",
        "avg_field_length": {f: totals[f] / docs if docs else 0.0 for f in FIELDS},
        "doc_count": docs,
        "postings": out_postings,
        "docs": out_docs,
    }


def materialize(index: Dict[str, Any]) -> Dict[str, Any]:
    """``index`` with its postings a plain dict, as ``build_index`` returns them."""
    return dict(index, postings={t: list(e) for t, e in index["postings"].items()})


def v1_bytes(index: Dict[str, Any]) -> bytes:
    """``index`` as v1 ``write_index`` wrote it."""
    return json.dumps(dict(index, schema_version=1), indent=2).encode("utf-8")


def prompt_for(index: Dict[str, Any]) -> str:
    """A prompt of mid-frequency terms that some doc in ``index`` shares."""
    postings = index["postings"]
    by_df = sorted(postings, key=lambda t: (len(postings[t]), t))
    mid = by_df[len(by_df) // 2: len(by_df) // 2 + 4] or by_df[:4]
    return " ".join(mid + ["pattern"])


def _fastest(fn: Callable[[], Any], repeats: int) -> float:
    best = float("inf")
    for _ in range(max(1, repeats)):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def measure_file(vault: Path, data: bytes, prompt: str, project: str,
                 repeats: int) -> Dict[str, Any]:
    path = vault / ".mnemo" / reflex_index.INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    decision = decide(lambda: reflex_index.load_index(vault), project=project,
                      prompt=prompt, reflex_cfg=REFLEX_CFG)
    return {
        "bytes": len(data),
        "load_s": _fastest(lambda: reflex_index.load_index(vault), repeats),
        "decide_s": _fastest(lambda: decide(lambda: reflex_index.load_index(vault),
                                            project=project, prompt=prompt,
                                            reflex_cfg=REFLEX_CFG), repeats),
        "scores": decision.scores,
        "accepted": decision.accepted,
    }


def measure(index: Dict[str, Any], *, repeats: int = 5,
            project: str = PROJECT) -> Dict[str, Any]:
    prompt = prompt_for(index)
    with tempfile.TemporaryDirectory() as tmp:
        old = measure_file(Path(tmp) / "v1", v1_bytes(index), prompt, project, repeats)
        old_path = Path(tmp) / "v1" / ".mnemo" / reflex_index.INDEX_FILENAME
        old["write_s"] = _fastest(lambda: atomic_write_bytes(old_path, v1_bytes(index)), repeats)
        new_vault = Path(tmp) / "v2"
        (new_vault / ".mnemo").mkdir(parents=True)
        write_s = _fastest(lambda: reflex_index.write_index(new_vault, index), repeats)
        new = measure_file(new_vault, reflex_index.encode_index(index), prompt, project, repeats)
    same = old.pop("scores") == new.pop("scores") and old["accepted"] == new["accepted"]
    new["write_s"] = write_s
    return {
        "docs": len(index["docs"]),
        "terms": len(index["postings"]),
        "postings": sum(len(e) for e in index["postings"].values()),
        "prompt": prompt,
        "v1": old,
        "v2": new,
        "bytes_ratio": new["bytes"] / old["bytes"] if old["bytes"] else None,
        "load_ratio": new["load_s"] / old["load_s"] if old["load_s"] else None,
        "same_scores": same,
    }


def write_cost(index: Dict[str, Any], *, repeats: int = 5) -> Dict[str, Any]:
    """The v2 write with and without the flush and the kept ``.prev`` (#767)."""
    data = reflex_index.encode_index(index)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / reflex_index.INDEX_FILENAME
        atomic_write_bytes(path, data)
        plain = _fastest(lambda: atomic_write_bytes(path, data), repeats)
        durable = _fastest(lambda: atomic_write_bytes(path, data, durable=True), repeats)
        kept = _fastest(lambda: atomic_write_bytes(path, data, durable=True,
                                                   keep_previous=True), repeats)
        prev = previous_path(path)
        prev_bytes = prev.stat().st_size if prev.exists() else 0
    return {
        "index_bytes": len(data),
        "plain_s": plain,
        "durable_s": durable,
        "kept_s": kept,
        "flush_s": durable - plain,
        "prev_bytes": prev_bytes,
    }


SRC = Path(__file__).resolve().parents[1] / "src"
#: Two tokens: the hook's pre-gate returns before it loads the index.
SHORT_PROMPT = "hi there"


def run_hook(vault: Path, prompt: str) -> float:
    """Wall time of one ``UserPromptSubmit`` hook process on ``vault``."""
    config = vault / "mnemo.config.json"
    config.write_text(json.dumps({"vaultRoot": str(vault), "reflex": {"enabled": True}}),
                      encoding="utf-8")
    env = dict(os.environ, MNEMO_CONFIG_PATH=str(config), PYTHONPATH=str(SRC))
    payload = json.dumps({"session_id": "measure-779", "cwd": str(vault), "prompt": prompt})
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-m", "mnemo.hooks.user_prompt_submit"],
                   input=payload.encode("utf-8"), env=env, cwd=str(vault),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return time.perf_counter() - t0


def measure_hook(index: Dict[str, Any], *, repeats: int = 3) -> Dict[str, Any]:
    """The hook's end-to-end time on one prompt, each version, and on a prompt
    too short to rank (the process cost that does not depend on the index).

    The vault is a temporary one holding only the index, with reflex on and
    no judge, so the hook ranks, gates and returns without a network call.
    """
    prompt = prompt_for(index)
    out: Dict[str, Any] = {"prompt": prompt}
    with tempfile.TemporaryDirectory() as tmp:
        for version, data in (("v1", v1_bytes(index)), ("v2", reflex_index.encode_index(index))):
            vault = Path(tmp) / version
            (vault / ".mnemo").mkdir(parents=True)
            (vault / ".mnemo" / reflex_index.INDEX_FILENAME).write_bytes(data)
            out[version] = min(run_hook(vault, prompt) for _ in range(max(1, repeats)))
            out[f"{version}_short_prompt"] = min(
                run_hook(vault, SHORT_PROMPT) for _ in range(max(1, repeats)))
    return out


def _project_of(index: Dict[str, Any]) -> str:
    for doc in index["docs"].values():
        if doc.get("projects"):
            return doc["projects"][0]
    return PROJECT


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--vault", type=Path)
    ap.add_argument("--docs", type=int, default=4078)
    ap.add_argument("--terms", type=int, default=50346)
    ap.add_argument("--postings", type=int, default=541875)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--hook", action="store_true",
                    help="also time the UserPromptSubmit hook process on one prompt")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.vault is not None:
        loaded = reflex_index.load_index(args.vault.expanduser())
        if loaded is None:
            print("no readable reflex index in that vault", file=sys.stderr)
            return 1
        index = materialize(loaded)
        project = _project_of(index)
    else:
        index = synthetic_index(args.docs, args.terms, args.postings)
        project = PROJECT
    report = measure(index, repeats=args.repeats, project=project)
    report["write_cost"] = write_cost(index, repeats=args.repeats)
    if args.hook:
        report["hook_s"] = measure_hook(index, repeats=args.repeats)
    prov = _provenance.provenance(__file__, argv if argv is not None else sys.argv[1:],
                                  vault=args.vault)
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1))
        return 0
    print(f"index: {report['docs']} docs, {report['terms']} terms, "
          f"{report['postings']} postings")
    for v in ("v1", "v2"):
        r = report[v]
        print(f"{v}: {r['bytes']:>12,} bytes  load {r['load_s']:.3f} s  "
              f"load+decide {r['decide_s']:.3f} s  write {r['write_s']:.3f} s")
    print(f"v2/v1: bytes {report['bytes_ratio']:.3f}  load {report['load_ratio']:.3f}  "
          f"same scores: {report['same_scores']}")
    cost = report["write_cost"]
    print(f"v2 write: {cost['plain_s']:.3f} s, flushed {cost['durable_s']:.3f} s "
          f"(+{cost['flush_s']:.3f} s), flushed and keeping .prev {cost['kept_s']:.3f} s; "
          f".prev holds {cost['prev_bytes']:,} bytes")
    hook = report.get("hook_s")
    if hook:
        print(f"hook, one prompt: v1 {hook['v1']:.3f} s  v2 {hook['v2']:.3f} s  "
              f"(a prompt too short to rank: {hook['v2_short_prompt']:.3f} s)")
    print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
