"""Sync the npm wrapper's version and pin from pyproject.toml.

Single source of truth for mnemo version is pyproject.toml. Two things in the
npm wrapper have to follow it:

1. ``npm/package.json`` ``version`` — the published wrapper version.
2. ``PIN_SPEC`` in ``npm/lib/bootstrap.js`` — the range the wrapper hands to
   ``uv``/``pipx``/``pip`` when installing the Python package.

(2) used to be a manual edit in the release commit. Forgetting it ships a
wrapper that installs the *previous* minor: users get the new npm package and
silently old Python code. Both are regenerated here so the two can't drift.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DIST_NAME = "mnemo-claude"
_PIN_LINE = re.compile(r'^(const PIN_SPEC = ")[^"]*(";)$', re.MULTILINE)


def _read_pyproject_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"Could not find version in {pyproject_path}")
    return m.group(1)


def build_pin_spec(version: str) -> str:
    """Return the ``>=X.Y,<X.Y+1`` range that admits ``version``.

    The patch component is deliberately dropped: 0.16.4 pins to ``>=0.16,<0.17``
    so patch releases reach existing npm users without republishing the wrapper.
    """
    parts = version.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        raise SystemExit(f"Cannot derive a pin from non-numeric version {version!r}")
    major, minor = int(parts[0]), int(parts[1])
    return f"{DIST_NAME}>={major}.{minor},<{major}.{minor + 1}"


def _sync_pin_spec(bootstrap_path: Path, version: str) -> str:
    spec = build_pin_spec(version)
    text = bootstrap_path.read_text()
    new_text, count = _PIN_LINE.subn(lambda m: f"{m.group(1)}{spec}{m.group(2)}", text)
    if count != 1:
        raise SystemExit(
            'Expected exactly one `const PIN_SPEC = "...";` line in '
            f"{bootstrap_path}, found {count}"
        )
    bootstrap_path.write_text(new_text)
    return spec


def sync(repo_root: Path) -> str:
    version = _read_pyproject_version(repo_root / "pyproject.toml")

    npm_pkg = repo_root / "npm" / "package.json"
    data = json.loads(npm_pkg.read_text())
    data["version"] = version
    npm_pkg.write_text(json.dumps(data, indent=2) + "\n")

    _sync_pin_spec(repo_root / "npm" / "lib" / "bootstrap.js", version)
    return version


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    version = sync(repo_root)
    print(f"npm/package.json version → {version}")
    print(f"npm/lib/bootstrap.js PIN_SPEC → {build_pin_spec(version)}")
