"""Doctor check surfaces a second, differently-versioned mnemo install.

Regression for #229: a plugin install at 1.3.3 ran a stale copy of every hook
alongside the fixed direct install, and nothing on the machine said so.
"""
from __future__ import annotations

import json
from pathlib import Path

import mnemo
from mnemo.cli.commands.doctor_checks import duplicate_install as check_mod
from mnemo.install import duplicate_install as dup

PLUGIN_KEY = "mnemo@mnemo-marketplace"
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd"]


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_plugin(
    claude_dir: Path,
    *,
    version: str = "1.3.3",
    enabled: bool = True,
    with_hooks: bool = True,
    key: str = PLUGIN_KEY,
) -> Path:
    """Create a plugin cache dir + registry entry + enabledPlugins flag."""
    marketplace = key.split("@", 1)[1]
    plugin = key.split("@", 1)[0]
    install_path = claude_dir / "plugins" / "cache" / marketplace / plugin / version
    install_path.mkdir(parents=True, exist_ok=True)
    if with_hooks:
        _write(
            install_path / "hooks" / "hooks.json",
            {"hooks": {e: [{"hooks": [{"type": "command", "command": "x"}]}] for e in HOOK_EVENTS}},
        )
    _write(
        claude_dir / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                key: [{
                    "scope": "user",
                    "installPath": str(install_path),
                    "version": version,
                }]
            },
        },
    )
    _write(claude_dir / "settings.json", {"enabledPlugins": {key: True} if enabled else {}})
    return install_path


def test_finds_enabled_plugin_with_hooks(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3")

    installs = dup.find_duplicate_installs(claude)
    assert len(installs) == 1
    install = installs[0]
    assert install.plugin_key == PLUGIN_KEY
    assert install.version == "1.3.3"
    assert install.enabled is True
    assert install.runs_hooks is True
    assert set(install.hook_events) == set(HOOK_EVENTS)


def test_silent_when_no_plugin_installed(tmp_path: Path) -> None:
    """The common case, and the CI case: no plugin registry at all."""
    claude = tmp_path / ".claude"
    claude.mkdir(parents=True)
    assert dup.find_duplicate_installs(claude) == []
    assert check_mod.check_duplicate_install(claude) is None


def test_stale_cache_dir_alone_is_not_a_finding(tmp_path: Path) -> None:
    """#229's real trap: `claude plugin uninstall` leaves the cache dir behind.

    The 1.3.3 tree — hooks.json and all — survives on disk after removal. A
    check that scanned the cache would keep warning about a plugin that no
    longer runs. Only the registry says what is installed.
    """
    claude = tmp_path / ".claude"
    install_path = claude / "plugins" / "cache" / "mnemo-marketplace" / "mnemo" / "1.3.3"
    _write(
        install_path / "hooks" / "hooks.json",
        {"hooks": {e: [] for e in HOOK_EVENTS}},
    )
    # Registry cleaned by the uninstall; marketplace entry may linger.
    _write(claude / "plugins" / "installed_plugins.json", {"version": 2, "plugins": {}})
    _write(claude / "settings.json", {"extraKnownMarketplaces": {"mnemo-marketplace": {}}})

    assert dup.find_duplicate_installs(claude) == []
    assert check_mod.check_duplicate_install(claude) is None


def test_disabled_plugin_does_not_run_hooks(tmp_path: Path) -> None:
    """Registered but absent from enabledPlugins → `✘ disabled`, fires nothing."""
    claude = tmp_path / ".claude"
    _seed_plugin(claude, enabled=False)

    installs = dup.find_duplicate_installs(claude)
    assert len(installs) == 1
    assert installs[0].enabled is False
    assert installs[0].runs_hooks is False
    assert dup.version_skew(installs, "1.4.1") == []


def test_version_skew_only_counts_hook_running_installs(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3")
    installs = dup.find_duplicate_installs(claude)

    assert dup.version_skew(installs, "1.4.1") == installs
    # Same version: still a duplicate, but no stale-code risk.
    assert dup.version_skew(installs, "1.3.3") == []


def test_matching_version_reports_duplication_without_skew_line(tmp_path: Path) -> None:
    # Seeded at the *running* version, read rather than written literally: a
    # hardcoded one means "same version" only until the next release bumps
    # pyproject, and then this test fails on the release commit for a reason
    # that has nothing to do with the check. It did exactly that on 1.5.0.
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version=mnemo.__version__)
    findings = check_mod.check_duplicate_install(claude)

    assert findings is not None
    blob = "\n".join(findings)
    assert "runs both" in blob
    assert "version skew" not in blob


def test_findings_name_version_and_remediation(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3")
    findings = check_mod.check_duplicate_install(claude)

    assert findings is not None
    blob = "\n".join(findings)
    assert "1.3.3" in blob
    assert "version skew" in blob
    assert "claude plugin uninstall mnemo@mnemo-marketplace" in blob


def test_ignores_unrelated_plugin_whose_key_merely_contains_mnemo(tmp_path: Path) -> None:
    """`mnemo-helper@x` is a different plugin; a substring match would claim it."""
    claude = tmp_path / ".claude"
    _seed_plugin(claude, key="mnemo-helper@some-marketplace")
    assert dup.find_duplicate_installs(claude) == []


def test_malformed_registry_is_silent(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    (claude / "plugins").mkdir(parents=True)
    (claude / "plugins" / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    assert dup.find_duplicate_installs(claude) == []


def test_registry_entry_without_hooks_json_is_not_hook_running(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    _seed_plugin(claude, with_hooks=False)
    installs = dup.find_duplicate_installs(claude)
    assert installs[0].hook_events == ()
    assert installs[0].runs_hooks is False


def test_doctor_adapter_returns_false_and_prints(tmp_path: Path, capsys) -> None:
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3")

    ok = check_mod._doctor_check_duplicate_install(tmp_path / "vault", claude)
    assert ok is False
    out = capsys.readouterr().out
    assert "duplicate mnemo install" in out
    assert "1.3.3" in out


def test_doctor_adapter_silent_without_plugin(tmp_path: Path, capsys) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir(parents=True)

    ok = check_mod._doctor_check_duplicate_install(tmp_path / "vault", claude)
    assert ok is True
    assert capsys.readouterr().out == ""


def test_adapter_defaults_to_the_real_machine_scope(tmp_path: Path, monkeypatch) -> None:
    """The registry calls the adapter with one argument, so the default must hold.

    Asserting on the forwarded value rather than on a patched ``HOME``: the
    production default goes through ``os.path.expanduser``, which reads
    ``USERPROFILE`` on Windows and ignores ``HOME`` entirely.
    """
    seen: list[object] = []
    monkeypatch.setattr(check_mod, "check_duplicate_install", lambda cd=None: seen.append(cd))

    check_mod._doctor_check_duplicate_install(tmp_path / "vault")
    assert seen == [None]  # None → find_duplicate_installs resolves ~/.claude


def test_project_scoped_enablement_counts(tmp_path: Path) -> None:
    """A project- or local-scoped plugin is enabled in the *project's* settings.

    The registry records scope values of user/project/local, and real machines
    carry `enabledPlugins` in a project `.claude/settings.json` as well as in
    `~/.claude/settings.json`. Reading only the global file reported such a
    plugin as disabled, understating the finding.
    """
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3", enabled=False)  # not enabled globally
    project = tmp_path / "proj"
    _write(project / ".claude" / "settings.json", {"enabledPlugins": {PLUGIN_KEY: True}})

    installs = dup.find_duplicate_installs(claude, cwd=project)
    assert installs[0].enabled is True
    assert installs[0].runs_hooks is True


def test_project_settings_local_also_counts(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3", enabled=False)
    project = tmp_path / "proj"
    _write(
        project / ".claude" / "settings.local.json",
        {"enabledPlugins": {PLUGIN_KEY: True}},
    )

    assert dup.find_duplicate_installs(claude, cwd=project)[0].enabled is True


def test_project_settings_can_disable_a_globally_enabled_plugin(tmp_path: Path) -> None:
    """The more specific scope wins."""
    claude = tmp_path / ".claude"
    _seed_plugin(claude, version="1.3.3", enabled=True)
    project = tmp_path / "proj"
    _write(project / ".claude" / "settings.json", {"enabledPlugins": {PLUGIN_KEY: False}})

    assert dup.find_duplicate_installs(claude, cwd=project)[0].enabled is False


def test_check_never_raises_into_doctor(tmp_path: Path, monkeypatch) -> None:
    """A diagnostic must not be the thing that breaks `mnemo doctor`."""
    monkeypatch.setattr(
        dup, "find_duplicate_installs",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("registry shape changed")),
    )
    assert check_mod.check_duplicate_install(tmp_path) is None
    assert check_mod._doctor_check_duplicate_install(tmp_path) is True


def test_status_survives_a_raising_registry(tmp_path: Path, monkeypatch) -> None:
    """status gained a dependency on the registry; it must still print.

    Falling back to "no plugin" routes to the settings.json scope lines, which
    is the honest answer when the registry cannot be read.
    """
    from mnemo.cli.commands import status as status_mod

    monkeypatch.setattr(
        dup, "find_duplicate_installs",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert status_mod._installed_plugin_root() is None


def test_hostile_registry_shapes_do_not_crash(tmp_path: Path) -> None:
    """Non-dict records and wrong-typed fields are written by another tool."""
    claude = tmp_path / ".claude"
    _write(claude / "plugins" / "installed_plugins.json", {
        "version": 2,
        "plugins": {
            "mnemo@a": ["not-a-dict"],
            "mnemo@b": [{"installPath": 123, "version": []}],
            "mnemo@c": [],
        },
    })
    _write(claude / "settings.json", {"enabledPlugins": "not-a-dict"})

    installs = dup.find_duplicate_installs(claude)
    assert [i.plugin_key for i in installs] == ["mnemo@b"]
    assert installs[0].version is None and installs[0].install_path is None
    assert installs[0].runs_hooks is False


def test_registered_in_doctor_registry() -> None:
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    names = [name for name, _ in DOCTOR_CHECKS]
    assert "duplicate_install" in names
