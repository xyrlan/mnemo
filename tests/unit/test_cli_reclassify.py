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
