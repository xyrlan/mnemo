import json
from pathlib import Path

from tools import sync_npm_version


def test_sync_npm_version_reads_pyproject_and_writes_npm_package_json(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "mnemo"\nversion = "0.12.0"\n')
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    npm_pkg = npm_dir / "package.json"
    npm_pkg.write_text(json.dumps({"name": "mnemo", "version": "0.0.0"}))

    sync_npm_version.sync(repo_root=tmp_path)

    data = json.loads(npm_pkg.read_text())
    assert data["version"] == "0.12.0"
