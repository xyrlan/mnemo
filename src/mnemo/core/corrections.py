"""The ``## Corrections`` section of a session briefing.

A correction is the user telling Claude to stop, change, prefer, or
never/always do something. The briefing LLM proposes items as
``- "<verbatim quote>" → <rule>``; this module is the only reader and writer of
that format, and :func:`verify` is the mechanical check that the quote really
is a substring of something the user typed. A fabricated quote never reaches
disk.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

SECTION_HEADER = "## Corrections"
ARROW = "→"
# Shorter than this and a quote matches by accident ("ok", "yes", "no").
MIN_QUOTE_CHARS = 12

_HEADER_RE = re.compile(r"^## ", re.M)
_SECTION_RE = re.compile(r"^## Corrections[ \t]*$", re.M)
_ITEM_RE = re.compile(
    r"""^\s*[-*]\s+["“](?P<quote>.+?)["”]\s*(?:→|->)\s*(?P<rule>.+?)\s*$"""
)
_QUOTE_CHARS = "\"'“”‘’"


@dataclass(frozen=True)
class Correction:
    quote: str
    rule: str


def normalize(text: str) -> str:
    """Whitespace-collapsed, dequoted, lower-cased form used for matching.

    ``.strip(_QUOTE_CHARS)`` only trims the ends on purpose: interior quotes
    are preserved, and both sides of a comparison go through this same
    normalisation so they line up regardless of which quote characters they
    were typed or rendered with.
    """
    return re.sub(r"\s+", " ", text).strip().strip(_QUOTE_CHARS).strip().lower()


def quote_matches_turn(quote: str, turn: str) -> bool:
    q = normalize(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(turn)


# A keep quote needs at least this many non-stopword tokens to count as
# evidence of a rule rather than an approval (#119). Calibrated on the 61 keep
# verdicts of the saved real-vault plan: 54 survive at 5, 51 at 6, and the
# generic approvals the issue names are demoted at both. The spec allows 5
# and never lower.
MIN_CONTENT_TOKENS = 5

# Words that carry no rule content on their own. Portuguese and English:
# articles, pronouns, prepositions, auxiliaries, and the generic imperatives a
# user types to approve or nudge ("implementa os fixes", "yes do it").
_STOPWORDS = frozenset("""
a o os as um uma uns umas de do da dos das em no na nos nas por para pra pro com sem
e ou mas que se não nao sim ok okay ja já isso isto aqui ali lá la ele ela eles elas
eu tu voce você nós nos vc me te lhe meu minha seu sua
é e ser esta está estão estao foi era tem têm ter vai vão vao vamos vamo bora
pode podem poder deve devem faz faça fazer fez implementa implementar implemente
aplica aplicar aplique testa testar teste roda rodar rode
the a an and or but if so to of in on at for with by from as is are was were be been
do does did done it its this that these those i you we they he she me my your our
yes no ok okay please just go run apply implement test try let lets let's
""".split())


def quote_is_specific(quote: str) -> bool:
    """True when the quote has enough content words to establish a rule (#119).

    ``quote_matches_turn`` proves the user typed the words; it does not prove
    the words say anything. A one-line approval passes the substring check
    for every rule extracted from that session. Requiring ``MIN_CONTENT_TOKENS``
    non-stopword tokens rejects those without a cross-language lexical match
    against the rule (quotes are Portuguese, rules are English).
    """
    tokens = re.findall(r"\w+", normalize(quote))
    content = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return len(content) >= MIN_CONTENT_TOKENS


def _section_span(markdown: str) -> tuple[int, int] | None:
    m = _SECTION_RE.search(markdown)
    if m is None:
        return None
    start = m.start()
    # Section runs until the next "## " header or end of text.
    nxt = _HEADER_RE.search(markdown, m.end())
    end = nxt.start() if nxt else len(markdown)
    return start, end


# The prompt's own shape, ``- "<verbatim quote from a USER TURN>" → <...>``,
# echoed back with nothing filled in.
_PLACEHOLDER_RE = re.compile(r"^<[^<>]*>$")


def parse_section(markdown: str) -> list[Correction]:
    """The section's items, without one that only echoes the prompt's shape.

    A model with nothing to report sometimes copies the bullet template
    (``"<verbatim quote from a USER TURN>"``). That is an empty section, not a
    quote the user did not type: rerun on 10 sessions whose briefing had
    logged ``briefing.corrections_rejected`` (2026-10-28), the briefing prompt
    produced two rejected quotes, and both were this placeholder (#758).
    """
    span = _section_span(markdown)
    if span is None:
        return []
    out: list[Correction] = []
    for line in markdown[span[0]:span[1]].splitlines():
        m = _ITEM_RE.match(line)
        if m and not _PLACEHOLDER_RE.match(m.group("quote").strip()):
            out.append(Correction(quote=m.group("quote").strip(), rule=m.group("rule").strip()))
    return out



#: How mnemo's own dispatch prompts open (``core/dispatch.py``: ``_PROMPT`` and
#: ``_PIECE_PROMPT``). A user turn that starts this way was written by mnemo,
#: not typed by a person, so nothing in it can be the user's correction.
DISPATCH_BRIEF_OPENINGS: tuple[str, ...] = (
    "Work on issue #",
    "You are building one piece of the feature",
)


def is_dispatch_brief(turn: str) -> bool:
    """True when *turn* is the opening prompt ``mnemo dispatch`` hands a child."""
    return (turn or "").lstrip().startswith(DISPATCH_BRIEF_OPENINGS)


_JOB_SCRATCH_RE = re.compile(r"(?:^|/)\.claude/jobs/[^/]+/tmp(?:/|$)")


def is_job_scratch(cwd: str) -> bool:
    """True when *cwd* lies inside a background Claude Code job's scratch dir.

    ``<config dir>/jobs/<id>/tmp`` is the ``$CLAUDE_JOB_DIR/tmp`` a background
    session is told to put throwaway files in. A session whose cwd is there
    was started *by that session* — a probe of the harness ("Use the Bash
    tool to run exactly this command: touch probe-file-86 . Do nothing
    else."), a dispatch rehearsal — and every turn in it was written by an
    agent, not typed by a person changing how they want to work (#348).
    Measured 2026-09-16: 13 of 466 transcripts on disk recorded such a cwd,
    all 13 probes or rehearsals, and two of them had been linked to a live
    rule by the contradiction pass. The same exclusion as
    :func:`is_dispatch_brief`, one layer further out: the whole session, not
    its opening turn.

    Mechanical on purpose. The probes' wording varies ("Reply with the single
    word ok", "Run exactly this shell command … and nothing else"), so a
    phrase list would chase it. A system temp dir is too wide: the one
    such session with a correction on disk is the five-minute loop's "never
    use npm in this repo, always yarn", typed in ``/private/tmp/mnemo-demo``.
    Nor is "the cwd no longer exists": that is every removed dispatch tree.
    """
    text = (cwd or "").replace("\\", "/")
    if not text:
        return False
    if _JOB_SCRATCH_RE.search(text):
        return True
    config = os.environ.get("CLAUDE_CONFIG_DIR", "").replace("\\", "/").rstrip("/")
    if not config:
        return False
    rest = text[len(config) + 1:] if text.startswith(config + "/") else ""
    parts = rest.split("/")
    return len(parts) >= 3 and parts[0] == "jobs" and bool(parts[1]) and parts[2] == "tmp"


#: How Claude Code records a ``!`` shell-mode turn: the command the user ran,
#: and what it printed. Typed, or printed, but addressed to a shell.
_SHELL_TURN_RE = re.compile(r"^\s*<(?:bash-input|bash-stdout|bash-stderr)>", re.I)


def is_shell_turn(text: str) -> bool:
    """True when *text* is a ``!`` shell-mode command or its output.

    A command the user runs is the user acting, not the user telling the
    assistant anything — and its output is not the user's words at all.
    Measured on the real vault (#360): 10 of the 90 backfilled corrections
    were ``<bash-input>`` blocks, and three of them (``gh pr merge … --admin``)
    were linked as contradicting ``merge-requires-admin`` — the largest single
    cluster in the ledger, from the user doing what the rule described.
    """
    return bool(_SHELL_TURN_RE.match(text or ""))


#: How Claude Code opens the user turn it writes when it compacts a session.
_COMPACT_SUMMARY_OPENING = "This session is being continued from a previous conversation"


def is_compact_summary(text: str) -> bool:
    """True when *text* is the summary Claude Code writes on compaction (#758).

    It sits in the transcript as a user turn (``isCompactSummary``), but the
    agent wrote it: it retells the user's earlier words and its own in one
    voice. On the maintainer's Windows vault two ledger rows were quoted from
    such a summary, in the agent's words. Matched on the text, not
    the event flag, so a turn keeps its index in :func:`transcript.user_turns`
    and every ledger row's ``turn_index`` still points where it did.
    """
    return (text or "").lstrip().startswith(_COMPACT_SUMMARY_OPENING)


def verify(
    items: list[Correction], user_turns: list[str],
) -> tuple[list[Correction], list[Correction]]:
    """Split items into (kept, rejected) by whether the quote was really typed
    by the user — and not merely handed to the session as its brief.

    A correction is a reaction: the user telling the assistant to stop, change
    or prefer something. Measured on the real vault (#244): 23 of 64
    Corrections items quoted only the opening turn, and 21 of those were
    dispatched children whose sole user turn is mnemo's own dispatch template
    ("Do NOT merge or push without asking.", "Write commits and any PR in
    English."). Three of the vault's nineteen gate-verified feedback rules
    cited that template as the user's words.

    So the opening turn is skipped **when it is a dispatch brief**
    (:func:`is_dispatch_brief`) — text mnemo wrote, which no user typed. A
    human's first message stays in scope: the five-minute loop in the README
    is exactly "never use npm in this repo, always yarn" as the first and
    only thing typed, and that is a correction. A quote the user repeats
    later in the session is kept either way: the repetition is the reaction.

    Shell-mode turns (:func:`is_shell_turn`) never count either: a quote found
    only in a ``!`` command or its output is not something the user said. Nor
    does a compaction summary (:func:`is_compact_summary`).
    """
    kept: list[Correction] = []
    rejected: list[Correction] = []
    for item in items:
        if locate(item.quote, user_turns) is not None:
            kept.append(item)
        else:
            rejected.append(item)
    return kept, rejected


def reaction_indexes(user_turns: list[str]) -> list[int]:
    """Indexes of the turns :func:`verify` accepts a quote from.

    Every turn but a dispatch brief in the opening position, a ``!``
    shell-mode turn and a compaction summary — the exclusions :func:`verify`
    documents.
    """
    start = 1 if user_turns and is_dispatch_brief(user_turns[0]) else 0
    return [
        i for i in range(start, len(user_turns))
        if not is_shell_turn(user_turns[i]) and not is_compact_summary(user_turns[i])
    ]


def locate(quote: str, user_turns: list[str]) -> int | None:
    """Index of the first turn :func:`verify` would accept *quote* from, or None.

    The first, not the last: a correction the user repeats was said, and
    could have been learned from, the first time (#517).
    """
    for i in reaction_indexes(user_turns):
        if quote_matches_turn(quote, user_turns[i]):
            return i
    return None


def rejection_reason(quote: str, user_turns: list[str]) -> str:
    """Why :func:`verify` rejected *quote*: where it was found, if anywhere (#758).

    ``"dispatch brief"``, ``"shell turn"`` or ``"compact summary"`` when the
    quote is in a turn
    :func:`reaction_indexes` skips on purpose, ``"short"`` when it is under
    :data:`MIN_QUOTE_CHARS`, ``"not typed"`` when no user turn holds it — a
    paraphrase, a fixed typo, or the assistant's own words.
    """
    if len(normalize(quote)) < MIN_QUOTE_CHARS:
        return "short"
    for i, turn in enumerate(user_turns):
        if quote_matches_turn(quote, turn):
            if is_shell_turn(turn):
                return "shell turn"
            return "compact summary" if is_compact_summary(turn) else "dispatch brief"
    return "not typed"


def rejection_causes(items: list[Correction], user_turns: list[str]) -> str:
    """``"not typed: 2, dispatch brief: 1"`` — :func:`rejection_reason` counted.

    What ``briefing.corrections_rejected`` logs beside its count, so a rejected
    quote says which way it missed: only the count was logged before #758.
    """
    counts: dict[str, int] = {}
    for item in items:
        reason = rejection_reason(item.quote, user_turns)
        counts[reason] = counts.get(reason, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# A turn is screened only when it is this short: a go-ahead is a line, and a
# long turn that opens with one may carry a rule no marker below names.
_SCREEN_MAX_WORDS = 30

# Words that make a turn a candidate rule however it opens: a negation, a
# standing frequency, a restriction, a preference, a condition, an order of
# steps, or a permission named as one ("você tem permissão para mudar o
# settings"). Portuguese is matched with and without its accents, as the vault's
# users type both. A go-ahead holding one of these ("pode mergear quando o CI
# passar", "pode seguir sem me perguntar") is left to the LLM steps.
_RULE_MARKERS = re.compile(
    r"\b(?:n[aã]o|nunca|jamais|sempre|sem|s[oó]|somente|apenas|antes|"
    r"quando|at[eé]|enquanto|em vez|ao inv[eé]s|errad[oa]s?|pare|para de|"
    r"evit[aeo]r?|cuidado|prefir[ao]|prefer[eo]|precisa|tem que|deve|devia|"
    r"not|never|always|only|instead|without|stop|avoid|wrong|rather|"
    r"prefer|before|after|until|unless|when|once|must|should|need|"
    r"permiss[aã]o|autoriz\w*|permission|allowed|"
    r"\w+n['’]t)\b"
    # English "no" only where it opens a clause: mid-sentence it is
    # Portuguese "in the" ("pode mergear no main").
    r"|(?:^|[,.;:!?]\s*)no\b"
)

# Words that open a turn without saying anything: acknowledgement and thanks.
_INTERJECTION = (
    r"(?:boa|ok|okay|sim|show|beleza|blz|perfeito|perfeita|[oó]timo|[oó]tima|"
    r"legal|massa|top|valeu|obrigad[oa]|ent[aã]o|certo|isso|"
    r"yes|yep|yeah|great|nice|cool|perfect|thanks|alright|good|right)"
)

# How a go-ahead opens, Portuguese then English: permission to proceed, to
# merge, to dispatch, to carry on. "pode X" is "go ahead and X" — the #524
# raters labelled "pode mergear", "pode fazer o merge você mesmo" approvals.
_GO_AHEAD_OPENERS = (
    r"(?:pode|podemos|segue|seguir|siga|prossiga|prosseguir|continua|continue|"
    r"manda ver|manda bala|bora|vamos nessa|vai l[aá]|mergeia|mergea|mergear|"
    r"despacha|fa[cç]a isso|faz isso|"
    r"go ahead|go for it|ship it|merge it|please merge|merge|proceed|carry on|"
    r"do it|lgtm|looks good|sounds good|approved)"
)
_GO_AHEAD_RE = re.compile(
    r"^(?:%s\b[\s,.!]*)*%s\b" % (_INTERJECTION, _GO_AHEAD_OPENERS)
)

# A pointed question redirects the method ("why didn't you use X?"), so a
# question that asks why is never screened, whatever else it holds.
_WHY_RE = re.compile(r"\b(?:por ?qu[eê]|pq|pra qu[eê]|para qu[eê]|why|how come)\b")


def _screen_text(turn: str) -> str:
    return normalize(turn).replace("!", " ").strip()


def is_go_ahead(turn: str) -> bool:
    """True when *turn* only lets the assistant proceed: "pode mergear" (#758).

    A short turn that opens with a go-ahead (after any "ok", "boa", "sim")
    and holds no :data:`_RULE_MARKERS` word. Approval is the commonest thing
    the briefing LLM records as a correction although both its prompts name
    it as not one: of the 45 ledger rows on the maintainer's Windows vault,
    2026-10-14 to 10-26, 14 were go-aheads shaped like "pode mergear os dois
    por favor" or "segue com o deploy".
    """
    text = _screen_text(turn)
    if not text or len(text.split()) > _SCREEN_MAX_WORDS:
        return False
    return bool(_GO_AHEAD_RE.match(text)) and not _RULE_MARKERS.search(text)


def is_question(turn: str) -> bool:
    """True when *turn* is nothing but questions for information (#758).

    Every sentence ends in ``?``, none asks why, and none holds a
    :data:`_RULE_MARKERS` word — "why didn't you use X?" and "can't you check
    that through our integration?" redirect the method, and are left to the
    LLM steps. "eai como esta indo a migracao do banco?" is not.
    """
    text = normalize(turn)
    if not text.endswith("?") or len(text.split()) > _SCREEN_MAX_WORDS:
        return False
    sentences = [s.strip() for s in re.findall(r"[^.!?\n]*[.!?\n]*", text) if s.strip()]
    if not all(s.endswith("?") for s in sentences):
        return False
    return not _WHY_RE.search(text) and not _RULE_MARKERS.search(text)


def screen_reason(turn: str) -> str:
    """``"go-ahead"``, ``"question"``, or ``""`` when *turn* may correct."""
    if is_go_ahead(turn):
        return "go-ahead"
    if is_question(turn):
        return "question"
    return ""


def screen(
    items: list[Correction], user_turns: list[str],
) -> tuple[list[Correction], list[Correction]]:
    """Split verified *items* into (kept, dropped) by what their turn does (#758).

    :func:`verify` proves the user typed the quote; this drops the item whose
    **whole turn** (:func:`locate`) is a go-ahead or a question — never one
    whose quote is a go-ahead cut from a longer turn that may hold a rule.
    Mechanical and always on, before :mod:`mnemo.core.correction_check`'s
    LLM look (off by default): both prompts already say an approval and a
    question are not corrections, and the ledger still filled with them.
    ``tools/measure_friction_screen.py`` replays the ledger through it.
    """
    kept: list[Correction] = []
    dropped: list[Correction] = []
    for item in items:
        index = locate(item.quote, user_turns)
        turn = user_turns[index] if index is not None else item.quote
        (dropped if screen_reason(turn) else kept).append(item)
    return kept, dropped


def render_section(items: list[Correction]) -> str:
    lines = [SECTION_HEADER]
    for it in items:
        lines.append(f'- "{it.quote}" {ARROW} {it.rule}')
    return "\n".join(lines) + "\n"


def strip_section(markdown: str) -> str:
    span = _section_span(markdown)
    if span is None:
        return markdown
    return (markdown[:span[0]].rstrip("\n") + "\n\n" + markdown[span[1]:].lstrip("\n")).strip("\n") + "\n"


def replace_section(markdown: str, items: list[Correction]) -> str:
    """Rewrite the section with exactly *items*; remove it when empty.

    Placed right after the ``## Decisions made`` section when present,
    otherwise appended at the end.
    """
    base = strip_section(markdown)
    if not items:
        return base
    block = render_section(items)
    anchor = base.find("## Decisions made")
    if anchor == -1:
        return base.rstrip("\n") + "\n\n" + block
    nxt = _HEADER_RE.search(base, anchor + 1)
    insert_at = nxt.start() if nxt else len(base)
    head = base[:insert_at].rstrip("\n") + "\n\n"
    tail = base[insert_at:]
    return head + block + ("\n" + tail if tail else "")
