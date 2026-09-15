"""Format the session queue for a terminal.

Pure: takes :class:`Session` records, returns a string. No I/O, so the
ordering rules are testable without touching disk.

The ordering is the feature. Waiting first, **newest first** — the maintainer
reads the first line and knows which session to attach to. Everything else
is context.

Newest first, not oldest: a blocked session's ``tempo`` is frozen at the
moment its process stopped writing, so the stalest entry is the one most
likely to be a corpse (#196). Sorting oldest-first pinned zombies to the top
and aimed the attach hint at the worst of them. Sessions whose process is
provably gone move to their own bucket — re-bucketed, never hidden, because
a queue that silently drops entries cannot be trusted to be complete.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mnemo.core.sessions.jobs import Session

EMPTY = "  nenhuma sessão em background"
NO_TIMESTAMP = "9999"  # sorts after every real ISO-8601 timestamp

# The detail/activity column. Named rather than repeated as a literal because
# `_activity` has to budget against the same number the f-strings pad to — the
# two drifting apart is what breaks the table.
DETAIL_WIDTH = 34
# Below this, a cut target says nothing ("Bash Rel…"), so the tool name and the
# signals take the whole column instead.
MIN_TARGET = 8

# The label column, budgeted for the same reason and in the same way. The old
# `{s.label:<22}` padded but never truncated, while `jobs.py` caps `label` at
# 40 — so every real label overflowed and shoved the column after it. Measured
# on the 2026-09-13 dispatch: labels run 32-40 (`"#197 dispatch a feature's
# pieces"` = 32, `"#203 measure unblock edge coverage"` = 34) and only the
# fixtures were short enough to fit.
LABEL_WIDTH = 34
# Below this, a cut title says nothing ("#193 rec…"), so the identifier takes
# the column alone rather than spending it on one unreadable syllable.
MIN_TITLE = 6


def _age(updated_at: str | None, *, now: datetime | None = None) -> str:
    """Human age of the last update, or '' when unknown."""
    if not updated_at:
        return ""
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if ts.tzinfo is None:  # a writer that omitted the offset means UTC, as in core/numbers.py
        ts = ts.replace(tzinfo=timezone.utc)
    delta = (now or datetime.now(timezone.utc)) - ts
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "agora"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h"


NO_TIMESTAMP_DESC = ""  # sorts before every real ISO-8601 timestamp


def _sort_key(s: Session) -> str:
    """Oldest update first; sessions without a timestamp sort last.

    ISO-8601 sorts lexically, so the year 9999 is simply later than any
    timestamp a real session can carry. Used for the abandoned bucket, where
    oldest-first is the right order: the stalest corpse is the one to clear.
    """
    return s.updated_at or NO_TIMESTAMP


def _freshest_first(s: Session) -> str:
    """Newest update first; sessions without a timestamp sort last.

    Descending on the timestamp means the empty string — the absent one —
    would otherwise sort *first* and hand the attach hint to a session we know
    nothing about. Reversing the sort is not enough; the sentinel has to flip
    with it.
    """
    return s.updated_at or NO_TIMESTAMP_DESC


def _tokens(s: Session) -> str:
    """The context column: how full the session is, as ``/context`` says (#307).

    Never falls back to ``s.tokens``. That field is 3-65x below the context on
    every job measured, so printing it when the transcript is unreadable would
    put the old lie back in the one cell that looks like it is telling the
    truth. Unknown renders blank, as it always did.
    """
    n = s.context_tokens
    if n is None:
        return ""
    return f"{n // 1000}k" if n >= 1000 else str(n)


# One tool's results holding this share of the context is worth a row suffix
# (#308). Measured over the 24 background jobs on disk on 2026-09-15: 23 are
# Bash-led (auto mode steers there) and the 22 dispatch children sit at 12-46%
# with a median of 31%, so a bar at 30% would print "Bash 3x%" on 14 rows and
# say nothing. At 40% it spoke on 1 of 24 — the child heavier than its siblings.
NOTABLE_SHARE = 0.40
# `mcp:claude-in-chrome` fits; a longer server name is cut, not the percentage.
TOOL_WIDTH = 20


def _tool_group(name: str) -> str:
    """``mcp__<server>__<tool>`` counts as its server; any other tool as itself.

    A browser session spreads its screenshots over ``computer``,
    ``browser_batch`` and ``find``; split by tool, no one of them crosses the
    bar although the server as a whole fills the context.
    """
    if name.startswith("mcp__"):
        server = name[len("mcp__"):].split("__", 1)[0]
        if server:
            return f"mcp:{server}"
    return name


def _filled(s: Session) -> str:
    """``Bash 46%`` when one tool's results fill a notable share of the context (#308).

    Silent otherwise, and silent when either number is unknown: the row is
    already 87-135 columns, and a suffix on every row would teach the reader
    to skip it. ``--json`` carries the whole breakdown for anyone who wants it.
    """
    if not s.context_tokens or not s.context_breakdown:
        return ""
    groups: dict[str, int] = {}
    for name, tokens in s.context_breakdown.items():
        key = _tool_group(name)
        groups[key] = groups.get(key, 0) + tokens
    name, tokens = max(groups.items(), key=lambda kv: (kv[1], kv[0]))
    share = tokens / s.context_tokens
    if share < NOTABLE_SHARE:
        return ""
    if len(name) > TOOL_WIDTH:
        name = name[: TOOL_WIDTH - 1] + "…"
    return f"{name} {share:.0%}"


def _prs(s: Session, lookup=None) -> str:
    """The PR this session produced, from git when possible (#217).

    Two sources, in that order of trust:

    1. *lookup*, a callable from ``Session`` to a PR string or ``None``. This
       is the **authoritative** join — ``gh pr list --head <branch>`` against
       the branch ``dispatch.branch_name`` derived — and it does not depend on
       the child reporting anything.
    2. ``Session.children`` filtered to ``kind == "pr"``, which is what this
       read before. Claude Code writes that list when it happens to notice a
       PR and mnemo never writes it at all, which is why one child of the
       2026-09-13 dispatch showed ``#212`` and its sibling showed a prose
       sentence though both had opened one.

    The fallback is kept rather than replaced: when the child *did* volunteer
    a PR it is correct, and it costs no subprocess. The order is what changed
    — git is asked first, and the volunteered value is now the degraded case
    instead of the only one.

    *lookup* is injected because this module is pure by contract: the
    docstring at the top promises no I/O so the ordering rules stay testable
    without touching disk. A ``gh`` call inlined here would put a 400ms
    subprocess inside a function called once per row of a 2s redraw.
    """
    if lookup is not None:
        found = lookup(s)
        if found:
            return found
    ids = [f"#{c.get('id')}" for c in s.children if c.get("kind") == "pr" and c.get("id")]
    return ", ".join(ids)


def _activity(act, budget: int = DETAIL_WIDTH) -> str:
    """One column of what a session is doing, or '' when nothing is known.

    ``(+N)`` is the movement signal and ``↻`` the loop signal: a count that
    rises with an unchanged target is a session going in circles, which reads
    identically to progress without the mark.

    Budgeted to *budget* columns by cutting the **target**, never the signals.
    Measured on 204 real actions from nine dispatch children: 39% of them
    exceed the column (p90 44, max 47), and `f"{x:<34}"` pads without
    truncating — so the unbudgeted string shoved the token column 10-15
    places right on two lines in five and the queue stopped reading as a
    table. Cutting the whole string instead would have eaten `(+26)` and
    `↻` first, which are the two things worth showing.
    """
    if act is None or not act.tool:
        return ""

    tail = ""
    if act.since:
        tail += f" (+{act.since})"
    if act.repeated:
        tail += " ↻"

    head = act.tool if not act.target else f"{act.tool} {act.target}"
    if len(head) + len(tail) <= budget:
        return head + tail

    # Not enough room for tool + target + signals. Give the target whatever is
    # left after the signals, and cut it on a word boundary.
    room = budget - len(tail) - len(act.tool) - 2  # space before, ellipsis after
    if not act.target or room < MIN_TARGET:
        # A tool name alone already fills the column: the signals still matter
        # more than the rest of the name.
        return act.tool[: budget - len(tail)] + tail
    cut = act.target[:room]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{act.tool} {cut.rstrip()}…{tail}"


def status_line(s: Session, act=None, *, pr_lookup=None, budget: int | None = DETAIL_WIDTH) -> str | None:
    """The one line the queue says about *s*, or ``None`` when nothing is known.

    One rule for the table and for ``--json`` (#293), for the reason #222 gave
    for the derived booleans: a consumer that re-derives it gets it wrong.

    - waiting or abandoned: ``needs``, the claim on the maintainer.
    - finished: the PR it produced, else ``detail``.
    - working: the last tool call from the transcript, else ``detail``.

    ``detail`` is **Claude Code's** summary, not mnemo's, and on a working
    session it is the fallback only — never preferred over a tool call, however
    recently it was written. Measured on child ``0f6589d7`` (2026-09-15): for
    ~20 minutes its ``detail`` read ``awaiting task clarification or file
    boundaries`` / ``awaiting task specification; message truncated`` while the
    transcript's last tool call, four seconds older than each of those lines,
    was a ``Bash`` running the piece's own measurement. A freshness comparison
    between the two would have kept the wrong line; the tool call is evidence
    and the summary is an inference about it.

    *budget* cuts the activity the way the table's column does; ``None`` leaves
    it whole, which is what a JSON consumer with its own layout wants.
    """
    if s.is_blocked:
        return s.needs or s.detail
    if s.is_done:
        return _prs(s, pr_lookup) or s.detail
    return _activity(act, budget if budget is not None else 10**6) or s.detail


def _label(s: Session, budget: int = LABEL_WIDTH) -> str:
    """The session's label, budgeted to *budget* columns.

    Cuts the **inferred title**, never the identifier — the same trade
    `_activity` makes one column to the right, where the target gives way and
    the signals survive.

    ``Session.label`` leads with what ``cwd`` encodes (``#197 …`` for an issue,
    ``c-label-column …`` for a contract piece) and follows it with a title
    Claude Code inferred from the transcript. The number is what a maintainer
    tracks across a dispatch; the title reads well and is the expendable half.
    An identifier long enough to fill the column on its own is still cut, so
    that no label can shove the column after it.
    """
    if len(s.label) <= budget:
        return s.label

    head, sep, title = s.label.partition(" ")
    room = budget - len(head) - 2  # space before, ellipsis after
    if not sep or room < MIN_TITLE:
        # No title to spend, or too little left of the column for one to say
        # anything. The identifier takes what there is.
        return s.label[: budget - 1] + "…"
    cut = title[:room]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{head} {cut.rstrip()}…"


def _models(sessions: list[Session]) -> str:
    """One line naming the models in play, or '' when there is nothing to say.

    A **footer**, not a column, and the reasoning is measured rather than
    aesthetic (#268). Real rows already run 87-135 columns against an 80-column
    terminal; a model column would add up to 20 more. Worse, it would repeat
    itself: 18 of 23 real sessions on 2026-09-14 carried the same
    ``claude-fable-5-1[1m]``, so the column would be the same string on nearly
    every row — the definition of a column that does not pay for its width.

    What a maintainer actually asks of the queue is *are my children on what I
    think they are*, and that is a question about the set, not about any one
    row. Counted and sorted by count, so the outlier — the one child on a
    cheaper model, or the one still on the expensive default — reads at the
    end of the line.

    Still printed when every session agrees: "all of them are on X" is the
    answer to the question, and a line that vanishes whenever the answer is
    uniform teaches the reader to distrust its absence. Omitted only when
    nothing on disk records a model at all — an older Claude Code, or a queue
    of sessions this dispatcher never saw.
    """
    counts: dict[str, int] = {}
    for s in sessions:
        if s.model:
            counts[s.model] = counts.get(s.model, 0) + 1
    if not counts:
        return ""
    parts = [
        f"{model} ×{count}" if count > 1 else model
        for model, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return "  modelos: " + ", ".join(parts)


def _grants(sessions: list[Session]) -> str:
    """One line naming the children that may publish unasked, or '' (#317).

    A footer for the same reason as :func:`_models`: most rows would read the
    same (nothing granted), and the question is about the set — *which of my
    children will push on their own?* Unlike the models line it is omitted
    when the answer is "none", because none is the default the maintainer
    already expects; a line appears only when a dispatch changed it.

    Only children still able to act on it. A finished child's grant is spent,
    and listing it would read as a push still to come.
    """
    held = [s for s in sessions if s.may and not s.is_done]
    if not held:
        return ""
    return "  publicam sem perguntar: " + ", ".join(
        f"{s.short_id} ({'+'.join(s.may)})" for s in held
    )


def _thousands(value: int) -> str:
    return f"{value // 1000}k" if value >= 1000 else str(value)


def _exploration(found) -> str:
    """``22u/+61k``: tool uses and context growth before the first edit (#269).

    ``≥`` when the transcript holds no edit at all, because then the count is
    everything the session did, which is a floor on exploration and not the
    measurement. '' when nothing was measured — no transcript, or not asked.
    """
    if found is None:
        return ""
    floor = "" if found.reached else "≥"
    return f"{floor}{found.uses}u/+{_thousands(found.tokens)}"


def _exploration_total(done: list[Session], explorations) -> str:
    """One line summing the column, or '' when no finished row carries one.

    A dispatch's exploration is the sum of its children's, and that sum is
    what #269 asks to be able to see; the per-row figure alone makes the
    reader add them up. Sessions without a measurement are left out of the
    count rather than counted as zero, and the line says how many were summed.
    """
    from mnemo.core.activity.exploration import total

    found = [explorations.get(s.short_id) for s in done]
    count, uses, tokens = total(found)
    if not count:
        return ""
    plural = "sessão" if count == 1 else "sessões"
    return (f"  antes da 1ª edição (u/+tokens): {uses} usos, +{_thousands(tokens)} "
            f"em {count} {plural} prontas")


def render_queue(sessions: list[Session], activities=None, pr_lookup=None,
                 explorations=None) -> str:
    """Render the whole queue, blocked first.

    *pr_lookup* maps a :class:`Session` to the PR it produced, or ``None``.
    Injected rather than called from here so this module stays pure (#217);
    omitted, the PRONTAS bucket falls back to whatever the child volunteered
    in ``children``, exactly as before. See :func:`_prs`.

    *activities* maps ``short_id`` to :class:`~mnemo.core.activity.Activity`.
    Omitted, the activity column costs nothing: the call is byte-identical to
    passing ``None`` or ``{}``, so every caller that predates it keeps working.

    It is no longer byte-identical to v1.4.0, and deliberately so. v1.4.0 pinned
    the label to 22 columns and *padded without truncating*, which is the bug
    this column budget fixes — matching those bytes would mean keeping it. Short
    labels now sit in a wider field; long ones are cut instead of overflowing.

    Only the working bucket uses it. A blocked session's claim on the
    maintainer is ``needs``; burying that under a tool name would invert the
    ordering the whole queue exists to provide.

    *explorations* maps ``short_id`` to
    :class:`~mnemo.core.activity.exploration.Exploration` (#269) and adds one
    column to the end of each PRONTAS row, plus a total under the table. Only
    finished sessions get it: a working child's count is still moving, and the
    activity column already says what it is doing. Omitted, nothing changes.

    Both TRABALHANDO and PRONTAS rows end with ``Bash 46%`` when one tool's
    results fill at least :data:`NOTABLE_SHARE` of the context, read off
    ``Session.context_breakdown`` (#308); every other row is unchanged.
    """
    if not sessions:
        return EMPTY

    acts = activities or {}
    explored = explorations or {}

    waiting = sorted((s for s in sessions if s.is_waiting), key=_freshest_first, reverse=True)
    abandoned = sorted((s for s in sessions if s.is_abandoned), key=_sort_key)
    done = [s for s in sessions if s.is_done and not s.is_blocked]
    working = [s for s in sessions if not s.is_blocked and not s.is_done]

    lines: list[str] = []

    if waiting:
        lines.append(f"TE ESPERANDO ({len(waiting)})")
        for s in waiting:
            age = _age(s.updated_at)
            lines.append(f"  {s.short_id}  {_label(s):<{LABEL_WIDTH}} {age:>5}  {status_line(s) or '—'}")
            if s.suggested_reply:
                lines.append(f"        ↳ sugerido: \"{s.suggested_reply}\"")
        lines.append("")

    if working:
        lines.append(f"TRABALHANDO ({len(working)})")
        for s in working:
            detail = status_line(s, acts.get(s.short_id)) or "—"
            row = f"  {s.short_id}  {_label(s):<{LABEL_WIDTH}} {detail:<{DETAIL_WIDTH}}{_tokens(s):>6}"
            filled = _filled(s)
            lines.append(f"{row}  {filled}" if filled else row)
        lines.append("")

    if done:
        lines.append(f"PRONTAS ({len(done)})")
        for s in done:
            row = f"  {s.short_id}  {_label(s):<{LABEL_WIDTH}} {status_line(s, pr_lookup=pr_lookup) or '—':<{DETAIL_WIDTH}}{_tokens(s):>6}"
            suffix = "  ".join(x for x in (_exploration(explored.get(s.short_id)), _filled(s)) if x)
            lines.append(f"{row}  {suffix}" if suffix else row)
        lines.append("")

    if abandoned:
        # Listed, not hidden. These asked for a human and their process died
        # before getting one; the user decides whether that still matters.
        lines.append(f"ABANDONADAS ({len(abandoned)})")
        for s in abandoned:
            age = _age(s.updated_at)
            lines.append(f"  {s.short_id}  {_label(s):<{LABEL_WIDTH}} {age:>5}  {status_line(s) or '—'}")
        lines.append("")

    models = _models(sessions)
    if models:
        lines.append(models)
    granted = _grants(sessions)
    if granted:
        lines.append(granted)
    spent_total = _exploration_total(done, explored)
    if spent_total:
        lines.append(spent_total)
    if waiting:
        lines.append(f"  attach: claude attach {waiting[0].short_id}")
    if abandoned:
        lines.append(f"  limpar: claude rm {abandoned[0].short_id}")

    return "\n".join(lines).rstrip() + "\n"
