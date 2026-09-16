"""Choose which briefing the SessionStart envelope carries.

Until this module the briefing was the one injector with no relevance step:
``briefing.pick_latest_briefing`` takes the newest and the hook pastes it.

What a query can be at ``SessionStart`` is narrow. Claude Code runs the hook
*before* the first prompt is written to the transcript, and the hook payload
carries ``session_id``, ``transcript_path``, ``cwd`` and ``source`` — no
prompt. The only task signal a cold start has is the working tree, and the
part of that which names a task is the branch (``fix/issue-269``; ``master``
names nothing). ``tools/measure_briefing_query.py`` replays that query against
the real vault: on mnemo's briefings it picks the best-matching briefing
exactly as often as newest-wins does, so the hook passes ``query=None`` and
keeps newest-wins until a query that beats it exists.

Ranking reuses reflex's BM25F scorer and triple-gate over the last
:data:`POOL_SIZE` briefings — no second ranker. Newest-wins stays the answer
whenever there is no query or the gate is not confident, so a query can only
ever *replace* the newest briefing with one it clearly names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mnemo.core.briefing import BriefingRecord
from mnemo.core.extract.scanner import parse_frontmatter
from mnemo.core.reflex import bm25, gates
from mnemo.core.reflex.stopwords import is_stopword
from mnemo.core.reflex.tokenizer import tokenize

# Briefings older than the last ten are the ones a returning user least wants
# pasted in: their "state at end of session" has been overtaken ten times.
POOL_SIZE = 10

# One briefing is carried, not two, so a tie between the top two *is*
# ambiguity here: replacing the newest briefing needs a clear winner. Reflex
# turned this gate off (#332) because it injects both near-tied rules; this
# chooser cannot, so it keeps the gap reflex shipped with before that.
RELATIVE_GAP = 1.5

# Words every branch-naming scheme uses and no briefing is *about*.
_BRANCH_NOISE = frozenset({
    "master", "main", "develop", "development", "dev", "trunk", "head",
    "feat", "feature", "features", "fix", "fixes", "bugfix", "hotfix",
    "issue", "issues", "chore", "docs", "doc", "refactor", "test", "tests",
    "wip", "release", "releases", "claude", "worktree", "worktrees",
})

_PART_SPLIT = re.compile(r"[-_./]+")
_BODY_FIELD = "body"
_WEIGHTS = {_BODY_FIELD: 1.0}


@dataclass
class Choice:
    """The briefing chosen and why. ``record`` is None only for an empty pool.

    ``scores`` pairs each briefing that matched the query (by session id) with
    its BM25 score, best first.
    """

    record: BriefingRecord | None
    reason: str
    query_tokens: list[str] = field(default_factory=list)
    scores: list[tuple[str, float]] = field(default_factory=list)


def _parts(token: str) -> list[str]:
    return [p for p in _PART_SPLIT.split(token) if p]


def query_tokens(query: str | None) -> list[str]:
    """Distinct words of ``query``, hyphen/slash parts split, noise dropped."""
    if not query:
        return []
    out: list[str] = []
    for tok in tokenize(query):
        for part in _parts(tok):
            if len(part) < 2 or part in _BRANCH_NOISE or is_stopword(part):
                continue
            if part not in out:
                out.append(part)
    return out


def _doc_terms(body: str) -> dict[str, int]:
    """Term frequencies for a briefing body; a kebab token also counts its parts."""
    tf: dict[str, int] = {}
    for tok in tokenize(body):
        pieces = _parts(tok)
        for term in ([tok] + pieces) if len(pieces) > 1 else pieces:
            tf[term] = tf.get(term, 0) + 1
    return tf


def _session_id(rec: BriefingRecord) -> str:
    return str(rec.frontmatter.get("session_id") or rec.path.stem)


def _index(records: list[BriefingRecord]) -> tuple[dict, dict[str, set[str]]]:
    """A reflex-shaped BM25F index over one field, plus per-doc term sets.

    Docs are keyed by zero-padded pool position, not session id: two files can
    carry one id (a migrated worktree briefing), and BM25's tie-break sorts
    keys ascending, so equal scores go to the newer briefing.
    """
    docs: dict[str, dict] = {}
    postings: dict[str, list[dict]] = {}
    terms_by_key: dict[str, set[str]] = {}
    total = 0
    for pos, rec in enumerate(records):
        key = f"{pos:04d}"
        tf = _doc_terms(rec.body)
        length = sum(tf.values())
        total += length
        docs[key] = {"field_length": {_BODY_FIELD: length}}
        terms_by_key[key] = set(tf)
        for term, n in tf.items():
            postings.setdefault(term, []).append({"slug": key, "tf": {_BODY_FIELD: n}})
    n_docs = len(docs)
    index = {
        "docs": docs,
        "postings": postings,
        "doc_count": n_docs,
        "avg_field_length": {_BODY_FIELD: (total / n_docs) if n_docs else 0.0},
    }
    return index, terms_by_key


def choose(
    records: list[BriefingRecord],
    query: str | None,
    *,
    thresholds: dict | None = None,
) -> Choice:
    """Pick from ``records`` (newest first) for ``query``. Pure; no I/O.

    The gate's term-overlap bar is capped at the query's own length: a branch
    is one to four words, and a one-word query that matches has matched all
    of itself. The floor is reflex's, unchanged; the relative gap is
    :data:`RELATIVE_GAP`, because only one briefing can win.
    """
    if not records:
        return Choice(record=None, reason="no_briefings")
    newest = records[0]
    q = query_tokens(query)
    if not q:
        return Choice(record=newest, reason="no_query")
    if len(records) == 1:
        return Choice(record=newest, reason="single_briefing", query_tokens=q)

    index, terms_by_key = _index(records)
    scores = bm25.score_docs(
        index, query_tokens=q, candidate_slugs=list(index["docs"]),
        weights=_WEIGHTS, params=bm25.DEFAULT_PARAMS,
    )
    named = [(_session_id(records[int(k)]), v) for k, v in scores]
    if not scores:
        return Choice(record=newest, reason="no_match", query_tokens=q)
    th = dict(gates.DEFAULT_THRESHOLDS, relative_gap=RELATIVE_GAP)
    th.update(thresholds or {})
    th["term_overlap_min"] = min(int(th["term_overlap_min"]), len(q))
    result = gates.evaluate_gates(
        scores, query_tokens=q, doc_tokens_by_slug=terms_by_key,
        thresholds=th, doc_count=index["doc_count"],
    )
    if not result.accepted_slugs:
        return Choice(record=newest, reason=result.silence_reason or "low_confidence",
                      query_tokens=q, scores=named)
    rec = records[int(result.accepted_slugs[0])]
    reason = "newest_confirmed" if rec is newest else "ranked"
    return Choice(record=rec, reason=reason, query_tokens=q, scores=named)


def recent_briefings(
    vault_root: Path, agent_name: str, limit: int = POOL_SIZE
) -> list[BriefingRecord]:
    """The newest ``limit`` briefings for ``agent_name``, newest first.

    Same order as ``briefing.pick_latest_briefing``, so ``recent[0]`` is the
    briefing the hook injected before this module existed.
    """
    sessions_dir = Path(vault_root) / "bots" / agent_name / "briefings" / "sessions"
    if not sessions_dir.is_dir():
        return []
    keyed: list[tuple[tuple, BriefingRecord]] = []
    for md in sessions_dir.glob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = parse_frontmatter(text)
        rec = BriefingRecord(path=md, frontmatter=fm, body=body.lstrip("\n"))
        date = fm.get("date", "")
        if date:
            key = (1, date, fm.get("session_id", md.stem), 0.0)
        else:
            try:
                mtime = md.stat().st_mtime
            except OSError:
                mtime = 0.0
            key = (0, "", "", mtime)
        keyed.append((key, rec))
    keyed.sort(key=lambda kv: kv[0], reverse=True)
    return [rec for _, rec in keyed[:limit]]


def pick(vault_root: Path, agent_name: str, *, query: str | None) -> BriefingRecord | None:
    """The briefing to inject for ``agent_name``, or None when there is none."""
    return choose(recent_briefings(vault_root, agent_name), query).record

