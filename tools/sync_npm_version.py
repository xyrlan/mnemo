"""Sync npm/package.json version from pyproject.toml.

Single source of truth for mnemo version is pyproject.toml. The npm
wrapper (`npm/package.json`) is regenerated from it before each
`npm publish` so PyPI and npm versions stay aligned.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _read_pyproject_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"Could not find version in {pyproject_path}")
    return m.group(1)


def sync(repo_root: Path) -> str:
    pyproject = repo_root / "pyproject.toml"
    npm_pkg = repo_root / "npm" / "package.json"
    version = _read_pyproject_version(pyproject)
    data = json.loads(npm_pkg.read_text())
    data["version"] = version
    npm_pkg.write_text(json.dumps(data, indent=2) + "\n")
    return version


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    version = sync(repo_root)
    print(f"npm/package.json version → {version}")
