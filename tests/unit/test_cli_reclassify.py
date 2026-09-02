from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser


def test_reclassify_registered_as_advanced_command():
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "reclassify" in COMMANDS and "reclassify" in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["reclassify", "--apply"])
    assert ns.command == "reclassify" and ns.apply is True
    ns = _build_parser().parse_args(["reclassify", "--undo", "20260901T000000"])
    assert ns.undo == "20260901T000000"
