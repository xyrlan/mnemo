"""What Claude Code does with a written notice (#732): its cross-session
inbound policy, read from the 2.1.302 bundle, and mnemo's honest record."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mnemo.core.sessions import inbound_policy as ip
from mnemo.core.sessions import inbox


# -- the policy ---------------------------------------------------------------

def test_an_accept_setting_delivers_whatever_the_modes() -> None:
    look = ip.decide(setting="accept", parent_mode="bypassPermissions", child_mode="auto")
    assert look.accepted is True and look.attest is None


def test_a_bypass_parent_holds_an_auto_childs_notice() -> None:
    look = ip.decide(setting=None, parent_mode="bypassPermissions", child_mode="auto")
    assert look.accepted is False
    assert look.cause == ip.NO_MODE_ASSERTED
    assert look.attest is None  # attesting "prompting" would only make it mode-mismatch
    assert "held for approval by the parent's crossSessionInbound policy" in look.reason


def test_a_bypass_parent_accepts_a_bypass_child_that_attests_its_class() -> None:
    look = ip.decide(setting=None, parent_mode="bypassPermissions",
                     child_mode="bypassPermissions")
    assert (look.accepted, look.attest) == (True, ip.BYPASS)


def test_without_the_envelope_a_bypass_childs_notice_is_held() -> None:
    look = ip.decide(setting=None, parent_mode="bypassPermissions",
                     child_mode="bypassPermissions", can_attest=False)
    assert look.accepted is False and look.attest is None


@pytest.mark.parametrize("child", ["auto", "default", "bypassPermissions", None])
def test_a_prompting_parent_accepts_unattested_notices(child) -> None:
    """A bypass child is not attested here: "bypass" to a prompting parent is
    a mode-mismatch hold, where nothing attested is accepted."""
    look = ip.decide(setting=None, parent_mode="auto", child_mode=child)
    assert (look.accepted, look.attest) == (True, None)


@pytest.mark.parametrize("setting,cause", [("hold", ip.SETTING_HOLD), ("refuse", ip.REFUSE)])
def test_a_hold_or_refuse_setting_wins_over_matching_classes(setting, cause) -> None:
    look = ip.decide(setting=setting, parent_mode="auto", child_mode="auto")
    assert (look.accepted, look.cause) == (False, cause)


@pytest.mark.parametrize("parent", [None, "plan", "somethingNew"])
def test_a_parent_whose_class_cannot_be_told_is_not_confirmed(parent) -> None:
    look = ip.decide(setting=None, parent_mode=parent, child_mode="auto")
    assert look.accepted is None
    assert look.reason.startswith("written; delivery not confirmed")


# -- the settings -------------------------------------------------------------

def _settings(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({ip.SETTING: value}), encoding="utf-8")


def test_the_users_setting_is_read(tmp_path) -> None:
    home = tmp_path / "home"
    _settings(home / "settings.json", "accept")
    assert ip.effective_setting(home=home, cwd=None, managed=[]) == "accept"


def test_a_project_may_tighten_but_not_loosen(tmp_path) -> None:
    home, proj = tmp_path / "home", tmp_path / "proj"
    _settings(home / "settings.json", "accept")
    _settings(proj / ".claude" / "settings.json", "hold")
    assert ip.effective_setting(home=home, cwd=str(proj), managed=[]) == "hold"
    _settings(home / "settings.json", "hold")
    _settings(proj / ".claude" / "settings.local.json", "accept")
    _settings(proj / ".claude" / "settings.json", "accept")
    assert ip.effective_setting(home=home, cwd=str(proj), managed=[]) == "hold"


def test_managed_policy_wins_over_the_user(tmp_path) -> None:
    home, managed = tmp_path / "home", tmp_path / "managed-settings.json"
    _settings(home / "settings.json", "accept")
    _settings(managed, "hold")
    assert ip.effective_setting(home=home, cwd=None, managed=[managed]) == "hold"


def test_an_unrecognized_value_holds(tmp_path) -> None:
    home = tmp_path / "home"
    _settings(home / "settings.json", "yes")
    assert ip.effective_setting(home=home, cwd=None, managed=[]) == "hold"


def test_no_setting_anywhere_is_none(tmp_path) -> None:
    assert ip.effective_setting(home=tmp_path, cwd=str(tmp_path), managed=[]) is None


# -- the transcripts ----------------------------------------------------------

def _transcript(path: Path, modes, cwd: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "user", "cwd": cwd, "message": {"content": "hi"}})]
    for mode in modes:
        lines.append(json.dumps({"type": "permission-mode", "permissionMode": mode}))
        lines.append(json.dumps({"type": "user", "permissionMode": mode, "cwd": cwd,
                                 "message": {"content": "x" * 5000}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_last_mode_a_transcript_records_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ip, "_TAIL_CHUNK", 1024)  # cross several chunks
    path = _transcript(tmp_path / "t.jsonl", ["default", "bypassPermissions"], str(tmp_path))
    assert ip.last_mode(path) == "bypassPermissions"
    path.write_text(path.read_text(encoding="utf-8") + "x" * 10000 + "\n", encoding="utf-8")
    assert ip.last_mode(path) == "bypassPermissions"
    assert ip.last_mode(tmp_path / "missing.jsonl") is None
    assert ip.last_mode(None) is None


def test_outlook_reads_the_parents_transcript_and_settings(tmp_path) -> None:
    home, proj = tmp_path / "claude", tmp_path / "proj"
    _transcript(home / "projects" / "p" / "PARENT.jsonl", ["bypassPermissions"], str(proj))

    held = ip.outlook("PARENT", child_mode="auto", homes=[home], managed=[])
    assert (held.accepted, held.parent_mode, held.child_mode) == (
        False, "bypassPermissions", "auto")

    _settings(home / "settings.json", "accept")
    assert ip.outlook("PARENT", child_mode="auto", homes=[home], managed=[]).accepted is True

    gone = ip.outlook("NOBODY", child_mode="auto", homes=[home], managed=[])
    assert gone.accepted is None and "transcript was not found" in gone.reason


# -- the envelope -------------------------------------------------------------

# Claude Code 2.1.302's parser for the envelope (``_(e)`` beside ``Eyt``): it
# matches this, re-renders what it matched, and keeps ``fromMode`` only when
# the two are equal.
_CC = re.compile(
    r'^<cross-session-message(?: from="([A-Za-z0-9%:_/.\\-]+)")?'
    r'(?: from-session="([A-Za-z0-9_-]{1,80})")?'
    r'(?: hop-chain="([0-9a-f]{24}(?:,[0-9a-f]{24}){0,31})")?'
    r'(?: from-name="([^"<>\n\r]+)")?(?: from-mode="(bypass|prompting)")?'
    r'(?: from-plugin="([^"<>\n\r]+)")?>\n([\s\S]*)\n</cross-session-message>\Z')


def _cc_from_mode(content: str):
    m = _CC.match(content)
    if not m:
        return None
    attrs = "".join(f' {k}="{v}"' for k, v in (
        ("from", m.group(1)), ("from-session", m.group(2)), ("hop-chain", m.group(3)),
        ("from-name", m.group(4)), ("from-mode", m.group(5)), ("from-plugin", m.group(6)),
    ) if v)
    body = m.group(7).replace("</cross-session-message", "<\\/cross-session-message")
    rendered = f"<cross-session-message{attrs}>\n{body}\n</cross-session-message>"
    return m.group(5) if rendered == content else None


def test_the_envelope_carries_the_class_as_claude_code_parses_it() -> None:
    text = '<mnemo-child-finished id="c0da0f55" state="ready">\nPR #12: 3 checks pass.\n'
    wrapped = ip.envelope(text, ip.BYPASS)
    assert _cc_from_mode(wrapped) == "bypass"
    assert text in wrapped


def test_a_body_that_names_the_envelope_is_not_wrapped() -> None:
    assert ip.envelope("quoting </cross-session-message> here", ip.BYPASS) is None
    assert ip.envelope("hi", "bypassPermissions") is None  # a mode, not a class


def test_inbox_drops_an_attestation_the_text_cannot_carry(tmp_path, monkeypatch) -> None:
    home = tmp_path / "claude"
    _transcript(home / "projects" / "p" / "PARENT.jsonl", ["bypassPermissions"], str(tmp_path))
    from mnemo.core.sessions import accounts

    monkeypatch.setattr(accounts, "roots", lambda **kw: [accounts.Root(home=home, jobs=home)])
    monkeypatch.setattr(ip, "managed_settings_paths", lambda: [])

    plain = inbox.outlook(tmp_path, "PARENT", "done", child_mode="bypassPermissions")
    assert (plain.accepted, plain.attest) == (True, ip.BYPASS)
    odd = inbox.outlook(tmp_path, "PARENT", "</cross-session-message>",
                        child_mode="bypassPermissions")
    assert (odd.accepted, odd.attest) == (False, None)


def test_a_grok_parent_has_no_inbound_policy(tmp_path, monkeypatch) -> None:
    from mnemo.core.sessions import accounts

    monkeypatch.setattr(accounts, "roots", lambda **kw: [])
    monkeypatch.setattr(inbox, "grok_address", lambda v, s: {"agent": inbox.GROK})
    assert inbox.outlook(tmp_path, "G", "done", child_mode="auto").accepted is True


def test_deliver_puts_the_attestation_on_a_claude_turn_only(tmp_path, monkeypatch) -> None:
    written = []
    monkeypatch.setattr(inbox, "post_pipe", lambda a, text: written.append(text) or "")
    monkeypatch.setattr(inbox, "resolve", lambda v, s: ({"agent": inbox.PIPE, "socket": "p"}, ""))

    assert inbox.deliver(tmp_path, "P", "done", attest=ip.BYPASS) == ""
    assert inbox.deliver(tmp_path, "P", "done") == ""
    assert _cc_from_mode(written[0]) == "bypass"
    assert written[1] == "done"


# -- no forging -------------------------------------------

MODES = [None, "default", "acceptEdits", "auto", "dontAsk", "plan", "bypassPermissions", "x"]


@pytest.mark.parametrize("setting", [None, "accept", "hold", "refuse"])
@pytest.mark.parametrize("parent", MODES)
@pytest.mark.parametrize("child", MODES)
def test_only_the_childs_own_class_is_ever_attested(setting, parent, child) -> None:
    attest = ip.decide(setting=setting, parent_mode=parent, child_mode=child).attest
    assert attest is None or attest == ip.mode_class(child)


# -- doctor -------------------------------------------------------------------

def _held_row(vault: Path) -> None:
    from mnemo.core.sessions import report_card

    look = ip.decide(setting=None, parent_mode="bypassPermissions", child_mode="auto")
    report_card.sent(vault, {"short_id": "c0da0f55", "parent": "P1", "event": "finished"},
                     look, text="notice")


def test_doctor_names_the_users_lever_when_notices_were_held(tmp_path, capsys) -> None:
    from mnemo.cli.commands.doctor_checks.child_reports import (
        _doctor_check_child_notice_policy,
    )

    _held_row(tmp_path)

    assert _doctor_check_child_notice_policy(tmp_path) is False
    out = capsys.readouterr().out
    assert "1 were held for your approval" in out
    assert '"crossSessionInbound": "accept"' in out
    assert "mnemo never sets it" in out
    # #741: accept is no longer the only lever, so the advice names the others.
    assert "dispatch.childMode" in out
    assert "disableBypassPermissionsMode" in out


def test_doctor_is_silent_once_the_user_accepts(tmp_path, capsys) -> None:
    from mnemo.cli.commands.doctor_checks.child_reports import (
        _doctor_check_child_notice_policy,
    )
    from mnemo.core.sessions import liveness

    _held_row(tmp_path)
    _settings(liveness.claude_home() / "settings.json", "accept")

    assert _doctor_check_child_notice_policy(tmp_path) is True
    assert capsys.readouterr().out == ""


def test_doctor_registers_the_check() -> None:
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert "child_notice_policy" in [name for name, _ in DOCTOR_CHECKS]


def test_the_hooks_one_line_fallback_is_recorded_as_held_too(tmp_path, monkeypatch) -> None:
    from mnemo.core.sessions import child_notices, held_notices, parents, report_card
    from mnemo.hooks import session_end

    held = ip.decide(setting=None, parent_mode="bypassPermissions", child_mode="auto")
    monkeypatch.setattr(parents, "read", lambda v: {"c0da0f55": "P1"})
    monkeypatch.setattr(child_notices, "told_by_backstop", lambda *a: False)
    monkeypatch.setattr(inbox, "resolve", lambda v, p: ({"agent": inbox.PIPE}, ""))
    monkeypatch.setattr(inbox, "outlook", lambda *a, **kw: held)
    monkeypatch.setattr(inbox, "deliver", lambda *a, **kw: "")

    def boom(*a, **kw):
        raise OSError("no spawn")

    monkeypatch.setattr(session_end, "_spawn_detached_child_report", boom)

    session_end._maybe_notify_parent({"dispatch": {"notifyParent": True}}, tmp_path,
                                     session_id="c0da0f55-1111", cwd="/x")

    rows = [json.loads(line) for line in (tmp_path / ".mnemo" / report_card.LOG_NAME)
            .read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["delivered"] is False and rows[-1]["state"] == "thin"
    assert rows[-1]["reason"].startswith("reporter did not start")
    assert "held for approval" in rows[-1]["reason"]
    assert held_notices.log_path(tmp_path).exists()
