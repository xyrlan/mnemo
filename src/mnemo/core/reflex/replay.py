"""``mnemo replay`` — your own transcripts, against your own vault.

Every other harness answers the maintainer's question ("did this ranking
change help?") with ranks and rates. This one answers the user's: *would a
thing I told Claude in an earlier session have come back to me when I hit the
same ground again?* It reads every Claude Code transcript on disk, replays
each human prompt through the same decision the ``UserPromptSubmit`` hook
makes (:mod:`mnemo.core.reflex.decide` — the hook, not a copy of it), and
sorts every rule that would have fired by *when the vault learned it*
relative to the prompt it fires on:

- **carried** — the rule was extracted from a different, earlier session.
  This is the only bucket the vault can take credit for: without it, nothing
  crosses a session boundary. "Without the vault" is therefore not a second
  run; it is this bucket's complement.
- **hindsight** — the rule was extracted from the very session it fires in.
  The correction was typed a few turns away; the vault could not have helped.
- **not yet learned** — today's vault fires, but the rule was extracted after
  the prompt. Replaying a vault over its own past over-counts here, so it is
  shown and not counted.

Inside *carried*, a rule that carries a verified ``evidence.quote`` is one the
user demonstrably asked for in their own words; that count is the strongest
claim this command makes. The issue that asked for this (#237) ruled out
productivity, session length and token claims, and so does the output.

That label has been written by three different bars (#244), so the report
splits it by provenance (#257): **gate-verified** — the quote passes
:func:`mnemo.core.extract.evidence.page_verifies` against a source briefing
*today* — and **label only** — ``confidence: verified`` whose quote the gate
cannot re-check, chiefly the 2026-09-02 ``mnemo reclassify`` keep verdicts,
which cite briefings written before the ``## Corrections`` section existed.
Both are printed; the gate-verified number is the one to quote.

Composed from what already exists: transcript discovery and the
``sources:`` → session map are :mod:`mnemo.core.mcp.recall_sessions`'s; the
decision is the hook's. What could *not* be composed: ``mnemo recall`` scores
``list_rules_by_topic`` calls from the MCP access log — a different retrieval
path (topic-filtered, model-initiated) from the per-prompt reflex, so its
cases are not prompts and its answer is not "what would have been injected".
``recall-sessions`` scores one prompt per session against labelled rules; the
replay scores every prompt and lets the gates decide, with no label at all.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from mnemo.core import ci_corrections
from mnemo.core.mcp.recall_sessions import _transcripts_by_session
from mnemo.core.reflex.decide import decide, doc_token_sets
from mnemo.core.transcript import SYNTHETIC_TURN as _SYNTHETIC
from mnemo.core.transcript import plain_user_text as _plain_text

# Below this many prompts, a percentage is noise dressed as a number
# (`recall-80pct-was-a-sampling-artifact`: n=17 carried ±22.7%). Counts are
# always printed; the rate is not.
MIN_PROMPTS_FOR_RATE = 30

CARRIED = "carried"
HINDSIGHT = "hindsight"
NOT_YET_LEARNED = "not_yet_learned"
UNDATED = "undated"

_BUCKET_ORDER = (CARRIED, HINDSIGHT, NOT_YET_LEARNED, UNDATED)


@dataclass(frozen=True)
class Prompt:
    session_id: str
    project: str
    ts: float  # unix seconds
    text: str


@dataclass(frozen=True)
class RuleFacts:
    taught_by: frozenset  # session ids the rule was extracted from
    learned_at: Optional[float]  # unix seconds the page entered the vault
    correction_backed: bool  # verified feedback page citing the user's words
    gate_verified: bool = False  # ...and the quote passes today's evidence gate
    origin: Optional[str] = None  # which channel corrected the model: user | ci (#272)


@dataclass(frozen=True)
class Injection:
    session_id: str
    project: str
    ts: float
    slug: str
    bucket: str
    correction_backed: bool
    gate_verified: bool = False
    origin: Optional[str] = None


# --- reading the transcripts --------------------------------------------------

def _parse_ts(raw: object) -> Optional[float]:
    """ISO-8601 → unix seconds. Naive values are taken as local time, which is
    how the extractor stamps ``extracted_at``; ``Z`` is accepted on 3.8."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def briefing_projects(vault_root: Path) -> dict[str, str]:
    """session id → project, from every briefing filed under ``bots/``.

    The fallback for a transcript whose working directory is gone (a deleted
    worktree resolves to its leaf name, which no rule is scoped to).
    """
    out: dict[str, str] = {}
    for md in sorted(Path(vault_root).glob("bots/*/briefings/sessions/*.md")):
        out.setdefault(md.stem, md.parents[2].name)
    return out


def default_project_for(vault_root: Path) -> Callable[[str, str], str]:
    """The hook's own resolution when the tree still exists; the briefing's
    record of the project when it does not.

    A removed dispatch tree (``<repo>-wt-<n>``, ``<repo>-wt-c-<slug>``) is
    folded into its sibling repo first (#334). While the child ran, its
    ``.git`` pointer led the hook to the repo, so that is the project it
    queried; the tree's basename is a namespace no rule is filed under, and
    replaying against it scored those prompts against the universal rules
    alone. The fold also wins over the briefing, which a child delivered
    before #301 may have filed under that same basename.
    """
    from mnemo.core.agent import resolve_canonical_agent
    from mnemo.core.backfill.discover import fold_gone_dispatch_tree

    by_session = briefing_projects(vault_root)

    def _resolve(cwd: str, session_id: str) -> str:
        if cwd:
            cwd = fold_gone_dispatch_tree(cwd)
        if cwd and Path(cwd).is_dir():
            return resolve_canonical_agent(cwd).name
        if session_id in by_session:
            return by_session[session_id]
        return resolve_canonical_agent(cwd).name if cwd else "unknown"

    return _resolve


def read_prompts(path: Path, session_id: str, project_for: Callable[[str, str], str]) -> list[Prompt]:
    """Every prompt a person typed in one transcript, in order.

    Same exclusions as :func:`mnemo.core.transcript.user_turns` — slash-command
    output, hook context and tool results are transcript plumbing wearing a
    user turn — plus sidechain (subagent) turns, which never reach the hook.
    """
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    project: Optional[str] = None
    out: list[Prompt] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        if entry.get("isSidechain") or entry.get("isMeta"):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        text = _plain_text(msg.get("content"))
        if not text or _SYNTHETIC.search(text):
            continue
        ts = _parse_ts(entry.get("timestamp"))
        if ts is None:
            continue
        if project is None:
            project = project_for(str(entry.get("cwd") or ""), session_id)
        out.append(Prompt(session_id=session_id, project=project, ts=ts, text=text))
    return out


def collect_prompts(
    vault_root: Path,
    *,
    projects_root: Optional[Path] = None,
    project_for: Optional[Callable[[str, str], str]] = None,
) -> list[Prompt]:
    """Every human prompt on disk, oldest first — the order the hook saw them."""
    if projects_root is None:
        projects_root = Path.home() / ".claude" / "projects"
    if project_for is None:
        project_for = default_project_for(Path(vault_root))
    prompts: list[Prompt] = []
    for session_id, path in sorted(_transcripts_by_session(Path(projects_root)).items()):
        prompts.extend(read_prompts(path, session_id, project_for))
    prompts.sort(key=lambda p: (p.ts, p.session_id))
    return prompts


# --- reading the vault ---------------------------------------------------------

# A briefing citation, wherever it sits in the string: ``sources:`` carries the
# bare path, and a reclassified page's ``evidence.source`` wraps the same path
# in prose (``briefing: bots/… — user turns, turn 3``).
_BRIEFING_CITATION = re.compile(r"bots/[^/\s]+/briefings/sessions/([^/\s]+?)\.md")


def _session_ids_in(citations: list) -> set[str]:
    return {m.group(1) for src in citations for m in _BRIEFING_CITATION.finditer(str(src))}


def rule_facts(vault_root: Path) -> dict[str, RuleFacts]:
    """What the replay needs to know about every consumer-visible rule.

    Same walk and visibility gate as the reflex index builder, so a slug the
    index can rank always has facts here.

    ``correction_backed`` is the label as written (a quote plus a confidence
    some channel verified). ``gate_verified`` re-asks the evidence gate of that
    page today, with the very predicate ``verify_page`` uses — one briefing
    read per verified page, nothing cached, so the same vault gives the same
    split.

    ``origin`` says which channel corrected the model (#272): ``user`` for a
    quote the person typed, ``ci`` for one a red run printed. A CI page's quote
    is checked by the run that printed it, so the briefing gate is not asked of
    it — see :mod:`mnemo.core.ci_corrections`.
    """
    from mnemo.core.extract.evidence import page_verifies
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible
    from mnemo.core.reclassify_types import split_frontmatter

    vault_root = Path(vault_root)
    out: dict[str, RuleFacts] = {}
    for page_type in ("feedback", "user", "reference", "project"):
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, _body = split_frontmatter(text)
            if not is_consumer_visible(md, fm, vault_root):
                continue
            slug = derive_rule_slug(fm, md.stem)
            sources_raw = fm.get("sources") or []
            if isinstance(sources_raw, str):
                sources_raw = [sources_raw]
            taught_by = _session_ids_in([s for s in sources_raw if isinstance(s, str)])
            evidence = fm.get("evidence")
            quote = ""
            if isinstance(evidence, dict):
                quote = str(evidence.get("quote") or "").strip()
                taught_by |= _session_ids_in([str(evidence.get("source") or "")])
            learned_at = None
            for key in ("extracted_at", "promoted_at", "extraction_run"):
                learned_at = _parse_ts(fm.get(key))
                if learned_at is not None:
                    break
            confidence = str(fm.get("confidence") or "")
            origin = ci_corrections.origin_of(confidence) if quote else None
            backed = origin is not None
            # A CI quote is checked by the run that printed it, not by a
            # briefing's Corrections, so the human gate is not asked of it
            # (#272). Asking would fail every CI page mechanically and report
            # it as "label only", which is the opposite of what it is.
            if origin == ci_corrections.ORIGIN_CI:
                gate_verified = True
            else:
                gate_verified = backed and page_verifies(
                    evidence, [s for s in sources_raw if isinstance(s, str)], vault_root,
                )
            out[slug] = RuleFacts(
                taught_by=frozenset(taught_by),
                learned_at=learned_at,
                correction_backed=backed,
                gate_verified=gate_verified,
                origin=origin,
            )
    return out


def classify(facts: Optional[RuleFacts], prompt: Prompt) -> str:
    """Which bucket a rule firing on this prompt belongs to."""
    if facts is None:
        return UNDATED
    if prompt.session_id in facts.taught_by:
        return HINDSIGHT
    if facts.learned_at is None:
        return UNDATED
    return CARRIED if facts.learned_at <= prompt.ts else NOT_YET_LEARNED


# --- the replay ----------------------------------------------------------------

@dataclass
class Replay:
    prompts: list[Prompt]
    injections: list[Injection]
    silence: dict[str, int]  # reason → prompts silenced for it
    fired_prompts: int  # prompts where at least one rule survived cap + dedupe


def run(
    prompts: list[Prompt],
    index: dict,
    facts: dict[str, RuleFacts],
    *,
    reflex_cfg: dict,
    overrides_for: Optional[Callable[[str], dict]] = None,
) -> Replay:
    """Replay the prompts, oldest first, through the hook's decision.

    The hook's two stateful guards are simulated from the transcript clock:
    the per-session emission cap, and the vault-wide injected cache that
    resets on the day rollover. What is *not* simulated is the export
    suppression (rules already in a tree's rules file) — the export manifest
    describes the tree as it is today, not as it was.
    """
    overrides_for = overrides_for or (lambda _project: {})
    doc_tokens = doc_token_sets(index)
    max_per = int(reflex_cfg.get("maxEmissionsPerSession", 10))
    overrides_cache: dict[str, dict] = {}

    emitted_per_session: dict[str, int] = {}
    injected_cache: set[str] = set()
    cache_day: Optional[str] = None

    injections: list[Injection] = []
    silence: dict[str, int] = {}
    fired_prompts = 0

    for prompt in prompts:
        day = datetime.fromtimestamp(prompt.ts).date().isoformat()
        if day != cache_day:
            injected_cache = set()
            cache_day = day

        if emitted_per_session.get(prompt.session_id, 0) >= max_per:
            silence["session_cap_reached"] = silence.get("session_cap_reached", 0) + 1
            continue

        if prompt.project not in overrides_cache:
            overrides_cache[prompt.project] = overrides_for(prompt.project) or {}
        decision = decide(
            index, project=prompt.project, prompt=prompt.text,
            reflex_cfg=reflex_cfg, overrides=overrides_cache[prompt.project],
            doc_tokens=doc_tokens,
        )
        if not decision.accepted:
            reason = decision.silence_reason or "index_missing"
            silence[reason] = silence.get(reason, 0) + 1
            continue

        survivors = [s for s in decision.accepted if s not in injected_cache]
        if not survivors:
            silence["deduped"] = silence.get("deduped", 0) + 1
            continue

        fired_prompts += 1
        for slug in survivors:
            injected_cache.add(slug)
            emitted_per_session[prompt.session_id] = emitted_per_session.get(prompt.session_id, 0) + 1
            f = facts.get(slug)
            injections.append(Injection(
                session_id=prompt.session_id, project=prompt.project, ts=prompt.ts,
                slug=slug, bucket=classify(f, prompt),
                correction_backed=bool(f and f.correction_backed),
                gate_verified=bool(f and f.gate_verified),
                origin=f.origin if f else None,
            ))

    return Replay(prompts=prompts, injections=injections, silence=silence,
                  fired_prompts=fired_prompts)


# --- the report ----------------------------------------------------------------

def wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion — the one that behaves near 0."""
    if n <= 0:
        return (0.0, 0.0)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def aggregate(
    replay: Replay, *, vault_rules: int, correction_backed_rules: int, gate_verified_rules: int = 0,
    correction_backed_by_origin: Optional[dict] = None,
) -> dict:
    """Counts first, rates only where the sample carries them.

    A prompt lands in the *best* bucket any of its rules did: a prompt that
    got one carried rule and one hindsight rule is a prompt the vault helped
    with. Rule-level counts are reported alongside so nothing is hidden by
    that choice.

    Every ``correction_backed`` figure is the union; ``gate_verified`` and
    ``label_only`` are its two halves (#257). ``gate_verified_rules`` is how
    many of ``correction_backed_rules`` pass the gate today.
    """
    prompts = replay.prompts
    n = len(prompts)
    sessions = {p.session_id for p in prompts}

    by_prompt: dict[tuple[str, float], list[Injection]] = {}
    for inj in replay.injections:
        by_prompt.setdefault((inj.session_id, inj.ts), []).append(inj)

    prompt_bucket = {b: 0 for b in _BUCKET_ORDER}
    prompt_carried_backed = 0
    prompt_carried_gate = 0
    prompt_carried_label = 0
    for key, injs in by_prompt.items():
        best = min(injs, key=lambda i: _BUCKET_ORDER.index(i.bucket)).bucket
        prompt_bucket[best] += 1
        carried_backed = [i for i in injs if i.bucket == CARRIED and i.correction_backed]
        if carried_backed:
            prompt_carried_backed += 1
        # A prompt is credited to the stronger half when any of its rules earns it.
        if any(i.gate_verified for i in carried_backed):
            prompt_carried_gate += 1
        elif carried_backed:
            prompt_carried_label += 1

    inj_bucket = {b: 0 for b in _BUCKET_ORDER}
    for inj in replay.injections:
        inj_bucket[inj.bucket] += 1
    carried_injections = [i for i in replay.injections if i.bucket == CARRIED]
    carried_backed_injections = [i for i in carried_injections if i.correction_backed]
    carried_gate_injections = [i for i in carried_backed_injections if i.gate_verified]

    carried_slugs = {i.slug for i in carried_injections}
    carried_backed_slugs = {i.slug for i in carried_backed_injections}
    carried_gate_slugs = {i.slug for i in carried_gate_injections}

    # The number the issue asks for (#272): carried, correction-backed, split
    # by which channel did the correcting. Prompt-level too, since a prompt is
    # what a rule actually has to come back to.
    # A correction-backed rule always has an origin; an injection that predates
    # the origin axis (or a caller that omits it) is the user's, which is what
    # every backed rule was before CI became a source. Defaulting here rather
    # than dropping it keeps the two halves summing to the whole.
    def _origin_of(inj) -> str:
        return inj.origin or ci_corrections.ORIGIN_USER

    def _by_origin(items) -> dict[str, int]:
        out = {ci_corrections.ORIGIN_USER: 0, ci_corrections.ORIGIN_CI: 0}
        for it in items:
            out[_origin_of(it)] = out.get(_origin_of(it), 0) + 1
        return out

    carried_backed_by_origin = _by_origin(carried_backed_injections)
    carried_backed_slugs_by_origin = {
        origin: len({i.slug for i in carried_backed_injections if _origin_of(i) == origin})
        for origin in (ci_corrections.ORIGIN_USER, ci_corrections.ORIGIN_CI)
    }
    prompts_backed_by_origin = {ci_corrections.ORIGIN_USER: 0, ci_corrections.ORIGIN_CI: 0}
    for key, injs in by_prompt.items():
        origins = {
            _origin_of(i) for i in injs
            if i.bucket == CARRIED and i.correction_backed
        }
        for origin in origins:
            if origin in prompts_backed_by_origin:
                prompts_backed_by_origin[origin] += 1

    counts: dict[str, int] = {}
    for inj in carried_injections:
        counts[inj.slug] = counts.get(inj.slug, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    backed_origin_by_slug = {
        i.slug: _origin_of(i) for i in carried_backed_injections
    }
    top_carried = [
        {"slug": slug, "prompts": c, "correction_backed": slug in carried_backed_slugs,
         "gate_verified": slug in carried_gate_slugs,
         "origin": backed_origin_by_slug.get(slug)}
        for slug, c in top
    ]

    rate: Optional[dict] = None
    if n >= MIN_PROMPTS_FOR_RATE:
        lo, hi = wilson_interval(prompt_bucket[CARRIED], n)
        blo, bhi = wilson_interval(prompt_carried_backed, n)
        glo, ghi = wilson_interval(prompt_carried_gate, n)
        rate = {
            "carried": round(prompt_bucket[CARRIED] / n, 4),
            "carried_ci95": [round(lo, 4), round(hi, 4)],
            "carried_correction_backed": round(prompt_carried_backed / n, 4),
            "carried_correction_backed_ci95": [round(blo, 4), round(bhi, 4)],
            "carried_gate_verified": round(prompt_carried_gate / n, 4),
            "carried_gate_verified_ci95": [round(glo, 4), round(ghi, 4)],
            "fired": round(replay.fired_prompts / n, 4),
        }

    return {
        "harness": "replay",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault": {
            "rules": vault_rules,
            "correction_backed": correction_backed_rules,
            "gate_verified": gate_verified_rules,
            "label_only": correction_backed_rules - gate_verified_rules,
            "correction_backed_by_origin": dict(
                correction_backed_by_origin
                or {ci_corrections.ORIGIN_USER: correction_backed_rules,
                    ci_corrections.ORIGIN_CI: 0}
            ),
        },
        "transcripts": {
            "sessions": len(sessions),
            "prompts": n,
            "first": _iso(min(p.ts for p in prompts)) if prompts else None,
            "last": _iso(max(p.ts for p in prompts)) if prompts else None,
        },
        "prompts": {
            "total": n,
            "fired": replay.fired_prompts,
            "carried": prompt_bucket[CARRIED],
            "carried_correction_backed": prompt_carried_backed,
            "carried_gate_verified": prompt_carried_gate,
            "carried_label_only": prompt_carried_label,
            "carried_correction_backed_by_origin": prompts_backed_by_origin,
            "hindsight": prompt_bucket[HINDSIGHT],
            "not_yet_learned": prompt_bucket[NOT_YET_LEARNED],
            "undated": prompt_bucket[UNDATED],
        },
        "injections": {
            "total": len(replay.injections),
            "carried": inj_bucket[CARRIED],
            "carried_correction_backed": len(carried_backed_injections),
            "carried_gate_verified": len(carried_gate_injections),
            "carried_label_only": len(carried_backed_injections) - len(carried_gate_injections),
            "carried_correction_backed_by_origin": carried_backed_by_origin,
            "hindsight": inj_bucket[HINDSIGHT],
            "not_yet_learned": inj_bucket[NOT_YET_LEARNED],
            "undated": inj_bucket[UNDATED],
        },
        "rules": {
            "carried_distinct": len(carried_slugs),
            "carried_correction_backed_distinct": len(carried_backed_slugs),
            "carried_gate_verified_distinct": len(carried_gate_slugs),
            "carried_label_only_distinct": len(carried_backed_slugs - carried_gate_slugs),
            "carried_correction_backed_distinct_by_origin": carried_backed_slugs_by_origin,
        },
        "rate": rate,
        "min_prompts_for_rate": MIN_PROMPTS_FOR_RATE,
        "silence": dict(sorted(replay.silence.items())),
        "top_carried": top_carried,
        "not_simulated": [
            "export suppression (the rules file of each tree, as it was then)",
            "per-project threshold calibration older than today's",
        ],
        "not_measured": [
            "whether an injected rule changed the answer",
            "tokens, session length, or time saved",
            "what CLAUDE.md or auto-memory would have covered instead",
        ],
    }


def _pct(hits: int, n: int) -> str:
    return f"{100.0 * hits / n:.1f}%" if n else "–"


def format_report(report: dict) -> str:
    """The human form: one column of counts, the rate only when it means something."""
    t = report["transcripts"]
    p = report["prompts"]
    inj = report["injections"]
    v = report["vault"]
    n = p["total"]
    rate = report.get("rate")
    lines: list[str] = []
    span = f"{t['first']} → {t['last']}" if t.get("first") else "no dated prompts"
    lines.append("mnemo replay — your transcripts against your vault")
    lines.append("")
    lines.append(f"prompts replayed        {n:>6}   ({t['sessions']} sessions, {span})")
    # "you typed" stays exactly true while the user is the only source; once a
    # red run has taught something, the line says so instead of overclaiming.
    vault_origin = v.get("correction_backed_by_origin") or {}
    ci_rules = vault_origin.get(ci_corrections.ORIGIN_CI, 0)
    whose = "cite a correction" if ci_rules else "cite a correction you typed"
    lines.append(f"rules in the vault      {v['rules']:>6}   ({v['correction_backed']} {whose}: "
                 f"{v['gate_verified']} verified by the evidence gate today, {v['label_only']} label only)")
    if ci_rules:
        lines.append(f"                                 by origin: "
                     f"{vault_origin.get(ci_corrections.ORIGIN_USER, 0)} you typed, "
                     f"{ci_rules} a red CI run printed")
    lines.append("")
    # This count includes any gate-verified rule, and a CI rule is one — so the
    # label only says "your own words" while the user is the sole source (#272).
    ci_prompts = (p.get("carried_correction_backed_by_origin") or {}).get(
        ci_corrections.ORIGIN_CI, 0)
    cited_label = ("citing a verified quote   " if ci_prompts
                   else "citing your own words     ")
    lines.append("would a rule from an earlier session have come back to you?")
    lines.append("")
    lines.append(f"  reflex would have fired            {p['fired']:>6}   prompts   {_pct(p['fired'], n)}")
    if rate:
        lo, hi = rate["carried_ci95"]
        glo, ghi = rate["carried_gate_verified_ci95"]
        lines.append(f"  ├─ rule from an EARLIER session   {p['carried']:>6}   prompts   "
                     f"{_pct(p['carried'], n)}  (95% CI {100*lo:.1f}–{100*hi:.1f}%)   ← the vault's contribution")
        lines.append(f"  │    {cited_label}{p['carried_gate_verified']:>6}   prompts   "
                     f"{_pct(p['carried_gate_verified'], n)}  (95% CI {100*glo:.1f}–{100*ghi:.1f}%)"
                     f"   verified by the evidence gate today")
    else:
        lines.append(f"  ├─ rule from an EARLIER session   {p['carried']:>6}   prompts   ← the vault's contribution")
        lines.append(f"  │    {cited_label}{p['carried_gate_verified']:>6}   prompts"
                     f"   verified by the evidence gate today")
    lines.append(f"  │    label only, gate can't check  {p['carried_label_only']:>6}   prompts"
                 f"   a `verified` from mnemo reclassify; its briefing has no Corrections to check against")
    by_origin = p.get("carried_correction_backed_by_origin") or {}
    if by_origin.get(ci_corrections.ORIGIN_CI):
        lines.append(f"  │    of those, corrected by CI     "
                     f"{by_origin[ci_corrections.ORIGIN_CI]:>6}   prompts"
                     f"   a red run's assertion, not your words (#272)")
    lines.append(f"  ├─ rule from this SAME session     {p['hindsight']:>6}   prompts   hindsight — the vault could not have helped")
    lines.append(f"  └─ rule not learned yet            {p['not_yet_learned'] + p['undated']:>6}   prompts   "
                 f"today's vault fires, but the rule postdates the prompt")
    lines.append("")
    lines.append(f"  distinct rules carried across sessions   {report['rules']['carried_distinct']}"
                 f"   ({report['rules']['carried_gate_verified_distinct']} gate-verified, "
                 f"{report['rules']['carried_label_only_distinct']} label only)")
    lines.append(f"  rule injections in total                 {inj['total']}"
                 f"   (carried {inj['carried']}, hindsight {inj['hindsight']}, "
                 f"not yet learned {inj['not_yet_learned'] + inj['undated']})")
    lines.append("")
    lines.append("without the vault, none of these prompts carries anything across a session boundary:")
    lines.append("the earlier-session count is the whole difference, and hindsight is what Claude had anyway.")
    if report.get("top_carried"):
        lines.append("")
        lines.append("most carried rules")
        for row in report["top_carried"]:
            # A CI rule is gate-verified but nobody typed it; calling it "your
            # words" here is the false attribution `verified-ci` exists to
            # prevent (#272).
            if row.get("origin") == ci_corrections.ORIGIN_CI:
                mark = "  ✓ CI said"
            elif row.get("gate_verified"):
                mark = "  ✓ your words"
            elif row["correction_backed"]:
                mark = "  ~ label only"
            else:
                mark = ""
            lines.append(f"  {row['prompts']:>4}   {row['slug']}{mark}")
    if report.get("silence"):
        lines.append("")
        lines.append("silent prompts, by reason")
        for reason, count in report["silence"].items():
            lines.append(f"  {count:>6}   {reason}")
    lines.append("")
    if rate is None:
        lines.append(f"sample: {n} prompts over {t['sessions']} sessions — under "
                     f"{report['min_prompts_for_rate']}, so no rate is printed; the counts are the result.")
    else:
        lines.append(f"sample: {n} prompts over {t['sessions']} sessions. Same transcripts and vault give "
                     f"the same numbers.")
    lines.append("not simulated: " + "; ".join(report["not_simulated"]) + ".")
    lines.append("not measured: " + "; ".join(report["not_measured"]) + ".")
    return "\n".join(lines)
