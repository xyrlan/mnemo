"""What Claude Code will do with a notice written to a parent's inbox (#732).

A written notice is not a delivered one. Claude Code passes every peer turn
through its cross-session inbound policy before it queues it, and a turn the
policy *holds* is shown to the user for approval, not given to the model. The
sender is never told: the receipt goes to the sender's own inbox, and mnemo
has none. Until #732 ``child-reports.jsonl`` said ``delivered: true`` for every
notice the pipe or socket took, including the ones held.

**The policy, read from the Claude Code 2.1.302 bundle** (``k(e, n)`` beside
the ``[cross-session-inbound]`` log lines):

1. The ``crossSessionInbound`` setting decides when it is set: ``accept``,
   ``hold`` or ``refuse``. The first of managed policy, ``--settings`` and the
   user's ``settings.json`` that sets it wins; the project's ``settings.json``
   and ``settings.local.json`` can then only tighten it (``accept`` < ``hold``
   < ``refuse``). An unrecognized value counts as ``hold``.
2. Otherwise a sender's ``from-mode`` decides: accepted when it names this
   session's permission class, held (``mode-mismatch``) when not.
3. With nothing attested, a ``bypass`` session holds (``no-mode-asserted``) and
   a ``prompting`` one accepts.

A session is in the ``bypass`` class when its mode is ``bypassPermissions``,
or ``plan`` entered with bypass available; every other mode (``default``,
``acceptEdits``, ``auto``, ``dontAsk``) is ``prompting``. ``plan`` is read
here as not known: whether bypass is available is not in the transcript.

**Where each input comes from.** A session's mode is the last
``permissionMode`` its transcript records (Claude Code stamps it on each turn
and on a ``permission-mode`` line when it changes). The parent's settings are
read from the account home its transcript lives under and from the directory
the session started in. Managed policy is read from its file where there is
one; MDM and server-managed policy, and ``--settings``, cannot be read from
outside, so a verdict can be wrong where those are in play.

**Attestation.** ``from-mode`` rides inside the turn's content, as an
attribute of a ``<cross-session-message>`` envelope that Claude Code parses
and re-renders, and honors only when the re-rendering matches byte for byte
(:func:`envelope`). mnemo attests the child's real class, and only where that
turns a hold into an accept: a ``bypass`` child writing to a ``bypass``
parent. Elsewhere attesting changes nothing (a ``prompting`` child to a
``bypass`` parent is held either way) or makes it worse (a ``bypass`` child to
a ``prompting`` parent is accepted only unattested), so the notice goes as it
always did.

mnemo never writes ``crossSessionInbound`` and never asserts a mode the child
is not in. The setting is the operator's: with ``accept``, any process running
as the user that can read ``~/.claude/sessions`` can prompt a bypass session
without approval. ``mnemo doctor`` names it (:func:`advice`); nothing here
sets it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

BYPASS = "bypass"
PROMPTING = "prompting"

#: Modes in the ``prompting`` class (Claude Code's ``Pd`` table, less
#: ``bypassPermissions`` and ``plan``).
PROMPTING_MODES = frozenset({"default", "acceptEdits", "auto", "dontAsk", "bubble"})

SETTING = "crossSessionInbound"
ACCEPT, HOLD, REFUSE = "accept", "hold", "refuse"
_RANK = {ACCEPT: 0, HOLD: 1, REFUSE: 2}

#: The envelope Claude Code reads a sender's ``from-mode`` from.
ENVELOPE_TAG = "cross-session-message"

#: Hold causes, as Claude Code names them.
MODE_MISMATCH = "mode-mismatch"
NO_MODE_ASSERTED = "no-mode-asserted"
SETTING_HOLD = "setting-hold"

_MODE_RE = re.compile(rb'"permissionMode":\s*"([A-Za-z]+)"')
_CWD_RE = re.compile(r'"cwd":\s*("(?:[^"\\]|\\.)*")')

#: How far back from the end a transcript is read for its last mode.
_TAIL_CHUNK = 256 * 1024


@dataclass(frozen=True)
class Outlook:
    """What a written notice will become.

    ``accepted`` is True when Claude Code queues it as a turn, False when it
    holds or refuses it, None when that cannot be told. ``reason`` is ``""``
    when accepted. ``attest`` is the ``from-mode`` to put on the turn, or None.
    """

    accepted: Optional[bool]
    reason: str = ""
    attest: Optional[str] = None
    cause: str = ""
    parent_mode: str = ""
    child_mode: str = ""
    setting: str = ""


ACCEPTED = Outlook(True)


def mode_class(mode: Optional[str]) -> Optional[str]:
    """``bypass``, ``prompting``, or None when *mode* does not say."""
    if mode == "bypassPermissions":
        return BYPASS
    if mode in PROMPTING_MODES:
        return PROMPTING
    return None


def last_mode(transcript: Optional[Path]) -> Optional[str]:
    """The last ``permissionMode`` *transcript* records, or None. Never raises.

    Read from the end, a chunk at a time, so a long transcript costs one
    chunk in the common case.
    """
    if transcript is None:
        return None
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            overlap = b""
            while end > 0:
                start = max(0, end - _TAIL_CHUNK)
                fh.seek(start)
                block = fh.read(end - start) + overlap
                found = _MODE_RE.findall(block)
                if found:
                    return found[-1].decode("ascii")
                overlap = block[:64]
                end = start
    except OSError:
        return None
    return None


def first_cwd(transcript: Path) -> Optional[str]:
    """The directory *transcript*'s session started in, or None."""
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(200), fh):
                match = _CWD_RE.search(line)
                if match:
                    value = json.loads(match.group(1))
                    return value if isinstance(value, str) and value else None
    except (OSError, ValueError):
        return None
    return None


def find_transcript(session_id: str, homes: Iterable[Path]) -> Optional[Tuple[Path, Path]]:
    """``(transcript, home)`` for *session_id* under the first home that has it."""
    if not session_id:
        return None
    for home in homes:
        try:
            for path in (Path(home) / "projects").glob(f"*/{session_id}.jsonl"):
                if path.is_file():
                    return path, Path(home)
        except OSError:
            continue
    return None


def managed_settings_paths() -> List[Path]:
    """Where a managed-settings file lives on this platform."""
    if sys.platform == "win32":
        return [Path(r"C:\Program Files\ClaudeCode\managed-settings.json"),
                Path(r"C:\ProgramData\ClaudeCode\managed-settings.json")]
    if sys.platform == "darwin":
        return [Path("/Library/Application Support/ClaudeCode/managed-settings.json")]
    return [Path("/etc/claude-code/managed-settings.json")]


def _setting_in(path: Path) -> Optional[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or SETTING not in data:
        return None
    value = data.get(SETTING)
    return value if value in _RANK else HOLD  # unrecognized: Claude Code holds


def effective_setting(*, home: Optional[Path], cwd: Optional[str],
                      managed: Optional[Iterable[Path]] = None) -> Optional[str]:
    """``crossSessionInbound`` as Claude Code resolves it for a session, or None.

    Managed policy, then the user's settings, first set wins; the project's
    two files may only tighten that.
    """
    value = None
    for path in list(managed_settings_paths() if managed is None else managed) + (
            [Path(home) / "settings.json"] if home else []):
        value = _setting_in(path)
        if value is not None:
            break
    if cwd:
        for name in ("settings.local.json", "settings.json"):
            found = _setting_in(Path(cwd) / ".claude" / name)
            if found is not None and _RANK[found] > _RANK[value or ACCEPT]:
                value = found
    return value


def decide(*, setting: Optional[str], parent_mode: Optional[str],
           child_mode: Optional[str], can_attest: bool = True) -> Outlook:
    """The policy over known inputs: what Claude Code does with the notice.

    Attests only where it turns a hold into an accept (module docstring), and
    only when *can_attest*: :func:`envelope` cannot carry every text.
    """
    facts = {"parent_mode": parent_mode or "", "child_mode": child_mode or "",
             "setting": setting or ""}
    if setting == ACCEPT:
        return Outlook(True, **facts)
    if setting in (HOLD, REFUSE):
        verb = "held for approval" if setting == HOLD else "refused"
        return Outlook(False, f"{verb} by the parent's crossSessionInbound "
                              f"setting ({setting!r})",
                       cause=SETTING_HOLD if setting == HOLD else REFUSE, **facts)
    parent = mode_class(parent_mode)
    if parent is None:
        why = (f"the parent's permission mode {parent_mode!r} does not say its class"
               if parent_mode else "the parent's permission mode could not be read")
        return Outlook(None, f"written; delivery not confirmed: {why}", **facts)
    child = mode_class(child_mode)
    if parent == PROMPTING:
        return Outlook(True, **facts)
    if child == BYPASS and can_attest:
        return Outlook(True, attest=BYPASS, **facts)
    if child == PROMPTING:
        why = f"the child runs {child_mode}, another class, so attesting it would not help"
    elif child == BYPASS:
        why = f"the child runs {child_mode}, but this notice cannot carry the attestation"
    else:
        why = "the child's mode is not known, so nothing was attested"
    return Outlook(False, f"held for approval by the parent's crossSessionInbound policy "
                          f"({NO_MODE_ASSERTED}: the parent runs {parent_mode}; {why})",
                   cause=NO_MODE_ASSERTED, **facts)


def outlook(parent_session: str, *, child_mode: Optional[str],
            homes: Iterable[Path], managed: Optional[Iterable[Path]] = None) -> Outlook:
    """:func:`decide` over what can be read about *parent_session*. Never raises."""
    try:
        found = find_transcript(parent_session, homes)
        if found is None:
            return Outlook(None, "written; delivery not confirmed: the parent's "
                                 "transcript was not found", child_mode=child_mode or "")
        transcript, home = found
        setting = effective_setting(home=home, cwd=first_cwd(transcript), managed=managed)
        return decide(setting=setting, parent_mode=last_mode(transcript),
                      child_mode=child_mode)
    except Exception as exc:  # noqa: BLE001 — never raises
        return Outlook(None, f"written; delivery not confirmed: {type(exc).__name__}: {exc}")


def envelope(text: str, from_mode: str) -> Optional[str]:
    """*text* in the envelope Claude Code reads ``from-mode`` from, or None.

    Claude Code re-renders what it parsed and drops the attestation unless the
    two match exactly, escaping ``</cross-session-message`` inside the body on
    the way; a body that holds that string is not wrapped.
    """
    if from_mode not in (BYPASS, PROMPTING) or not text:
        return None
    if ENVELOPE_TAG in text.lower():
        return None
    return f'<{ENVELOPE_TAG} from-mode="{from_mode}">\n{text}\n</{ENVELOPE_TAG}>'


def advice(rows: Iterable[dict], *, setting: Optional[str]) -> List[str]:
    """``mnemo doctor``'s lines for child notices held by mode class, or ``[]``.

    *rows* are ``child-reports.jsonl`` rows; *setting* the user's own
    ``crossSessionInbound``. Silent once that is ``accept``.
    """
    if setting == ACCEPT:
        return []
    held = [r for r in rows if r.get("held") in (MODE_MISMATCH, NO_MODE_ASSERTED)
            and mode_class(r.get("parent_mode")) == BYPASS
            and mode_class(r.get("child_mode")) == PROMPTING]
    if not held:
        return []
    parents = sorted({str(r.get("parent_mode")) for r in held if r.get("parent_mode")})
    children = sorted({str(r.get("child_mode")) for r in held if r.get("child_mode")})
    return [
        f"  ⚠ child notices: {len(held)} were held for your approval: your sessions run "
        f"{', '.join(parents) or '?'} and their children ran {', '.join(children) or '?'}, "
        "so Claude Code asks before a child's notice reaches the parent",
        '    → every child notice will ask for approval until you set '
        '"crossSessionInbound": "accept" in ~/.claude/settings.json. The trade-off: any '
        "process running as you that can read ~/.claude/sessions can then prompt your "
        "bypass sessions without approval. mnemo never sets it; the held copy still "
        "reaches each parent at its next prompt",
    ]
