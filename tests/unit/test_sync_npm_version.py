import json
from pathlib import Path

import pytest

from tools import sync_npm_version


def _repo(tmp_path: Path, *, pyproject_version: str, pin_spec: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "mnemo-claude"\nversion = "{pyproject_version}"\n'
    )
    lib = tmp_path / "npm" / "lib"
    lib.mkdir(parents=True)
    (tmp_path / "npm" / "package.json").write_text(
        json.dumps({"name": "@xyrlan/mnemo", "version": "0.0.0"})
    )
    (lib / "bootstrap.js").write_text(
        '"use strict";\n\n'
        f'const PIN_SPEC = "{pin_spec}";\n\n'
        "function buildInstallCmd(installer, spec = PIN_SPEC) {}\n"
    )
    return tmp_path


def test_sync_npm_version_reads_pyproject_and_writes_npm_package_json(tmp_path: Path):
    repo = _repo(tmp_path, pyproject_version="0.12.0", pin_spec="mnemo-claude>=0.11,<0.12")

    assert sync_npm_version.sync(repo_root=repo) == "0.12.0"

    data = json.loads((repo / "npm" / "package.json").read_text())
    assert data["version"] == "0.12.0"


def test_sync_rewrites_pin_spec_to_the_matching_minor_range(tmp_path: Path):
    repo = _repo(tmp_path, pyproject_version="0.16.0", pin_spec="mnemo-claude>=0.15,<0.16")

    sync_npm_version.sync(repo_root=repo)

    body = (repo / "npm" / "lib" / "bootstrap.js").read_text()
    assert 'const PIN_SPEC = "mnemo-claude>=0.16,<0.17";' in body


def test_sync_rolls_the_pin_over_a_major_boundary(tmp_path: Path):
    repo = _repo(tmp_path, pyproject_version="1.0.0", pin_spec="mnemo-claude>=0.16,<0.17")

    sync_npm_version.sync(repo_root=repo)

    body = (repo / "npm" / "lib" / "bootstrap.js").read_text()
    assert 'const PIN_SPEC = "mnemo-claude>=1.0,<1.1";' in body


def test_sync_pins_to_the_minor_ignoring_the_patch_component(tmp_path: Path):
    repo = _repo(tmp_path, pyproject_version="0.16.4", pin_spec="mnemo-claude>=0.16,<0.17")

    sync_npm_version.sync(repo_root=repo)

    body = (repo / "npm" / "lib" / "bootstrap.js").read_text()
    assert 'const PIN_SPEC = "mnemo-claude>=0.16,<0.17";' in body


def test_sync_fails_loudly_when_pin_spec_is_missing(tmp_path: Path):
    repo = _repo(tmp_path, pyproject_version="0.16.0", pin_spec="unused")
    (repo / "npm" / "lib" / "bootstrap.js").write_text('"use strict";\nconst OTHER = 1;\n')

    with pytest.raises(SystemExit, match="PIN_SPEC"):
        sync_npm_version.sync(repo_root=repo)
