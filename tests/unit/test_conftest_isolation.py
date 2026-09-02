"""#117: every test runs under a temp HOME; the real vault is never touched."""
from __future__ import annotations

import os
from pathlib import Path

from mnemo.core import config, paths

pytest_plugins = ["pytester"]


def _under(path: Path, root: Path) -> bool:
    """``Path.is_relative_to`` is 3.9+; the suite still runs on 3.8."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def test_home_is_isolated_by_default(real_home):
    home = Path(os.environ["HOME"])
    assert home != real_home
    assert Path.home() == home
    assert os.environ["USERPROFILE"] == str(home)
    assert _under(Path(os.environ["MNEMO_CONFIG_PATH"]), home)


def test_default_vault_root_is_under_tmp_home(real_home):
    cfg = config.load_config()
    vault = Path(paths.vault_root(cfg))
    assert _under(vault, Path.home())
    # On Windows (and some CI images) pytest's tmp dir itself lives under the
    # real home, so "not under real_home" would be false there. The property
    # that matters is that the default vault is not the developer's vault.
    assert vault.resolve() != (real_home / "mnemo").resolve()


def test_tests_that_set_their_own_config_path_keep_it(monkeypatch, tmp_path):
    own = tmp_path / "own.config.json"
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(own))
    assert config.default_config_path() == own


def test_guard_fails_a_test_that_writes_to_the_real_vault(pytester):
    """Run the suite's conftest in a throwaway rootdir and touch its "real" vault.

    ``pytester`` sets HOME to its own directory before the inner conftest is
    imported, so the inner guard's ``_REAL_VAULT`` is ``<pytester.path>/mnemo``
    -- a path this outer test may freely write to.
    """
    conftest_src = (Path(__file__).parent.parent / "conftest.py").read_text(encoding="utf-8")
    pytester.makeconftest(conftest_src)
    guarded = pytester.path / "mnemo" / ".errors.log"
    guarded.parent.mkdir(parents=True)
    guarded.write_text("", encoding="utf-8")
    pytester.makepyfile(
        test_touch=f"""
        from pathlib import Path

        def test_appends_to_the_real_errors_log():
            with open({str(guarded)!r}, "a", encoding="utf-8") as fh:
                fh.write("boom\\n")

        def test_leaves_the_real_vault_alone():
            assert Path({str(guarded)!r}).read_text(encoding="utf-8") == "boom\\n"
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-q")
    # The guard fires in fixture teardown, which pytest reports as an ERROR on
    # the offending test (its body already passed); the sibling stays green.
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*test touched the real vault*"])
