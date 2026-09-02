"""Grade legacy feedback rules against the evidence rules, with a byte-exact undo.

The vault accumulated ~1400 auto-extracted ``shared/feedback/*.md`` pages under
the pre-evidence extractor. Many are generic aphorisms, near-duplicate families,
or session narrative. This module runs one LLM pass over them, mechanically
*validates* every verdict against the same quote-verification the extractor now
uses (:mod:`mnemo.core.corrections`), saves the result as a reviewable plan, and
applies it under an archive directory that holds byte-for-byte originals so
:func:`undo` can put the vault back exactly as it was.

Split of responsibility on purpose: :func:`plan` is the only function that talks
to an LLM, :func:`apply` never does. The maintainer reviews the JSON in between.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from mnemo.core import corrections, llm, transcript
from mnemo.core.extract.scanner import _normalize_slug
from mnemo.core.filters import derive_rule_slug
from mnemo.core.reclassify_apply import apply, undo  # noqa: F401 — public API
from mnemo.core.reclassify_types import (
    ApplyReport,  # noqa: F401 — public API
    Plan,
    RuleDoc,
    Verdict,
    split_frontmatter,
)

VERDICTS = ("keep", "demote", "merge", "archive")
PLAN_FILENAME = "reclassify-plan.json"

BRIEFING_MARKER = "/briefings/sessions/"
_MAX_TURNS = 40
_MAX_TURN_CHARS = 300
_MAX_DECISIONS_CHARS = 1500
_MAX_BODY_CHARS = 500
_MAX_MEMORY_CHARS = 400

RECLASSIFY_SYSTEM_PROMPT = (
    "You grade rules that were auto-extracted from coding sessions. "
    "For each rule decide ONE verdict:\n"
    "- keep: a USER QUOTE in the provided context supports this rule as something the user "
    "told the assistant to do or not do. You MUST copy that quote verbatim into `quote` and "
    "name its briefing path in `source`.\n"
    "- demote: real, reusable project knowledge (a config value, a deploy step, an API gotcha) "
    "but no user quote establishes it as a correction.\n"
    "- merge: states the same rule as another slug in this batch or in the known-slugs list; "
    "put that slug in `target`.\n"
    "- archive: generic best practice any engineer knows, session narrative, a one-off decision, "
    "or text that reads like tool instructions rather than a rule.\n"
    'Output JSON only: {"verdicts": [{"slug": ..., "verdict": ..., "target": ..., "quote": ..., '
    '"source": ..., "reason": ...}]} with one entry per input slug.'
)


# ---------------------------------------------------------------- collection


def collect_rules(vault_root: Path) -> list[RuleDoc]:
    """Every live ``shared/feedback/*.md`` page, proposals excluded."""
    out: list[RuleDoc] = []
    folder = Path(vault_root) / "shared" / "feedback"
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.md")):
        if path.name.endswith(".proposed.md") or path.name.endswith(".update-proposed.md"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = split_frontmatter(text)
        sources = fm.get("sources") or []
        if not isinstance(sources, list):
            sources = [sources]
        out.append(RuleDoc(
            path=path,
            slug=_normalize_slug(derive_rule_slug(fm, path.stem)),
            name=str(fm.get("name") or path.stem),
            fm=fm,
            body=body,
            sources=[str(s) for s in sources],
        ))
    return out


# ------------------------------------------------------------------ context


def _default_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def transcript_turns(vault_root: Path, briefing_rel: str, *, projects_root=None) -> list[str]:
    """The user's own turns from the jsonl session behind *briefing_rel*.

    Briefings are named after their session id, so the transcript is found by
    globbing every project directory for ``<session_id>.jsonl``. Bad JSON lines
    are skipped rather than fatal — old transcripts are not always clean.
    """
    root = Path(projects_root) if projects_root is not None else _default_projects_root()
    session_id = Path(briefing_rel).stem
    if not session_id or not root.is_dir():
        return []
    events: list[dict] = []
    for jsonl in sorted(root.glob(f"*/{session_id}.jsonl")):
        try:
            raw = jsonl.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(ev, dict):
                events.append(ev)
    turns = transcript.user_turns(events)
    return [t[:_MAX_TURN_CHARS] for t in turns[:_MAX_TURNS]]


def _section(markdown: str, header: str, limit: Optional[int] = None) -> str:
    idx = markdown.find(header)
    if idx == -1:
        return ""
    nxt = markdown.find("\n## ", idx + len(header))
    block = markdown[idx:nxt if nxt != -1 else len(markdown)].strip()
    if limit is not None and len(block) > limit:
        block = block[:limit] + "…"
    return block


def context_for(rule: RuleDoc, vault_root: Path, *, projects_root=None) -> str:
    """Everything the grader needs to judge one rule: decisions, corrections, turns."""
    vault_root = Path(vault_root)
    chunks: list[str] = []
    for src in rule.sources:
        src = str(src)
        path = vault_root / src
        if BRIEFING_MARKER in src.replace("\\", "/"):
            text = ""
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                pass
            parts = [f"briefing: {src}"]
            decisions = _section(text, "## Decisions made", _MAX_DECISIONS_CHARS)
            if decisions:
                parts.append(decisions)
            corr = _section(text, corrections.SECTION_HEADER)
            if corr:
                parts.append(corr)
            turns = transcript_turns(vault_root, src, projects_root=projects_root)
            if turns:
                parts.append("user turns:")
                parts.extend(f"{i}. {t}" for i, t in enumerate(turns, 1))
            chunks.append("\n".join(parts))
        else:
            try:
                head = path.read_text(encoding="utf-8")[:_MAX_MEMORY_CHARS]
            except OSError:
                continue
            chunks.append(f"memory: {src}\n{head}")
    return "\n\n".join(chunks)


# ------------------------------------------------------------------- prompt


def build_prompt(batch: list[RuleDoc], contexts: dict, known_slugs: list) -> str:
    lines: list[str] = ["Grade each of these rules.", ""]
    for rule in batch:
        lines.append(f"- {rule.slug}: {rule.name}")
        body = " ".join(rule.body.split())[:_MAX_BODY_CHARS]
        lines.append(f"  body: {body}")
        ctx = contexts.get(rule.slug) or "(no context found)"
        lines.append("  context: " + ctx.replace("\n", "\n    "))
        lines.append("")
    lines.append("Known slugs: " + ", ".join(known_slugs))
    return "\n".join(lines)


def parse_verdicts(text: str) -> list[Verdict]:
    payload = llm._parse_llm_json(text)
    raw = payload.get("verdicts")
    if not isinstance(raw, list):
        return []
    out: list[Verdict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        out.append(Verdict(
            slug=slug,
            verdict=str(item.get("verdict") or "").strip().lower(),
            target=(str(item["target"]).strip() or None) if item.get("target") else None,
            quote=(str(item["quote"]) or None) if item.get("quote") else None,
            source=(str(item["source"]).strip() or None) if item.get("source") else None,
            reason=str(item.get("reason") or ""),
        ))
    return out


# ---------------------------------------------------------------- validation


def _briefing_sources(rule: RuleDoc) -> list[str]:
    return [s for s in rule.sources if BRIEFING_MARKER in str(s).replace("\\", "/")]


def _verify_quote(
    quote: str, rule: RuleDoc, vault_root: Path, *, projects_root
) -> Optional[str]:
    """The briefing path whose corrections or transcript contain *quote*, else None."""
    if not quote:
        return None
    for src in _briefing_sources(rule):
        try:
            text = (Path(vault_root) / src).read_text(encoding="utf-8")
        except OSError:
            text = ""
        for item in corrections.parse_section(text):
            if corrections.quote_matches_turn(quote, item.quote):
                return src
        for turn in transcript_turns(vault_root, src, projects_root=projects_root):
            if corrections.quote_matches_turn(quote, turn):
                return src
    return None


def validate(
    verdicts: list, rules_by_slug: dict, vault_root: Path, *, projects_root=None
) -> list[Verdict]:
    """Downgrade every verdict the vault's own files do not support.

    The LLM is never trusted about a quote: a ``keep`` survives only when the
    quote really appears in a correction or a user turn of one of the rule's own
    briefing sources — the same bar :mod:`mnemo.core.corrections` sets.
    """
    out: list[Verdict] = []
    for v in verdicts:
        verdict = (v.verdict or "").strip().lower()
        target, source, reason = v.target, v.source, v.reason
        if verdict not in VERDICTS:
            verdict, reason = "archive", reason or "unknown-verdict"
        elif verdict == "merge":
            if not target or target == v.slug or target not in rules_by_slug:
                verdict, target, reason = "demote", None, reason or "merge-target-missing"
        elif verdict == "keep":
            rule = rules_by_slug.get(v.slug)
            matched = None
            if rule is not None:
                matched = _verify_quote(v.quote or "", rule, vault_root, projects_root=projects_root)
            if matched is None:
                verdict, reason = "demote", reason or "quote-unverified"
            elif not source:
                source = matched
        out.append(Verdict(
            slug=v.slug, verdict=verdict, target=target,
            quote=v.quote, source=source, reason=reason,
        ))
    return out


# --------------------------------------------------------------------- plan


def plan(
    vault_root: Path,
    *,
    model: str,
    timeout: int,
    batch_size: int = 10,
    limit: Optional[int] = None,
    projects_root=None,
    call: Callable = llm.call,
) -> Plan:
    vault_root = Path(vault_root)
    rules = collect_rules(vault_root)
    if limit is not None:
        rules = rules[:limit]
    rules_by_slug = {r.slug: r for r in rules}
    known_slugs = [r.slug for r in rules]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    llm_calls = 0
    collected: list[Verdict] = []

    for start in range(0, len(rules), max(1, batch_size)):
        batch = rules[start:start + max(1, batch_size)]
        contexts = {
            r.slug: context_for(r, vault_root, projects_root=projects_root) for r in batch
        }
        prompt = build_prompt(batch, contexts, known_slugs)
        try:
            response = call(
                prompt, system=RECLASSIFY_SYSTEM_PROMPT, model=model, timeout=timeout,
            )
            llm_calls += 1
            parsed = parse_verdicts(response.text)
        except (llm.LLMSubprocessError, llm.LLMParseError):
            llm_calls += 1
            collected.extend(
                Verdict(slug=r.slug, verdict="demote", reason="llm-error") for r in batch
            )
            continue
        batch_slugs = {r.slug for r in batch}
        collected.extend(v for v in parsed if v.slug in batch_slugs)

    validated = validate(collected, rules_by_slug, vault_root, projects_root=projects_root)

    # Exactly one verdict per input slug, in input order.
    first: dict = {}
    for v in validated:
        first.setdefault(v.slug, v)
    final = [
        first.get(r.slug) or Verdict(slug=r.slug, verdict="demote", reason="no-verdict")
        for r in rules
    ]
    return Plan(run_id=run_id, llm_calls=llm_calls, verdicts=final)


def _plan_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / PLAN_FILENAME


def save_plan(vault_root: Path, plan_obj: Plan) -> Path:
    path = _plan_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(plan_obj), indent=2), encoding="utf-8")
    return path


def load_plan(vault_root: Path) -> Optional[Plan]:
    path = _plan_path(vault_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    verdicts = [
        Verdict(
            slug=str(d.get("slug") or ""),
            verdict=str(d.get("verdict") or ""),
            target=d.get("target"),
            quote=d.get("quote"),
            source=d.get("source"),
            reason=str(d.get("reason") or ""),
        )
        for d in payload.get("verdicts") or []
        if isinstance(d, dict)
    ]
    return Plan(
        run_id=str(payload.get("run_id") or ""),
        llm_calls=int(payload.get("llm_calls") or 0),
        verdicts=verdicts,
    )
