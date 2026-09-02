from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser


def test_reclassify_registered_as_advanced_command():
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "reclassify" in COMMANDS and "reclassify" in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["reclassify", "--apply"])
    assert ns.command == "reclassify" and ns.apply is True
    ns = _build_parser().parse_args(["reclassify", "--undo", "20260901T000000"])
    assert ns.undo == "20260901T000000"


def test_plan_uses_the_configured_extraction_model_and_timeout(tmp_path, monkeypatch, capsys):
    """The planner reads cfg["extraction"], not the top-level keys that never existed."""
    import argparse

    from mnemo import cli
    from mnemo.cli.commands import reclassify as cmd
    from mnemo.core import reclassify as R

    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_path)
    monkeypatch.setattr(
        cmd, "load_config",
        lambda: {"extraction": {"model": "claude-x", "subprocessTimeout": 7}},
        raising=False,
    )
    monkeypatch.setattr(
        "mnemo.core.config.load_config",
        lambda: {"extraction": {"model": "claude-x", "subprocessTimeout": 7}},
    )

    class _Rule:
        slug = "use-yarn"

    monkeypatch.setattr(R, "collect_rules", lambda vault: [_Rule(), _Rule()])
    monkeypatch.setattr(R, "has_transcript", lambda vault, rule: True)

    seen = {}

    def fake_plan(vault, *, model, timeout, batch_size, limit):
        seen.update(model=model, timeout=timeout, limit=limit)
        return R.Plan(run_id="r", llm_calls=1, verdicts=[])

    monkeypatch.setattr(R, "plan", fake_plan)
    monkeypatch.setattr(R, "save_plan", lambda vault, plan: None)

    args = argparse.Namespace(command="reclassify", apply=False, undo=None, yes=True, limit=1)
    assert cmd.cmd_reclassify(args) == 0
    assert seen["model"] == "claude-x" and seen["timeout"] == 7
    assert "claude-x" in capsys.readouterr().out


def test_plan_printout_shows_the_link_for_keep_verdicts(tmp_path, monkeypatch, capsys):
    """A keep is only reviewable if the maintainer can see why the quote was accepted (#119)."""
    import argparse

    from mnemo import cli
    from mnemo.cli.commands import reclassify as cmd
    from mnemo.core import reclassify as R

    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_path)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"extraction": {}})
    monkeypatch.setattr(R, "collect_rules", lambda vault: [object()])
    monkeypatch.setattr(R, "has_transcript", lambda vault, rule: True)
    monkeypatch.setattr(R, "save_plan", lambda vault, plan: None)
    monkeypatch.setattr(R, "plan", lambda vault, **kw: R.Plan(run_id="r", llm_calls=1, verdicts=[
        R.Verdict(slug="prod-env", verdict="keep", quote="vamo mudar o env do app para prod e subir",
                  source="s", link="user ordered the prod env switch"),
        R.Verdict(slug="clean-code", verdict="demote", reason="no quote"),
    ]))

    args = argparse.Namespace(command="reclassify", apply=False, undo=None, yes=True, limit=None)
    assert cmd.cmd_reclassify(args) == 0
    out = capsys.readouterr().out
    assert 'keep     prod-env · "vamo mudar o env do app para prod e subir"' in out
    assert "link: user ordered the prod env switch" in out
    assert "demote   clean-code · no quote" in out


def test_fmt_keep_without_link_stays_one_line():
    from mnemo.cli.commands.reclassify import _fmt
    from mnemo.core.reclassify import Verdict

    line = _fmt(Verdict(slug="s", verdict="keep", quote="q", source="src"))
    assert "\n" not in line and "link" not in line
