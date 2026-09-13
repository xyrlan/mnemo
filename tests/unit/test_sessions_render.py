"""Rendering the session queue.

The ordering is the message: blocked first, oldest first. The maintainer
reads line one and knows where to go.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import _age, render_queue

NOW = datetime(2026, 9, 12, 12, 30, tzinfo=timezone.utc)


def _blocked(short_id: str, needs: str, *, updated_at: str = "2026-09-12T12:00:00.000Z", **kw) -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs=needs, updated_at=updated_at, **kw)


def test_empty_says_so() -> None:
    assert "nenhuma sessão em background" in render_queue([])


def test_blocked_section_comes_first() -> None:
    out = render_queue([
        Session(short_id="w0", state="working", tempo="active", name="trabalhando"),
        _blocked("b0", "responde?", name="bloqueada"),
    ])

    assert out.index("TE ESPERANDO") < out.index("TRABALHANDO")
    assert out.index("bloqueada") < out.index("trabalhando")


def test_freshest_blocked_first() -> None:
    """Inverted by #196. This test used to assert oldest-first, which was the bug.

    ``tempo`` freezes at the moment a process stops writing, so among blocked
    sessions the stalest is the one most likely to be a corpse. Oldest-first
    pinned zombies to the top of the queue by construction.
    """
    out = render_queue([
        _blocked("new", "recente", name="recente", updated_at="2026-09-12T12:30:00.000Z"),
        _blocked("old", "antiga", name="antiga", updated_at="2026-09-12T12:00:00.000Z"),
    ])

    assert out.index("recente") < out.index("antiga")


def test_needs_is_shown_and_falls_back_to_detail() -> None:
    out = render_queue([
        _blocked("a", "a pergunta", name="com-needs"),
        Session(short_id="b", state="working", tempo="blocked",
                detail="o detalhe", name="sem-needs"),
    ])

    assert "a pergunta" in out
    assert "o detalhe" in out


def test_suggested_reply_renders_when_present() -> None:
    out = render_queue([_blocked("a", "qual idioma?", name="x", suggested_reply="português")])

    assert "sugerido" in out
    assert "português" in out


def test_done_section_lists_pull_requests() -> None:
    out = render_queue([Session(
        short_id="e51f", state="done", tempo="idle", name="entregue",
        children=({"id": "307", "kind": "pr"}, {"id": "308", "kind": "pr"}),
    )])

    assert "PRONTAS" in out
    assert "#307" in out and "#308" in out


def test_attach_hint_names_the_first_blocked_session() -> None:
    out = render_queue([_blocked("a3f1", "q?", name="x")])

    assert "claude attach a3f1" in out


def test_no_attach_hint_when_nothing_is_blocked() -> None:
    out = render_queue([Session(short_id="w0", state="working", tempo="active", name="w")])

    assert "claude attach" not in out


def test_age_says_agora_under_a_minute() -> None:
    assert _age("2026-09-12T12:29:30.000Z", now=NOW) == "agora"


def test_age_counts_minutes_up_to_an_hour() -> None:
    assert _age("2026-09-12T12:29:00.000Z", now=NOW) == "1m"
    assert _age("2026-09-12T11:31:00.000Z", now=NOW) == "59m"


def test_age_switches_to_hours_at_sixty_minutes() -> None:
    assert _age("2026-09-12T11:30:00.000Z", now=NOW) == "1h"
    assert _age("2026-09-11T12:30:00.000Z", now=NOW) == "24h"


def test_age_is_blank_without_a_timestamp() -> None:
    assert _age(None, now=NOW) == ""
    assert _age("", now=NOW) == ""


def test_age_is_blank_when_the_timestamp_is_unparsable() -> None:
    assert _age("ontem de tarde", now=NOW) == ""


def test_age_accepts_a_timestamp_without_a_timezone() -> None:
    """An updatedAt with no Z and no offset must degrade, not crash."""
    assert _age("2026-09-12T12:00:00", now=NOW) == "30m"


def test_age_of_a_future_timestamp_reads_as_agora() -> None:
    """Documents today's behaviour: a negative delta falls in the <1min branch."""
    assert _age("2026-09-12T13:00:00.000Z", now=NOW) == "agora"


# --- activity column (layer 1) ---

from mnemo.core.activity.summarize import Activity  # noqa: E402


def _working(short_id="abc", **kw):
    kw.setdefault("tempo", "active")
    kw.setdefault("name", "child")
    return Session(short_id=short_id, **kw)


def test_render_without_activities_is_unchanged():
    """Passing no activities costs nothing: same bytes as None, as {}.

    Note what this does *not* assert. Its name once implied byte-identity with
    the queue v1.4.0 shipped, but it only ever compared the three present-tense
    calls to each other — so it stayed green when the label field widened from
    22 columns to LABEL_WIDTH. That widening is the fix for the overflow bug,
    not a regression: v1.4.0 padded the label without truncating it.
    """
    sessions = [_working(detail="building")]

    assert render_queue(sessions) == render_queue(sessions, None)
    assert render_queue(sessions, {}) == render_queue(sessions)


def test_activity_replaces_detail_in_the_working_bucket():
    sessions = [_working(detail="building")]
    acts = {"abc": Activity(tool="Edit", target="dispatch.py", since=3)}

    out = render_queue(sessions, acts)

    assert "Edit dispatch.py (+3)" in out
    assert "building" not in out


def test_detail_is_the_fallback_when_a_session_has_no_activity():
    sessions = [_working(detail="building")]

    out = render_queue(sessions, {})

    assert "building" in out


def test_zero_since_shows_no_counter():
    sessions = [_working()]
    acts = {"abc": Activity(tool="Bash", target="Run tests", since=0)}

    out = render_queue(sessions, acts)

    assert "Bash Run tests" in out
    assert "(+0)" not in out


def test_repeated_tool_is_marked():
    """The loop signal has to be visible without counting columns."""
    sessions = [_working()]
    acts = {"abc": Activity(tool="Grep", target="linkScanOffset", since=7, repeated=True)}

    out = render_queue(sessions, acts)

    assert "Grep linkScanOffset (+7)" in out
    assert "↻" in out


def test_activity_without_a_target_still_renders():
    sessions = [_working()]
    acts = {"abc": Activity(tool="AskUserQuestion", target=None, since=1)}

    out = render_queue(sessions, acts)

    assert "AskUserQuestion" in out


def test_activity_does_not_leak_into_the_waiting_bucket():
    """A blocked session's claim is `needs`; activity would bury it."""
    sessions = [_working(short_id="w", tempo="blocked", needs="qual opção?")]
    acts = {"w": Activity(tool="Edit", target="x.py", since=2)}

    out = render_queue(sessions, acts)

    assert "qual opção?" in out
    assert "Edit x.py" not in out


def test_activity_for_an_unlisted_session_is_ignored():
    sessions = [_working()]
    acts = {"someone-else": Activity(tool="Edit", target="x.py")}

    out = render_queue(sessions, acts)

    assert "x.py" not in out


def test_long_activity_does_not_break_the_column():
    sessions = [_working()]
    acts = {"abc": Activity(tool="Bash", target="x" * 40, since=99)}

    out = render_queue(sessions, acts)

    for line in out.splitlines():
        assert len(line) <= 100, line


# --- activity column budget (measured: 39% of real actions exceeded it) ---

def test_activity_is_budgeted_to_the_column_width():
    from mnemo.core.sessions.render import DETAIL_WIDTH, _activity

    act = Activity(tool="Bash", target="Relocate comment file and verify clean", since=23)

    rendered = _activity(act)

    assert len(rendered) <= DETAIL_WIDTH
    assert "…" in rendered


def test_budgeting_cuts_the_target_never_the_signals():
    """(+N) and the loop mark are the two things worth showing; they survive."""
    from mnemo.core.sessions.render import _activity

    act = Activity(tool="Bash", target="Windows job step conclusions and more",
                   since=24, repeated=True)

    rendered = _activity(act)

    assert rendered.endswith("(+24) ↻")
    assert rendered.startswith("Bash ")


def test_budgeted_target_is_cut_on_a_word_boundary():
    from mnemo.core.sessions.render import _activity

    act = Activity(tool="Bash", target="Commit measurement script and report", since=26)

    assert _activity(act) == "Bash Commit measurement… (+26)"


def test_a_tool_name_that_fills_the_column_keeps_its_signals():
    from mnemo.core.sessions.render import DETAIL_WIDTH, _activity

    act = Activity(tool="mcp__claude-in-chrome__browser_batch", target="x", since=5)

    rendered = _activity(act)

    assert len(rendered) <= DETAIL_WIDTH
    assert rendered.endswith("(+5)")


def test_the_table_stays_aligned_when_activity_is_long():
    """The whole point of the budget: the token column must not move."""
    sessions = [
        _working(short_id="s1", tokens=4200),
        _working(short_id="s2", tokens=1500),
    ]
    acts = {
        "s1": Activity(tool="Bash", target="Relocate comment file and verify clean", since=23),
        "s2": Activity(tool="Edit", target="x.py"),
    }

    out = render_queue(sessions, acts)
    rows = [l for l in out.splitlines() if l.startswith("  s")]

    assert len(rows) == 2
    assert len(rows[0]) == len(rows[1]), rows


# --- label column budget -----------------------------------------------------
#
# The same bug the activity column already fixed, one column to its left.
# `{s.label:<22}` pads but never truncates, and `jobs.py` caps `label` at 40:
# the real labels a maintainer sees run 30-40 and shoved everything after them.
# Reproduced from real dispatch state.json files, 2026-09-13.

REAL_LABELS = [
    "#197 dispatch a feature's pieces",          # 32
    "#203 measure unblock edge coverage",        # 34
    "#193 recall harness hit_slugs migration",   # 39
    "c-label-column label-column implementati",  # 40, the jobs.py cap
]


def _labelled(short_id: str, label: str, **kw) -> Session:
    """A session whose rendered label is exactly *label*.

    `Session.label` prefixes from `cwd`; passing the whole string as `name`
    with no `cwd` makes the label verbatim, so a test can state the width it
    means rather than recomputing the prefix rule.
    """
    kw.setdefault("tempo", "active")
    return Session(short_id=short_id, name=label, **kw)


def test_a_long_label_does_not_shove_the_column_after_it():
    """The reproduction: real labels, every row the same width."""
    sessions = [
        _labelled(f"s{i}", label, tokens=1500)
        for i, label in enumerate(REAL_LABELS)
    ]

    out = render_queue(sessions)
    rows = [l for l in out.splitlines() if l.startswith("  s")]

    assert len(rows) == len(REAL_LABELS)
    assert len(set(len(r) for r in rows)) == 1, rows


def test_the_label_budget_holds_in_every_bucket():
    """All four buckets pad the same field, so all four have the same bug."""
    from mnemo.core.sessions.render import LABEL_WIDTH

    label = "#193 recall harness hit_slugs migration"
    buckets = {
        "waiting": _labelled("w1", label, tempo="blocked", needs="q?"),
        "working": _labelled("k1", label, tokens=1500),
        "done": _labelled("d1", label, state="done", tempo="idle",
                          children=({"id": "307", "kind": "pr"},)),
        "abandoned": _labelled("a1", label, tempo="blocked", needs="q?", live=False),
    }
    short = {
        "waiting": _labelled("w2", "x", tempo="blocked", needs="q?"),
        "working": _labelled("k2", "x", tokens=1500),
        "done": _labelled("d2", "x", state="done", tempo="idle",
                          children=({"id": "307", "kind": "pr"},)),
        "abandoned": _labelled("a2", "x", tempo="blocked", needs="q?", live=False),
    }

    for bucket, long_session in buckets.items():
        out = render_queue([long_session, short[bucket]])
        rows = [l for l in out.splitlines() if l.startswith(("  w", "  k", "  d", "  a"))
                and not l.startswith("  attach") and not l.startswith("  limpar")]

        assert len(rows) == 2, (bucket, out)
        # Every row must reserve the same number of columns for the label, so
        # whatever follows lands at one offset. Rows are
        # "  <short_id>  <label padded to LABEL_WIDTH> <rest>".
        for row in rows:
            field = row[len("  w1  "):][:LABEL_WIDTH]
            assert len(field.rstrip()) <= LABEL_WIDTH, (bucket, row)
            assert row[len("  w1  ") + LABEL_WIDTH] == " ", (bucket, row)


def test_the_issue_number_survives_a_cut_label():
    """The number is the identifier tracked across a dispatch; the title is not."""
    from mnemo.core.sessions.render import _label

    cut = _label(_labelled("s", "#193 recall harness hit_slugs migration"))

    assert cut.startswith("#193 ")
    assert "…" in cut


def test_a_piece_slug_survives_a_cut_label():
    """`#` means GitHub issue, so a contract piece is prefixed bare (jobs.py)."""
    from mnemo.core.sessions.render import _label

    cut = _label(_labelled("s", "c-label-column label-column implementati"))

    assert cut.startswith("c-label-column ")


def test_a_short_label_is_not_touched():
    from mnemo.core.sessions.render import _label

    assert _label(_labelled("s", "child")) == "child"


def test_a_cut_label_fits_the_budget():
    from mnemo.core.sessions.render import LABEL_WIDTH, _label

    for label in REAL_LABELS:
        assert len(_label(_labelled("s", label))) <= LABEL_WIDTH, label


def test_an_identifier_that_fills_the_column_is_still_cut():
    """No label may exceed the budget — not even one that is all identifier.

    `jobs.py` caps `label` at 40, so a 40-char label with no room left for a
    title still has to be cut to LABEL_WIDTH or it shoves the column again.
    """
    from mnemo.core.sessions.render import LABEL_WIDTH, _label

    # A title so starved that MIN_TITLE refuses to spend the column on it.
    rendered = _label(_labelled("s", "#12345678901234567890123456789012 ab"))

    assert len(rendered) == LABEL_WIDTH
    assert rendered.endswith("…")


def test_a_label_with_no_title_at_all_is_cut():
    """A single unbroken token has no expendable half; it is cut regardless."""
    from mnemo.core.sessions.render import LABEL_WIDTH, _label

    rendered = _label(_labelled("s", "x" * 40))

    assert len(rendered) == LABEL_WIDTH
    assert rendered.endswith("…")


def test_a_cut_label_never_exceeds_the_budget_by_one():
    """Guards the ellipsis arithmetic: the cut label plus `…` must still fit.

    An off-by-one in the room calculation reads as a rounding detail and costs
    exactly one column — which is all it takes to shove the table.
    """
    from mnemo.core.sessions.render import LABEL_WIDTH, _label

    # Widths chosen to walk the title across the budget boundary.
    for n in range(1, 40):
        label = "#203 " + ("a" * n)
        if len(label) <= LABEL_WIDTH:
            continue
        rendered = _label(_labelled("s", label))
        assert len(rendered) <= LABEL_WIDTH, (label, rendered)


def test_a_label_that_exactly_fills_the_column_is_not_cut():
    """34 columns is 34 columns. `#203 measure unblock edge coverage` is 34."""
    from mnemo.core.sessions.render import LABEL_WIDTH, _label

    label = "#203 measure unblock edge coverage"
    assert len(label) == LABEL_WIDTH

    assert _label(_labelled("s", label)) == label


def test_the_label_is_cut_on_a_word_boundary():
    """Same rule as the activity column, so both columns read alike."""
    from mnemo.core.sessions.render import _label

    cut = _label(_labelled("s", "#193 recall harness hit_slugs migration"))

    assert cut == "#193 recall harness hit_slugs…"


# --- the PR join: git first, the child's report as the fallback (#217) -----
#
# `_prs` read `children[].kind == "pr"` — a list Claude Code writes when it
# happens to notice a PR and mnemo never writes at all. On the 2026-09-13
# dispatch one child's row showed `#212` and its sibling showed a prose
# sentence, though both had opened a PR. The lookup is injected rather than
# called from here because this module is pure by contract: a `gh` call costs
# ~400ms and this runs once per row of a 2s redraw.


def _done(short_id: str = "d1", **kw) -> Session:
    return Session(short_id=short_id, state="done", tempo="idle",
                   name="pronta", **kw)


def test_the_lookup_supplies_a_pr_the_child_never_reported():
    """The case the fallback cannot serve, and the reason for the change."""
    out = render_queue([_done()], None, lambda s: "#212")

    assert "#212" in out


def test_the_lookup_wins_over_what_the_child_volunteered():
    """Git is the authority; `children` is what Claude Code happened to see."""
    session = _done(children=({"id": "999", "kind": "pr"},))

    out = render_queue([session], None, lambda s: "#212")

    assert "#212" in out
    assert "#999" not in out


def test_the_childs_report_is_kept_when_the_lookup_finds_nothing():
    """No gh, no network, a pruned tree — the old behaviour is still right."""
    session = _done(children=({"id": "307", "kind": "pr"},))

    out = render_queue([session], None, lambda s: None)

    assert "#307" in out


def test_detail_is_still_the_last_resort():
    out = render_queue([_done(detail="merged by hand")], None, lambda s: None)

    assert "merged by hand" in out


def test_omitting_the_lookup_changes_nothing():
    """Every caller that predates the parameter keeps its bytes."""
    sessions = [_done(children=({"id": "307", "kind": "pr"},))]

    assert render_queue(sessions) == render_queue(sessions, None, None)


def test_the_lookup_is_only_asked_about_finished_sessions():
    """A `gh` call per row is the cost; the PR column is only in PRONTAS."""
    asked = []
    sessions = [
        _done("d1"),
        Session(short_id="k1", state="working", tempo="active", name="working"),
        Session(short_id="b1", state="working", tempo="blocked", needs="q?"),
    ]

    render_queue(sessions, None, lambda s: asked.append(s.short_id) or None)

    assert asked == ["d1"]
