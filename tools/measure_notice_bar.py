"""Skip machine notices, raise the reflex's bar for them, or leave them? (#789)

Usage:
    PYTHONPATH=src python3 tools/measure_notice_bar.py           # the report, local
    PYTHONPATH=src python3 tools/measure_notice_bar.py --json    # the same as data

Read-only. It labels nothing and calls nothing: it reads #764's frozen sample
and its blind labels (``<vault>/.mnemo/inject-band/``, written by
``tools/measure_inject_band.py``), and rebuilds the population's prompt texts
from the reflex log and the transcripts the way that tool does.

**Why.** #764 found 45% of the prompts the reflex injected into are machine
notices nobody typed, and the near-bar notices its rater read were on-point
20% of the time. #789 asks for a bar, written before it is read, that picks
one of three branches for those prompts.

**Kind.** A prompt is a notice when :func:`mnemo.core.reflex.notice.kind`
says so — the classifier the hook ships, not #764's prefix list: the whole
prompt is a ``<task-notification>``, ``<cross-session-message>`` or
``<agent-message>`` element, a ``<mnemo-child-finished`` notice, or a peer
message in Claude Code's ``Another Claude session sent a message:`` frame.
``<mnemo-resume`` and ``<mnemo-pr-follow`` wakes, which #764 counted as
notices, are work orders and count as typed here. Everything else is typed.

**Pre-registered (committed before this tool computed anything).** #764's
labels have been read and its per-cell rates are in #789's body, so this bar
is not blind to them; what it does instead is reuse #764's own committed
reading, :func:`measure_inject_band.verdict` — ``yes`` when the 95% CI of the
difference (resampled by whole prompt, #764's seed and draws) is above 0
*and* the low side is at most half the high side — and add nothing tuned.
The branches, checked in this order, the first ``yes`` winning:

1. **skip**: the reflex does not run on notices, when the verdict of
   ``on_point(typed) - on_point(notice)``, both bands, is ``yes``. (This is
   #764's secondary "skip notices" bar, re-read with the shipped classifier.)
2. **raise**: notices need the judge's 0.5 where typed prompts need
   ``injectAt``, when the verdict of
   ``on_point(notice, >= 0.5) - on_point(notice, 0.40-0.49)`` is ``yes``.
   (This is #764's primary "raise to 0.5" bar, read within notices.)
3. **leave**: otherwise.

Reported beside the verdict, from the same data, for the branch picked and
for the other two: the injected bytes each would cut (population pairs it
removes, their logged ``rule_bytes``) and the on-point injections it would
lose (pairs removed in each cell times that cell's labelled on-point rate,
as #764 estimated its own).
"""
