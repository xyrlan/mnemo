"""The SessionStart envelope stays under Claude Code's persist limit (#533).

Claude Code saves a hook text of 10,000 characters or more to a file and puts
a 2 KB preview in context (``tools/measure_persist_threshold.py``). 28 of 343
envelopes in a month went that way, and ``[mnemo learned]``, which sat after
the briefing, survived the preview in none of them. So the briefing goes last
and is the one block cut to fit; everything before it is read whole.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from mnemo.hooks import session_start
from mnemo.hooks.session_start import (
    BRIEFING_TRIMMED,
    ENVELOPE_MAX_BYTES,
    _build_injection_payload,
    _fit_briefing,
)

LEARNED = "[mnemo learned since your last session]\n• a-rule — A rule\n[/mnemo learned]"


def _write_briefing(vault: Path, agent: str, session_id: str, body: str) -> Path:
    sessions_dir = vault / "bots" / agent / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    p = sessions_dir / f"{session_id}.md"
    p.write_text(
        "---\ntype: briefing\n"
        f"agent: {agent}\nsession_id: {session_id}\n"
        "date: 2026-09-28\nduration_minutes: 42\n---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def _long_body(n_lines: int, word: str = "line") -> str:
    return "\n".join(f"- {word} {i:04d} of the briefing, long enough to count" for i in range(n_lines))


def _block(body: str) -> str:
    return "\n\n[last-briefing session=s date=d duration_minutes=1]\n" + body + "\n[/last-briefing]"


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


# --- _fit_briefing --------------------------------------------------------------------


def test_a_briefing_that_fits_is_untouched():
    block = _block("short")
    assert _fit_briefing(block, 10_000, "/v/b.md") == block


def test_a_cut_briefing_fits_ends_on_a_whole_line_and_names_its_file():
    block = _block(_long_body(300))
    out = _fit_briefing(block, 2_000, "/v/bots/p/briefings/sessions/s.md")
    assert _size(out) <= 2_000
    assert out.startswith("\n\n[last-briefing session=s")
    assert out.endswith("\n[/last-briefing]")
    last = out.splitlines()[-2]
    assert last == BRIEFING_TRIMMED + "/v/bots/p/briefings/sessions/s.md]"
    # every body line kept is a whole line of the original
    kept = out.splitlines()[3:-2]
    assert kept and all(ln in block.splitlines() for ln in kept)


def test_the_cut_counts_bytes_not_characters():
    """Three bytes a character: a character count would overshoot the room threefold."""
    block = _block(_long_body(300, word="ação—"))
    out = _fit_briefing(block, 3_000, "/v/s.md")
    assert _size(out) <= 3_000
    assert "�" not in out


def test_no_room_keeps_the_frame_and_the_pointer():
    block = _block(_long_body(50))
    out = _fit_briefing(block, 10, "/v/s.md")
    assert out.splitlines()[2:] == [
        "[last-briefing session=s date=d duration_minutes=1]",
        BRIEFING_TRIMMED + "/v/s.md]",
        "[/last-briefing]",
    ]


def test_no_briefing_stays_no_briefing():
    assert _fit_briefing("", 0, None) == ""


# --- the builder ------------------------------------------------------------------------


def test_blocks_sit_between_the_menu_and_the_briefing(tmp_path):
    _write_briefing(tmp_path, "proj", "s1", "Stopped at line 42")
    out = _build_injection_payload(
        tmp_path, current_project="proj", inject_briefing=True, blocks=["[mnemo] a notice", "", LEARNED],
    )
    assert out.index("[mnemo] a notice") < out.index("[mnemo learned") < out.index("[last-briefing")
    assert out.rstrip().endswith("[/last-briefing]")
    assert BRIEFING_TRIMMED not in out


def test_an_oversized_briefing_is_cut_and_nothing_before_it(tmp_path):
    path = _write_briefing(tmp_path, "proj", "s1", _long_body(400))
    out = _build_injection_payload(
        tmp_path, current_project="proj", inject_briefing=True, blocks=["[mnemo] a notice", LEARNED],
    )
    assert _size(out) <= ENVELOPE_MAX_BYTES
    assert "[mnemo] a notice" in out and LEARNED in out
    assert BRIEFING_TRIMMED + f"{path}]" in out
    assert out.rstrip().endswith("[/last-briefing]")


def test_blocks_alone_are_an_envelope(tmp_path):
    out = _build_injection_payload(tmp_path, current_project="proj", blocks=[LEARNED])
    assert out == LEARNED


def test_nothing_is_still_nothing(tmp_path):
    assert _build_injection_payload(tmp_path, current_project="proj", blocks=["", ""]) == ""


# --- the hook ---------------------------------------------------------------------------


def test_the_hook_caps_the_envelope_and_logs_what_it_did(tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys):
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "injection": {"enabled": True, "telemetry": {"enabled": True}},
        # the whole briefing is what _fit_briefing cuts; the index shares its
        # room out itself (test_session_start_briefing_index.py)
        "briefings": {"enabled": True, "injectLastOnSessionStart": True, "sessionStart": "last"},
        "capture": {"sessionStartEnd": False},
    }), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))
    _write_briefing(tmp_vault, "vault", "abc123", _long_body(400))
    monkeypatch.setattr(session_start, "_learned_block", lambda *a, **k: LEARNED)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "new-session", "cwd": str(tmp_vault), "source": "startup"})))

    assert session_start.main() == 0

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert _size(context) <= ENVELOPE_MAX_BYTES
    assert context.index(LEARNED) < context.index("[last-briefing")
    assert BRIEFING_TRIMMED in context

    log = tmp_vault / ".mnemo" / "mcp-access-log.jsonl"
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    inj = [r for r in rows if r.get("tool") == "session_start.inject"]
    assert len(inj) == 1
    assert inj[0]["envelope_chars"] == len(context)
    assert inj[0]["envelope_bytes"] == _size(context)
    assert inj[0]["briefing_trimmed"] is True
