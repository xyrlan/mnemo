"""`mnemo dedup-rules` — the two dedupes: names that match, and lessons that do.

The default is the old one: consolidate `shared/<type>/*.md` files sharing the
same `name:`. Dry-run unless `--apply`; canonical = most `sources[]` (tie →
newer `extracted_at`), duplicates deleted, `sources[]` + frontmatter project(s)
unioned onto the canonical.

`--judge` is the other half (#409). Two rules that say one thing in different
words share no `name:` and no token overlap — #187 measured a real duplicate at
Jaccard 0.136 against a p90 of 0.131 over 79,800 unrelated pairs, so no
threshold separates them. This asks a judge that reads the pair, over every
pair of live rules inside one topic bucket of one project, and writes a queue
for a curator to read. It is a dry run unless `--send`, it never merges
anything, and `--merge KEEP DROP` — the only thing here that changes a rule —
takes both slugs from the maintainer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from mnemo.cli.parser import command


# ------------------------------------------------------- the name-keyed plan


def _by_name(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core.dedup_rules import plan_dedup
    from mnemo.core.filters import parse_frontmatter

    vault = cli._resolve_vault()
    plan = plan_dedup(vault)

    if not plan.groups:
        print("no duplicates found")
        return 0

    print(f"{len(plan.groups)} group(s) with duplicate names:\n")
    for g in plan.groups:
        canon_fm = parse_frontmatter(g.canonical.read_text(encoding="utf-8"))
        name = canon_fm.get("name", "")
        canon_rel = g.canonical.relative_to(vault)
        dup_rels = ", ".join(p.name for p in g.duplicates)
        print(f"  '{name}'")
        print(f"    keep:   {canon_rel}")
        print(f"    delete: {dup_rels}")
        print(f"    merged_sources: {len(g.merged_sources)}  projects: {g.merged_projects}")

    if not getattr(args, "apply", False):
        print("\n(dry-run — pass --apply to execute)")
        return 0

    plan.apply()
    print("\napplied.")
    return 0


# ------------------------------------------------------------- the judge


def _n(value: int) -> str:
    """Thousands separators: the whole vault is a five-figure pair count."""
    return format(int(value), ",d")


def _scope_line(args: argparse.Namespace, bucket_list: List[dict]) -> str:
    where = "every project" if not args.project else args.project
    if args.topic:
        where += " / " + args.topic
    return "%s: %d bucket%s with two or more rules" % (
        where, len(bucket_list), "" if len(bucket_list) == 1 else "s")


def _print_queue(rows: List[dict], summary: dict, path: Path) -> None:
    from mnemo.core import dedup_judge as dj

    print("%d pair%s the judge scores >= %s, of %d judged (%d under the Jaccard "
          "gate of %s)" % (summary["queued"], "" if summary["queued"] == 1 else "s",
                           summary["at"], summary["judged"],
                           summary["queued_under_gate"], dj.JACCARD_GATE))
    for row in rows:
        print("")
        print("[%.2f] jaccard %.3f, rank %d/%d in %s/%s"
              % (row["score"], row["jaccard"], row["jaccard_rank"],
                 row["bucket_pairs"], row["project"], row["topic"]))
        for slug, name, opening in (
            (row["a"], row["name_a"], row["opening_a"]),
            (row["b"], row["name_b"], row["opening_b"]),
        ):
            print("  %s — %s" % (slug, name))
            print("      %s" % opening)
    print("")
    print("queue: %s" % path)
    print("nothing was merged. `mnemo dedup-rules --merge KEEP DROP` folds DROP "
          "into KEEP; which is which is yours to decide.")


def _judge(args: argparse.Namespace) -> int:
    import sys
    import time

    from mnemo import cli
    from mnemo.core import dedup_judge as dj
    from mnemo.core.config import load_config
    from mnemo.core.mcp import rerank

    vault = cli._resolve_vault()
    chosen = dj.provider_settings(load_config())
    model = chosen["model"]

    bucket_list = dj.buckets(vault, project=args.project, topic=args.topic)
    if not bucket_list:
        print("no topic bucket holds two rules at that scope — nothing to compare.")
        return 0
    bodies, names = dj.load_bodies(vault, bucket_list, max_chars=args.max_chars)
    pairs, bucket_total = dj.plan_pairs(bucket_list, bodies)
    answered = dj.load_answers(vault, model=model)
    todo = dj.missing(pairs, answered)
    cost = dj.estimate_pairs([(r["a"], r["b"]) for r in todo], bodies)
    over_cap = len(todo) > args.max_pairs
    biggest = dj.largest_buckets(bucket_list)

    if not args.send:
        if args.json:
            print(json.dumps({
                "buckets": len(bucket_list), "pairs": len(pairs),
                "bucket_pairs": bucket_total, "answered": len(pairs) - len(todo),
                "to_ask": len(todo), "estimate": cost, "model": model,
                "max_pairs": args.max_pairs, "over_max_pairs": over_cap,
                "pairs_over_gate": sum(1 for r in pairs if r["jaccard"] >= dj.JACCARD_GATE),
                "largest_buckets": biggest, "url": rerank.TYPESAFE_URL,
            }, indent=2, ensure_ascii=False))
            return 0
        print("dry run — nothing has left this machine.")
        print("  " + _scope_line(args, bucket_list))
        print("  %s pair%s to compare (%s bucket pairs; %s of them the same pair "
              "in another topic or project)"
              % (_n(len(pairs)), "" if len(pairs) == 1 else "s", _n(bucket_total),
                 _n(bucket_total - len(pairs))))
        if len(todo) != len(pairs):
            print("  %s already answered by %s; %s left to ask"
                  % (_n(len(pairs) - len(todo)), model, _n(len(todo))))
        print("  ~%s input tokens, ~$%s at $%s per million (%s)"
              % (_n(cost["tokens"]), cost["usd"], dj.USD_PER_MTOK, model))
        print("  the Jaccard gate (>= %s) catches %s of them — the rest is what "
              "this is for"
              % (dj.JACCARD_GATE,
                 _n(sum(1 for r in pairs if r["jaccard"] >= dj.JACCARD_GATE))))
        if len(bucket_list) > 1:
            print("  largest buckets:")
            for b in biggest:
                print("    %8s pairs  %s / %s (%d rules)"
                      % (_n(b["pairs"]), b["project"], b["topic"], b["rules"]))
        if over_cap:
            print("  refusing --send: %s pairs is over --max-pairs %s. Narrow it "
                  "with --project/--topic, or raise the cap."
                  % (_n(len(todo)), _n(args.max_pairs)))
        else:
            print("  --send posts both rule bodies of every pair to %s, a third "
                  "party. Answers are saved as they arrive (%s), so a re-run "
                  "asks only for what is missing."
                  % (rerank.TYPESAFE_URL, dj.answers_path(vault)))
        return 0

    if over_cap:
        print("error: %s pairs is over --max-pairs %s; nothing was sent. Narrow "
              "it with --project/--topic, or raise the cap."
              % (_n(len(todo)), _n(args.max_pairs)), file=sys.stderr)
        for b in biggest:
            print("  %8s pairs  %s / %s (%d rules)"
                  % (_n(b["pairs"]), b["project"], b["topic"], b["rules"]), file=sys.stderr)
        return 2

    # A run whose answers are all on disk asks for nothing, so it needs no key:
    # rebuilding the queue at another ``--at`` must not depend on the provider.
    if todo:
        key, _source = rerank.resolve_key(chosen)
        if not key:
            print("error: --send needs a key for %s and there is none, in %s or "
                  "in the secrets file. `mnemo rerank --setup` stores one."
                  % (chosen["provider"], chosen["keyEnv"]), file=sys.stderr)
            return 2

        client = rerank.typesafe_client(key, model=model, timeout=args.timeout)
        print("asking %s about %s pair%s (~$%s); answers are saved as they arrive."
              % (model, _n(len(todo)), "" if len(todo) == 1 else "s", cost["usd"]))

        def flush(fresh: Dict[tuple, dict]) -> None:
            merged = dict(answered)
            merged.update(fresh)
            dj.save_answers(vault, merged)

        answered.update(dj.judge_pairs(todo, bodies, client, model=model,
                                       workers=args.workers, flush=flush))
        dj.save_answers(vault, answered)
    else:
        print("every pair is already answered by %s; nothing was sent." % model)

    queue = dj.build_queue(pairs, answered, bodies, names, at=args.at)
    summary = dj.summarize(pairs, answered, queue, at=args.at)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": model, "at": args.at,
        "scope": {"project": args.project, "topic": args.topic},
        "summary": summary, "pairs": queue,
    }
    path = dj.write_queue(vault, payload)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    _print_queue(queue, summary, path)
    return 0


# ------------------------------------------------------------- the merge


def _rule_paths(vault_root: Path) -> Dict[str, Path]:
    """slug -> live rule file, from the rule-activation index.

    The index is what ``list_rules_by_topic`` answers from, so a slug the
    queue names resolves here or it is not a live rule. A path is never
    rebuilt as ``shared/<type>/{slug}.md``: on this vault the file stem and
    the slug disagree for most rules.
    """
    from mnemo.core import rule_activation

    index = rule_activation.load_index(vault_root)
    out: Dict[str, Path] = {}
    for slug, rule in ((index or {}).get("rules") or {}).items():
        page_type = rule.get("type", "feedback")
        stem = rule.get("file_stem") or slug
        path = Path(vault_root) / "shared" / page_type / ("%s.md" % stem)
        if path.exists():
            out[slug] = path
    return out


def _merge(args: argparse.Namespace) -> int:
    """Fold DROP into KEEP through ``reclassify``'s own ``merge`` verdict.

    Same execution, same archive, same undo: sources unioned onto KEEP, DROP
    moved under ``shared/_archive/reclassify-<run>/merged/`` with a manifest,
    and ``mnemo reclassify --undo <run>`` restores both byte for byte. What
    this does not do is decide anything — the judge ranks pairs, a person
    names KEEP and DROP.
    """
    import sys
    from datetime import datetime

    from mnemo import cli
    from mnemo.core.reclassify_apply import apply as apply_plan
    from mnemo.core.reclassify_types import Plan, Verdict

    keep, drop = args.merge
    vault = cli._resolve_vault()
    if keep == drop:
        print("error: KEEP and DROP are the same slug.", file=sys.stderr)
        return 2
    paths = _rule_paths(vault)
    for role, slug in (("KEEP", keep), ("DROP", drop)):
        if slug not in paths:
            print("error: %s %r is not a live rule in this vault." % (role, slug),
                  file=sys.stderr)
            return 2

    run_id = "dedupe-%s" % datetime.now().strftime("%Y%m%d-%H%M%S")
    verdict = Verdict(
        slug=drop, verdict="merge", target=keep,
        reason="mnemo dedup-rules --merge",
        path=paths[drop].relative_to(vault).as_posix(),
        target_path=paths[keep].relative_to(vault).as_posix(),
    )
    try:
        report = apply_plan(vault, Plan(run_id=run_id, llm_calls=0, verdicts=[verdict]))
    except RuntimeError as exc:  # VaultBusy: an extraction holds the vault
        print("error: %s" % exc, file=sys.stderr)
        return 1
    if report.merged != 1:
        for note in report.notes:
            print("  " + note, file=sys.stderr)
        for entry in report.skipped:
            print("  %s: %s" % (entry.get("slug"), entry.get("reason")), file=sys.stderr)
        print("error: nothing was merged.", file=sys.stderr)
        return 1
    print("merged %s into %s" % (drop, keep))
    print("  sources unioned onto %s" % paths[keep].relative_to(vault).as_posix())
    print("  %s archived under %s" % (drop, report.archive_dir))
    print("  undo: mnemo reclassify --undo %s" % run_id)
    return 0


@command("dedup-rules")
def cmd_dedup_rules(args: argparse.Namespace) -> int:
    if getattr(args, "merge", None):
        return _merge(args)
    if getattr(args, "judge", False):
        return _judge(args)
    return _by_name(args)
