"""``mnemo rerank`` — consent, a key that is proved before it is stored, and
the report that ends the silent fallback (#406).

Nothing here reaches the network: ``urlopen`` is wired to fail the test, and
the one place a request would be made takes its client from the rerank module,
which the tests replace. The sentinel key appears in every write path and is
asserted to appear in no output and in no file mnemo writes except the secrets
file it belongs in.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from mnemo.cli.parser import COMMANDS, _build_parser
from mnemo.core import secrets
from mnemo.core.mcp import rerank as mcp_rerank
from mnemo.cli.commands import rerank as cmd

SENTINEL = "sk-live-DO-NOT-PRINT-406"


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


def _answering(value=0.9):
    """A provider that answers the probe the way the real one does."""
    def client(state, questions):
        return {"answers": {key: {"noul": value} for key in questions}}
    return client


def _run(monkeypatch, vault: Path, argv: list) -> tuple[int, str, str]:
    """Parse ``mnemo rerank <argv>`` and run the registered handler."""
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = _build_parser().parse_args(["rerank", *argv])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = COMMANDS["rerank"](args)
    return code, out.getvalue(), err.getvalue()


def _log(vault: Path, rows: list, *, rotated: list | None = None) -> None:
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    def dump(path: Path, entries: list) -> None:
        path.write_text("".join(json.dumps(r) + "\n" for r in entries), encoding="utf-8")
    dump(mnemo_dir / "mcp-access-log.jsonl", rows)
    if rotated is not None:
        dump(mnemo_dir / "mcp-access-log.jsonl.1", rotated)


def _row(status: str, *, ts: str = "2099-01-01T00:00:00Z", judged: int = 3,
         relevant: int = 1) -> dict:
    """One access-log row, shaped as ``apply`` writes it.

    A call that did not reach the judge judged nothing and marked nothing, so
    the defaults only apply to ``ok``.
    """
    if status != "ok":
        judged, relevant = 0, 0
    return {"timestamp": ts, "tool": "list_rules_by_topic", "result_count": 3,
            "rerank": {"provider": "typesafe", "status": status,
                       "judged": judged, "relevant": relevant}}


# ── registration ──────────────────────────────────────────────────────────


def test_the_subcommand_is_reachable():
    import mnemo.cli.commands  # noqa: F401

    assert "rerank" in COMMANDS
    parser = _build_parser()
    assert parser.parse_args(["rerank"]).command == "rerank"


def test_setup_and_off_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        with contextlib.redirect_stderr(io.StringIO()):
            _build_parser().parse_args(["rerank", "--setup", "--off"])


# ── the report ────────────────────────────────────────────────────────────


def test_off_by_default_says_so_and_names_setup(monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert code == 0
    assert "rerank: off" in out
    assert "key: none" in out
    assert "mnemo rerank --setup" in out


def test_the_provider_being_set_with_no_key_is_called_out(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert code == 0
    assert "rerank: on" in out
    assert "no key anywhere" in out


def test_the_env_var_is_named_as_the_source(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    monkeypatch.setenv(mcp_rerank.DEFAULT_KEY_ENV, SENTINEL)
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "key: env %s" % mcp_rerank.DEFAULT_KEY_ENV in out
    assert SENTINEL not in out


def test_the_secrets_file_is_named_as_the_source(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    secrets.write("typesafe", SENTINEL)
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "key: secrets file" in out and str(secrets_path) in out
    assert SENTINEL not in out


def test_the_summary_counts_both_log_files(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    _log(tmp_vault,
         [_row("ok"), _row("error"), _row("ok", relevant=0)],
         rotated=[_row("no_key")])
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "4 calls" in out
    assert "2 ok" in out and "1 no_key" in out and "1 error" in out
    assert "6 rules judged, 1 marked relevant, 1 call marked none" in out


def test_rows_outside_the_window_are_not_counted(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    _log(tmp_vault, [_row("ok"), _row("ok", ts="2001-01-01T00:00:00Z")])
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "1 calls" in out


def test_rows_without_a_rerank_object_are_ignored(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    _log(tmp_vault, [{"timestamp": "2099-01-01T00:00:00Z", "tool": "read_mnemo_rule",
                      "result_count": 1}])
    code, out, _ = _run(monkeypatch, tmp_vault, [])
    assert "no list_rules_by_topic call reached the stage" in out


def test_json_carries_the_source_label_and_never_the_key(monkeypatch, tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    secrets.write("typesafe", SENTINEL)
    _log(tmp_vault, [_row("ok")])
    code, out, _ = _run(monkeypatch, tmp_vault, ["--json"])
    data = json.loads(out)
    assert data["provider"] == "typesafe"
    assert data["keySource"] == "secrets"
    assert data["calls"] == 1 and data["by_status"] == {"ok": 1}
    assert SENTINEL not in out


def test_the_report_is_local(monkeypatch, tmp_vault, config_path, secrets_path):
    """The ``_no_network`` fixture fails the test if this reaches a socket."""
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    secrets.write("typesafe", SENTINEL)
    assert _run(monkeypatch, tmp_vault, [])[0] == 0


# ── --setup ───────────────────────────────────────────────────────────────


def _setup(monkeypatch, vault, *, key=SENTINEL, client=None, argv=("--setup", "--yes", "--key-stdin")):
    monkeypatch.setattr("sys.stdin", io.StringIO(key))
    if client is not None:
        monkeypatch.setattr(mcp_rerank, "typesafe_client",
                            lambda k, **kw: (lambda s, q: client(s, q)))
    return _run(monkeypatch, vault, list(argv))


def test_setup_prints_what_it_sends_before_asking(monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, _ = _setup(monkeypatch, tmp_vault, client=_answering())
    flat = re.sub(r"\s+", " ", out)
    assert re.sub(r"\s+", " ", cmd.WHAT_IT_SENDS) in flat
    assert flat.index("api.typesafe.ai") < flat.index("Checking the key")


def test_setup_writes_the_key_and_the_provider(monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, err = _setup(monkeypatch, tmp_vault, client=_answering())
    assert code == 0, err
    assert secrets.read("typesafe") == SENTINEL
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "recall": {"rerank": {"provider": "typesafe"}}}


def test_setup_keeps_the_rest_of_the_config(monkeypatch, tmp_vault, config_path, secrets_path):
    """#303: writing ``load_config()`` back would freeze every default in."""
    config_path.write_text(json.dumps({"vaultRoot": "~/elsewhere"}), encoding="utf-8")
    code, _, err = _setup(monkeypatch, tmp_vault, client=_answering())
    assert code == 0, err
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["vaultRoot"] == "~/elsewhere"
    assert len(written) == 2, "the file the user has, plus one key — not the merged DEFAULTS"


def test_setup_never_shows_the_key(monkeypatch, tmp_vault, config_path, secrets_path):
    code, out, err = _setup(monkeypatch, tmp_vault, client=_answering())
    assert code == 0
    assert SENTINEL not in out and SENTINEL not in err
    assert SENTINEL not in config_path.read_text(encoding="utf-8")
    # The one file it is allowed to be in, and nowhere else mnemo wrote.
    assert SENTINEL in secrets_path.read_text(encoding="utf-8")


def test_a_failed_probe_writes_nothing(monkeypatch, tmp_vault, config_path, secrets_path):
    import urllib.error

    def refuse(state, questions):
        raise urllib.error.HTTPError("https://api.typesafe.ai", 401, "Unauthorized", {}, None)

    code, out, err = _setup(monkeypatch, tmp_vault, client=refuse)
    assert code == 1
    assert "401" in err and "rejected the key" in err
    assert not secrets_path.exists()
    assert not config_path.exists()
    assert SENTINEL not in out and SENTINEL not in err


def test_an_answer_with_no_score_writes_nothing(monkeypatch, tmp_vault, config_path, secrets_path):
    code, _, err = _setup(monkeypatch, tmp_vault, client=lambda s, q: {"answers": {"r0": {}}})
    assert code == 1
    assert "no score" in err
    assert not secrets_path.exists() and not config_path.exists()


def test_a_foreign_failure_reports_only_its_type(monkeypatch, tmp_vault, config_path, secrets_path):
    """A message mnemo did not write is never formatted into output."""
    def blow_up(state, questions):
        raise RuntimeError("Authorization: Bearer " + SENTINEL)

    code, out, err = _setup(monkeypatch, tmp_vault, client=blow_up)
    assert code == 1
    assert "RuntimeError" in err
    assert SENTINEL not in out and SENTINEL not in err


def test_the_probe_sends_no_vault_content(monkeypatch, tmp_vault, config_path, secrets_path):
    seen = {}

    def record(state, questions):
        seen["state"] = state
        seen["questions"] = questions
        return _answering()(state, questions)

    _setup(monkeypatch, tmp_vault, client=record)
    assert seen["state"] == {"developer_task": cmd.PROBE_TASK}
    assert len(seen["questions"]) == 1
    assert cmd.PROBE_RULE in json.dumps(seen["questions"])


def test_setup_refuses_off_a_tty_without_yes(monkeypatch, tmp_vault, config_path, secrets_path):
    code, _, err = _setup(monkeypatch, tmp_vault, client=_answering(),
                          argv=("--setup", "--key-stdin"))
    assert code == 2
    assert "without a tty" in err
    assert not secrets_path.exists() and not config_path.exists()


def test_setup_stops_on_a_declined_prompt(monkeypatch, tmp_vault, config_path, secrets_path):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    code, out, _ = _run(monkeypatch, tmp_vault, ["--setup"])
    assert code == 1
    assert "Nothing written." in out
    assert not secrets_path.exists() and not config_path.exists()


def test_an_empty_key_writes_nothing(monkeypatch, tmp_vault, config_path, secrets_path):
    code, _, err = _setup(monkeypatch, tmp_vault, key="   \n", client=_answering())
    assert code == 2
    assert not secrets_path.exists() and not config_path.exists()


def test_the_key_cannot_be_passed_as_an_argument():
    """It would land in shell history and in ``ps``."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        with contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["rerank", "--setup", "--key", SENTINEL])


# ── --off ─────────────────────────────────────────────────────────────────


def test_off_reverses_both_halves(monkeypatch, tmp_vault, config_path, secrets_path):
    _setup(monkeypatch, tmp_vault, client=_answering())
    code, out, _ = _run(monkeypatch, tmp_vault, ["--off"])
    assert code == 0
    assert json.loads(config_path.read_text(encoding="utf-8"))["recall"]["rerank"]["provider"] == "none"
    assert not secrets_path.exists()
    assert "Removed the stored key" in out


def test_off_is_idempotent(monkeypatch, tmp_vault, config_path, secrets_path):
    _setup(monkeypatch, tmp_vault, client=_answering())
    _run(monkeypatch, tmp_vault, ["--off"])
    code, out, _ = _run(monkeypatch, tmp_vault, ["--off"])
    assert code == 0
    assert "No stored key to remove." in out


def test_off_says_the_env_var_is_still_set(monkeypatch, tmp_vault, config_path, secrets_path):
    monkeypatch.setenv(mcp_rerank.DEFAULT_KEY_ENV, SENTINEL)
    code, out, _ = _run(monkeypatch, tmp_vault, ["--off"])
    assert mcp_rerank.DEFAULT_KEY_ENV in out
    assert SENTINEL not in out


# ── the consent text and the docs cannot drift ────────────────────────────


def test_what_setup_prints_is_what_the_docs_promise():
    """The paragraph is quoted from ``docs/configuration.md``, not paraphrased.

    Consent that says less than the documentation, or more, is the failure
    this pins: whoever edits one is made to edit the other.
    """
    repo = Path(__file__).resolve().parents[2]
    doc = (repo / "docs" / "configuration.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", doc).replace("**", "")
    assert re.sub(r"\s+", " ", cmd.WHAT_IT_SENDS) in flat


# ── doctor and status make the silent fallback visible ────────────────────


def _doctor(vault: Path) -> tuple[bool, str]:
    from mnemo.cli.commands.doctor_checks import rerank as check

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = check._doctor_check_rerank(vault)
    return ok, buf.getvalue()


def test_doctor_is_silent_while_the_stage_is_off(tmp_vault, config_path, secrets_path):
    ok, out = _doctor(tmp_vault)
    assert ok and out == ""


def test_doctor_is_silent_when_a_key_resolves(tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    secrets.write("typesafe", SENTINEL)
    ok, out = _doctor(tmp_vault)
    assert ok and out == ""


def test_doctor_fails_when_the_provider_is_set_and_no_key_resolves(tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    ok, out = _doctor(tmp_vault)
    assert ok is False
    assert "falling back" in out
    assert "mnemo rerank --setup" in out
    assert SENTINEL not in out


def test_doctor_is_registered_as_a_row():
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert "rerank" in [name for name, _fn in DOCTOR_CHECKS]


def test_doctor_never_raises(monkeypatch, tmp_vault):
    """A diagnostic that throws is worse than one that says nothing."""
    from mnemo.cli.commands.doctor_checks import rerank as check

    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: 1 / 0)
    assert check._doctor_check_rerank(tmp_vault) is True


def _status(vault: Path) -> str:
    from mnemo.cli.commands import status as status_cmd

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        status_cmd._print_rerank_status(vault)
    return buf.getvalue()


def test_status_says_nothing_while_the_stage_is_off(tmp_vault, config_path, secrets_path):
    assert _status(tmp_vault) == ""


def test_status_carries_the_summary_in_one_line(tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    secrets.write("typesafe", SENTINEL)
    _log(tmp_vault, [_row("ok"), _row("ok"), _row("error")])
    out = _status(tmp_vault)
    assert "rerank: 3 calls in 14d, 2 ok, 1 error" in out
    assert "key from secrets" in out
    assert SENTINEL not in out


def test_status_spells_out_a_majority_of_no_key(tmp_vault, config_path, secrets_path):
    config_path.write_text(json.dumps({"recall": {"rerank": {"provider": "typesafe"}}}), encoding="utf-8")
    _log(tmp_vault, [_row("no_key"), _row("no_key"), _row("ok")])
    out = _status(tmp_vault)
    assert "NO KEY on 2 of 3 calls" in out
    assert "mnemo rerank --setup" in out
