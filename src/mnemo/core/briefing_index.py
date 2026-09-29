"""The ``[recent-briefings]`` index SessionStart hands a session (#551).

Until #551 the hook pasted the project's newest briefing whole (a median of
6.3 KB). #534 found that briefing is about the session's work in 17.6% of
sessions, and #540 that a wrong one doubles the replies that assume a wrong
state of the work. #548 (``tools/measure_briefing_index.py``) measured this
index instead: the ten newest briefings, newest first, each as its date and
its ``## TL;DR``. It kept about half of the right briefing's help with no
picking, and raised no wrong-state assumptions.

This module is that index, byte for byte: the tool and the hook both build it
here, so what ships cannot drift from what was measured (as #542 did for the
reflex with ``reflex/render.py``). The one thing the hook adds is
:func:`fit`, which drops the oldest entries when the rest of the envelope
leaves too little room for even the headings; #548's 110 units never came
near it (the whole envelope's max was 7,781 bytes against 9,000).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

#: How many of the newest briefings the index lists (#534's pool).
POOL = 10

INDEX_OPEN = "[recent-briefings count=%d newest first: the TL;DR of each of this project's newest session briefings]"
INDEX_CLOSE = "[/recent-briefings]"
#: The framing lines, to mask or strip.
INDEX_FRAMING = re.compile(r"\[/?recent-briefings[^\]]*\]")
_TLDR = re.compile(r"^##\s*TL;DR[^\n]*\n(.*?)(?=^##\s|\Z)", re.S | re.M)
ELLIPSIS = "…"


def _utf8(text: str) -> int:
    return len(text.encode("utf-8"))


def tldr(body: str) -> str:
    """The briefing's ``## TL;DR`` section, else its first paragraph after the title."""
    m = _TLDR.search(body or "")
    if m and m.group(1).strip():
        return m.group(1).strip()
    text = "\n".join(ln for ln in (body or "").splitlines() if not ln.lstrip().startswith("#"))
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras[0] if paras else ""


def cut_to(text: str, budget: int) -> str:
    """``text`` in at most ``budget`` UTF-8 bytes, cut on a word and marked."""
    if _utf8(text) <= budget:
        return text
    room = budget - _utf8(ELLIPSIS)
    if room <= 0:
        return ""
    head = text.encode("utf-8")[:room].decode("utf-8", "ignore")
    space = max(head.rfind(" "), head.rfind("\n"))
    if space > 0:
        head = head[:space]
    return head.rstrip() + ELLIPSIS


def fair_cut(sizes: Sequence[int], room: int) -> List[int]:
    """Each text's byte budget: whole when they all fit, else an equal share,
    with what a short one leaves spread over the longer ones."""
    budgets = [0] * len(sizes)
    if sum(sizes) <= room:
        return list(sizes)
    left, room = sorted(range(len(sizes)), key=lambda i: sizes[i]), max(0, room)
    while left:
        share = room // len(left)
        i = left[0]
        if sizes[i] <= share:
            budgets[i] = sizes[i]
            room -= sizes[i]
            left.pop(0)
            continue
        for i in left:
            budgets[i] = share
        break
    return budgets


def entry_head(meta: Dict[str, Any]) -> str:
    return "### %s" % (meta.get("date") or "undated")


def index_block(entries: Sequence[Tuple[Dict[str, Any], str]], room: int) -> Tuple[str, int]:
    """(the ``[recent-briefings]`` block, starting with its blank-line
    separator, in at most ``room`` bytes where it can be; how many TL;DRs
    were cut). ``entries``: ``(frontmatter, TL;DR)``, newest first."""
    if not entries:
        return "", 0
    heads = [entry_head(m) for m, _ in entries]
    frame = "\n\n" + INDEX_OPEN % len(entries) + "\n"
    close = "\n" + INDEX_CLOSE
    # every byte but the TL;DRs themselves: framing, closer, headings, separators
    fixed = _utf8(frame) + _utf8(close) + sum(_utf8(h) + 1 for h in heads) + 2 * (len(entries) - 1)
    texts = [t for _, t in entries]
    budgets = fair_cut([_utf8(t) for t in texts], room - fixed)
    cut = [cut_to(t, b) for t, b in zip(texts, budgets)]
    body = "\n\n".join(h + "\n" + t for h, t in zip(heads, cut))
    return frame + body + close, sum(1 for t, c in zip(texts, cut) if c != t)


def entries_of(records: Sequence[Any]) -> List[Tuple[Dict[str, Any], str]]:
    """``(frontmatter, TL;DR)`` for briefing records (``BriefingRecord``-shaped:
    ``.frontmatter`` and ``.body``), in the order given."""
    out = []
    for rec in records:
        fm = {k: str(v) for k, v in (rec.frontmatter or {}).items()}
        out.append((fm, tldr(rec.body)))
    return out


def fit(records: Sequence[Any], room: int) -> Tuple[str, int, int]:
    """(the index of ``records`` in at most ``room`` bytes, how many entries it
    lists, how many TL;DRs were cut). ``records`` newest first.

    :func:`index_block` shares whatever room there is among the TL;DRs, but
    its headings and framing are never cut, so when ``room`` is smaller than
    those the oldest entries go until the rest fits; when not even one fits,
    the index is empty.
    """
    entries = entries_of(records)
    while entries:
        block, cut = index_block(entries, room)
        if _utf8(block) <= room:
            return block, len(entries), cut
        entries = entries[:-1]
    return "", 0, 0
