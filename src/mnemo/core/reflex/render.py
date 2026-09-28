"""The text of a reflex injection: what the agent reads for each emitted rule.

One module for the hook (``hooks/user_prompt_submit.py``) and the tools that
measure it (``tools/measure_full_body.py``, ``tools/measure_reflex_fit.py``),
so what ships and what was measured cannot drift (#542).

Two shapes, chosen by ``reflex.body``:

- ``full`` (the default) — ``• [[slug]]:`` then the rule's whole body on the
  lines under it: :func:`full_body`, which drops the frontmatter and the graph
  section as the preview does and cuts nothing. #535 measured it against the
  preview on #527's reflex units: net help +0.215 against −0.021, paired
  difference +0.236 [+0.090, +0.396]. A whole body carries no
  ``read_mnemo_rule`` suffix; there is nothing left to read.
- ``preview`` — the shape shipped before #542, byte for byte: the 300-character
  preview on one line and the suffix. Kept only so a later comparison can run
  the old arm; it is not an opt-in.

The whole block stays within :data:`~mnemo.core.hook_envelope.ENVELOPE_MAX_BYTES`
(#533): past 10,000 characters Claude Code replaces a hook's text with a 2 KB
preview, which would lose every rule at once. When the bodies do not fit, each
rule gets a fair share of the room (a rule shorter than its share keeps its
whole body and leaves the rest to the others), and a body over its share is
cut at a line break and ends with the page's path and the suffix, so the rest
is one call away.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence

from mnemo.core.hook_envelope import ENVELOPE_MAX_BYTES, utf8_len

HEADER = "mnemo reflex context:"
READ_SUFFIX = "(call read_mnemo_rule if you need the full file)"
FULL, PREVIEW = "full", "preview"
FORMATS = (FULL, PREVIEW)
#: No real rule body is this long: ``body_preview`` with it returns the body uncut.
UNCUT = 10 ** 9


class Entry(NamedTuple):
    """One emitted rule, as the renderer needs it. ``body`` is None when the
    page could not be read; the rule then goes out as its preview line."""
    slug: str
    preview: str
    body: Optional[str] = None
    path: str = ""


class Rendered(NamedTuple):
    """The ``additionalContext`` text, and per rule (in order) the bytes of
    its line and whether its whole body is in the text."""
    text: str
    rule_bytes: List[int]
    rule_whole: List[bool]


def body_format(reflex_cfg: Optional[dict]) -> str:
    """``reflex.body``; anything but ``preview`` reads as ``full``."""
    value = str(((reflex_cfg or {}).get("body") or FULL)).strip().lower()
    return value if value in FORMATS else FULL


def full_body(page_text: str) -> str:
    """The rule body the reflex preview is cut from, uncut."""
    from mnemo.core.text_utils import body_preview

    return body_preview(page_text, max_chars=UNCUT)


def full_line(head: str, body: str) -> str:
    """A rule's body under its ``• [[slug]]`` head — the shape #535 measured."""
    return "%s:\n%s" % (head, body)


def bullet(slug: str) -> str:
    return "• [[%s]]" % slug


def preview_line(slug: str, preview: str) -> str:
    """The line the reflex wrote before #542."""
    squashed = preview.replace("\n", " ").strip()
    return f"{bullet(slug)}: {squashed} {READ_SUFFIX}."


def _squash(text: str) -> str:
    return " ".join(text.split())


def cut_line(slug: str, body: str, path: str, budget: int) -> str:
    """``slug``'s body cut to fit ``budget`` bytes, ending with where the rest is.

    The cut falls on the last line break that fits; a body with none inside
    the budget falls back to the last whitespace past the budget's midpoint,
    then to the raw byte cut. When not even the head and the pointer fit, the
    pointer goes out alone under the head.
    """
    where = f"the full rule is at {path}" if path else "the rule is longer"
    tail = f"\n[cut to fit the prompt limit — {where}] {READ_SUFFIX}."
    head = bullet(slug) + ":\n"
    room = budget - utf8_len(head) - utf8_len(tail)
    kept = ""
    if room > 0:
        raw = body.encode("utf-8")[:room].decode("utf-8", "ignore")
        newline = raw.rfind("\n")
        if newline > 0:
            kept = raw[:newline]
        else:
            space = max(raw.rfind(" "), raw.rfind("\t"))
            kept = raw[:space] if space > room // 2 else raw
    kept = kept.rstrip()
    if not kept:
        return bullet(slug) + ":" + tail
    return head + kept + tail


def fair_shares(sizes: Sequence[int], room: int) -> List[int]:
    """Split ``room`` bytes over ``sizes``: max-min fair.

    A size under its equal share is granted whole and its leftover goes to
    the rest; every size left over the share gets the same share.
    """
    shares = [0] * len(sizes)
    remaining = max(0, room)
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    for k, i in enumerate(order):
        share = remaining // (len(order) - k)
        shares[i] = min(sizes[i], share)
        remaining -= shares[i]
    return shares


def render(entries: Sequence[Entry], fmt: str = FULL,
           max_bytes: int = ENVELOPE_MAX_BYTES) -> Rendered:
    """The reflex ``additionalContext`` for ``entries``, in ``fmt``."""
    if fmt == PREVIEW:
        lines = [preview_line(e.slug, e.preview) for e in entries]
        whole = [e.body is not None and _squash(e.body) == _squash(e.preview)
                 for e in entries]
        return _done(lines, whole)

    lines = [full_line(bullet(e.slug), e.body) if e.body is not None
             else preview_line(e.slug, e.preview) for e in entries]
    whole = [e.body is not None for e in entries]
    sizes = [utf8_len(ln) for ln in lines]
    # The header and one newline before each line are fixed.
    room = max_bytes - utf8_len(HEADER) - len(lines)
    if sum(sizes) > room:
        # A preview line cannot be cut further; it is paid for first.
        cuttable = [i for i, e in enumerate(entries) if e.body is not None]
        fixed = sum(sizes[i] for i in range(len(lines)) if i not in cuttable)
        shares = fair_shares([sizes[i] for i in cuttable], room - fixed)
        for i, share in zip(cuttable, shares):
            if sizes[i] > share:
                e = entries[i]
                lines[i] = cut_line(e.slug, e.body or "", e.path, share)
                whole[i] = False
    return _done(lines, whole)


def _done(lines: List[str], whole: List[bool]) -> Rendered:
    return Rendered("\n".join([HEADER] + lines), [utf8_len(ln) for ln in lines], whole)

