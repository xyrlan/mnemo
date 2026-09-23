"""``mnemo rerank --reflex`` — the per-prompt judge's own switch (#412).

Nothing here reaches the network, and this command must not even try: the
whole point of ``--reflex on`` is that it makes no request of its own, so a
test that lets it run with ``urlopen`` wired to fail is the assertion.

What is pinned: the consent paragraph says the thing that is different about
this stage (your own prompt text) and matches the docs word for word; it
refuses without a tty and without a key; ``--off`` turns both stages off;
and the report and the doctor row read the reflex log rather than the access
log.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from mnemo.cli.commands import rerank as cmd
from mnemo.cli.parser import COMMANDS, _build_parser
from mnemo.core import secrets
from mnemo.core.mcp import rerank as mcp_rerank

SENTINEL = "sk-live-DO-NOT-PRINT-412"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(mcp_rerank.DEFAULT_KEY_ENV, raising=False)


@pytest.fixture
def config_path(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "vaultish" / "mnemo.config.json"
    target.parent.mkdir(parents=True)
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(target))
    return target


@pytest.fixture
def secrets_path(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "machine" / ".mnemo" / "secrets.json"
    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(target))
    return target


def _run(monkeypatch, vault: Path, argv: list):
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = _build_parser().parse_args(["rerank", *argv])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = COMMANDS["rerank"](args)
    return code, out.getvalue(), err.getvalue()


def _reflex_log(vault: Path, rows: list) -> None:
    target = vault / ".mnemo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "reflex-log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(status: str, *, asked=3, injected=1, ms=900, ts="2099-01-01T00:00:00Z") -> dict:
    return {"ts": ts, "session_id": "s", "project": "p", "prompt_hash": "sha256:abc",
            "emitted": [], "scores": [], "silence_reason": "judge_none_relevant",
            "judge": {"status": status, "asked": asked, "injected": injected,
                      "ms": ms, "scores": []}}


# --- turning it on -----------------------------------------------------------

def test_reflex_is_mutually_exclusive_with_setup_and_off():
    for other in ("--setup", "--off"):
        with pytest.raises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                _build_parser().parse_args(["rerank", "--reflex", "on", other])


def test_on_refuses_off_a_tty(monkeypatch, tmp_vault, config_path, secrets_path):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, out, err = _run(monkeypatch, tmp_vault, ["--reflex", "on"])
    assert code == 2
    assert "without a tty" in err
    assert not config_path.exists()


def test_on_refuses_without_a_key_and_names_setup(
        monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, err = _run(monkeypatch, tmp_vault, ["--reflex", "on", "--yes"])
    assert code == 2
    assert "mnemo rerank --setup" in err
    assert not config_path.exists(), "a refusal must write nothing"


def test_on_sets_the_one_key_and_makes_no_request(
        monkeypatch, tmp_vault, config_path, secrets_path):
    """The key was already proved by `--setup`; asking the provider again
    would be a second consent for a request nobody needs."""
    secrets.write("typesafe", SENTINEL)
    code, out, _ = _run(monkeypatch, tmp_vault, ["--reflex", "on", "--yes"])

    assert code == 0
    assert SENTINEL not in out
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written == {"reflex": {"judge": {"provider": "typesafe"}}}, \
        "only the provider key, never a dump of the defaults"


def test_on_keeps_every_other_key_in_the_file(
        monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps(
        {"extraction": {"chunkSize": 99}, "reflex": {"enabled": True}}), encoding="utf-8")
    secrets.write("typesafe", SENTINEL)
    _run(monkeypatch, tmp_vault, ["--reflex", "on", "--yes"])

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["extraction"]["chunkSize"] == 99
    assert written["reflex"] == {"enabled": True, "judge": {"provider": "typesafe"}}


def test_off_sets_the_provider_back_and_leaves_the_key_alone(
        monkeypatch, tmp_vault, config_path, secrets_path):
    """`--reflex off` is not `--off`: the list stage and the stored key stay."""
    secrets.write("typesafe", SENTINEL)
    config_path.write_text(json.dumps({
        "recall": {"rerank": {"provider": "typesafe"}},
        "reflex": {"judge": {"provider": "typesafe"}}}), encoding="utf-8")

    code, out, _ = _run(monkeypatch, tmp_vault, ["--reflex", "off"])

    assert code == 0
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["reflex"]["judge"]["provider"] == "none"
    assert written["recall"]["rerank"]["provider"] == "typesafe"
    assert secrets.read("typesafe") == SENTINEL


def test_the_whole_command_off_turns_the_prompt_stage_off_too(
        monkeypatch, tmp_vault, config_path, secrets_path):
    """An "off" that left prompt text going to a third party would be the
    opposite of what this flag is for."""
    secrets.write("typesafe", SENTINEL)
    config_path.write_text(json.dumps({
        "recall": {"rerank": {"provider": "typesafe"}},
        "reflex": {"judge": {"provider": "typesafe"}}}), encoding="utf-8")

    code, out, _ = _run(monkeypatch, tmp_vault, ["--off"])

    assert code == 0
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["reflex"]["judge"]["provider"] == "none"
    assert written["recall"]["rerank"]["provider"] == "none"
    assert secrets.read("typesafe") is None
    assert "reflex.judge.provider = 'none'" in out


# --- the consent text --------------------------------------------------------

def test_the_consent_says_what_is_different_about_this_stage():
    flat = re.sub(r"\s+", " ", cmd.REFLEX_SENDS)
    assert "the text of each prompt you type (first 1,200 characters)" in flat
    assert "api.typesafe.ai" in flat
    assert "UserPromptSubmit hook" in flat


def test_what_reflex_on_prints_is_what_the_docs_promise():
    """Whoever edits one is made to edit the other."""
    repo = Path(__file__).resolve().parents[2]
    doc = (repo / "docs" / "configuration.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", doc).replace("**", "").replace("`", "")
    assert re.sub(r"\s+", " ", cmd.REFLEX_SENDS).replace("`", "") in flat


def test_the_paragraph_is_printed_before_the_question(
        monkeypatch, tmp_vault, config_path, secrets_path):
    secrets.write("typesafe", SENTINEL)
    code, out, _ = _run(monkeypatch, tmp_vault, ["--reflex", "on", "--yes"])
    flat = re.sub(r"\s+", " ", out)
    assert "first 1,200 characters" in flat


# --- offered by --setup (#461) -----------------------------------------------

def _answering(value=0.9):
    def client(state, questions):
        return {"answers": {key: {"noul": value} for key in questions}}
    return client


def _setup(monkeypatch, vault, argv, *, answers=None, tty=False, client=None):
    """``--setup`` with the probe answered, the key read and any questions
    answered from ``answers`` in order; records the questions asked."""
    asked = []
    monkeypatch.setattr(mcp_rerank, "typesafe_client",
                        lambda k, **kw: (client or _answering()))
    monkeypatch.setattr("sys.stdin", io.StringIO(SENTINEL))
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty, raising=False)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: SENTINEL)
    queue = list(answers or [])

    def fake_input(prompt=""):
        asked.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    code, out, err = _run(monkeypatch, vault, argv)
    return code, out, err, asked


def _judge_provider(config_path: Path):
    written = json.loads(config_path.read_text(encoding="utf-8"))
    return written.get("reflex", {}).get("judge", {}).get("provider")


def test_setup_asks_for_the_judge_after_the_key_works(
        monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, err, asked = _setup(monkeypatch, tmp_vault, ["--setup"],
                                   answers=["y", "y"], tty=True)
    assert code == 0, err
    assert asked[-1] == "Turn on the per-prompt judge too? [Y/n] "
    flat = re.sub(r"\s+", " ", out)
    # Its own paragraph, word for word, after the key was proved and stored.
    assert re.sub(r"\s+", " ", cmd.REFLEX_SENDS) in flat
    assert flat.index("the provider answered") < flat.index("first 1,200 characters")
    assert _judge_provider(config_path) == "typesafe"
    assert SENTINEL not in out and SENTINEL not in err


def test_setup_judge_question_defaults_to_yes(
        monkeypatch, tmp_vault, config_path, secrets_path):
    code, _, err, _ = _setup(monkeypatch, tmp_vault, ["--setup"],
                             answers=["y", ""], tty=True)
    assert code == 0, err
    assert _judge_provider(config_path) == "typesafe"


def test_setup_writes_what_reflex_on_writes(
        monkeypatch, tmp_vault, config_path, secrets_path):
    """The same one key --reflex on sets, next to the list stage's own."""
    _setup(monkeypatch, tmp_vault, ["--setup"], answers=["y", "y"], tty=True)
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "recall": {"rerank": {"provider": "typesafe"}},
        "reflex": {"judge": {"provider": "typesafe"}}}


@pytest.mark.parametrize("answer", ["n", "no", "N"])
def test_setup_judge_declined_leaves_it_off_and_says_how(
        monkeypatch, tmp_vault, config_path, secrets_path, answer):
    code, out, err, _ = _setup(monkeypatch, tmp_vault, ["--setup"],
                               answers=["y", answer], tty=True)
    assert code == 0, err
    assert _judge_provider(config_path) is None
    assert "mnemo rerank --reflex on" in out
    # The list stage and the key are what the user did say yes to.
    assert secrets.read("typesafe") == SENTINEL


def test_setup_judge_eof_is_a_no(monkeypatch, tmp_vault, config_path, secrets_path):
    code, _, err, _ = _setup(monkeypatch, tmp_vault, ["--setup"],
                             answers=["y"], tty=True)
    assert code == 0, err
    assert _judge_provider(config_path) is None


def test_setup_with_key_stdin_never_turns_the_judge_on_silently(
        monkeypatch, tmp_vault, config_path, secrets_path):
    """`--yes` is the list stage's consent; it is not the judge's."""
    code, out, err, asked = _setup(
        monkeypatch, tmp_vault, ["--setup", "--yes", "--key-stdin"], tty=True)
    assert code == 0, err
    assert asked == [], "stdin held the key; there is no one to ask"
    assert _judge_provider(config_path) is None
    assert "--judge" in out and "mnemo rerank --reflex on" in out


def test_setup_off_a_tty_never_turns_the_judge_on_silently(
        monkeypatch, tmp_vault, config_path, secrets_path):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, out, err, asked = _setup(monkeypatch, tmp_vault, ["--setup", "--yes"])
    assert code == 0, err
    assert asked == []
    assert _judge_provider(config_path) is None


def test_setup_judge_flag_is_the_scripted_consent(
        monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, err, asked = _setup(
        monkeypatch, tmp_vault, ["--setup", "--yes", "--key-stdin", "--judge"])
    assert code == 0, err
    assert asked == []
    assert _judge_provider(config_path) == "typesafe"
    flat = re.sub(r"\s+", " ", out)
    assert re.sub(r"\s+", " ", cmd.REFLEX_SENDS) in flat, \
        "a script's consent still prints what it consented to"


def test_a_failed_setup_never_offers_the_judge(
        monkeypatch, tmp_vault, config_path, secrets_path):
    def refuse(state, questions):
        raise RuntimeError("down")

    code, out, _, asked = _setup(
        monkeypatch, tmp_vault, ["--setup", "--yes", "--key-stdin", "--judge"],
        client=refuse)
    assert code == 1
    assert "first 1,200 characters" not in out
    assert not config_path.exists()


def test_setup_does_not_ask_again_when_the_judge_is_on(
        monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps(
        {"reflex": {"judge": {"provider": "typesafe"}}}), encoding="utf-8")
    code, out, err, asked = _setup(monkeypatch, tmp_vault, ["--setup"],
                                   answers=["y"], tty=True)
    assert code == 0, err
    assert asked == ["Turn it on? [y/N] "]
    assert "already on" in out
    assert _judge_provider(config_path) == "typesafe"


# --- the report --------------------------------------------------------------

def test_the_report_says_the_reflex_stage_is_off(
        monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "reflex judge: off" in out
    assert "mnemo rerank --reflex on" in out


def test_the_report_counts_the_reflex_log(
        monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps(
        {"reflex": {"judge": {"provider": "typesafe"}}}), encoding="utf-8")
    monkeypatch.setenv(mcp_rerank.DEFAULT_KEY_ENV, SENTINEL)
    _reflex_log(tmp_vault, [_row("ok", ms=800), _row("ok", asked=2, injected=0, ms=1200),
                            _row("timeout", asked=3, injected=0, ms=2500)])

    code, out, _ = _run(monkeypatch, tmp_vault, ["--days", "100000"])

    assert code == 0
    assert "reflex judge: on" in out
    assert "3 prompts judged" in out
    assert "2 ok" in out and "1 timeout" in out
    assert "8 rules asked, 1 injected" in out
    assert "1200 ms median" in out
    assert SENTINEL not in out


def test_the_report_calls_out_a_provider_with_no_key(
        monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps(
        {"reflex": {"judge": {"provider": "typesafe"}}}), encoding="utf-8")
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "no key anywhere, so every prompt falls back" in out


def test_the_json_report_carries_the_reflex_block(
        monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps(
        {"reflex": {"judge": {"provider": "typesafe"}}}), encoding="utf-8")
    monkeypatch.setenv(mcp_rerank.DEFAULT_KEY_ENV, SENTINEL)
    _reflex_log(tmp_vault, [_row("ok")])

    code, out, _ = _run(monkeypatch, tmp_vault, ["--json", "--days", "100000"])

    data = json.loads(out)
    assert data["reflex"]["provider"] == "typesafe"
    assert data["reflex"]["prompts"] == 1
    assert data["reflex"]["keySource"] == "env"
    assert SENTINEL not in out


# --- doctor ------------------------------------------------------------------

def _doctor(cfg):
    from mnemo.cli.commands.doctor_checks import rerank as check

    return check.check_rerank(cfg)


def test_doctor_is_silent_with_the_stage_off(secrets_path):
    assert _doctor({"reflex": {"judge": {"provider": "none"}}}) is None


def test_doctor_fails_the_row_when_the_reflex_stage_has_no_key(secrets_path):
    findings = _doctor({"reflex": {"judge": {"provider": "typesafe"}}})
    assert findings and "reflex.judge.provider" in findings[0]
    assert "mnemo rerank --setup" in findings[1]


def test_doctor_is_silent_once_a_key_resolves(monkeypatch, secrets_path):
    monkeypatch.setenv(mcp_rerank.DEFAULT_KEY_ENV, SENTINEL)
    assert _doctor({"reflex": {"judge": {"provider": "typesafe"}}}) is None


def test_doctor_reports_both_stages_at_once(secrets_path):
    findings = _doctor({"recall": {"rerank": {"provider": "typesafe"}},
                        "reflex": {"judge": {"provider": "typesafe"}}})
    assert findings is not None
    joined = "\n".join(findings)
    assert "recall.rerank.provider" in joined and "reflex.judge.provider" in joined
