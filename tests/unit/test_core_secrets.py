"""The machine-local secrets file (#406).

Two promises. It never raises on the read path — that read happens inside an
MCP tool call, where a truncated file must cost the rerank stage its key and
nothing else. And it is written owner-only on the platforms that have file
modes, at creation rather than by a chmod afterwards, so there is no window in
which the key sits in a world-readable file.
"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from mnemo.core import secrets

WINDOWS = sys.platform.startswith("win")


@pytest.fixture
def secrets_path(tmp_path, monkeypatch):
    """The override, not ``HOME``: ``ntpath.expanduser`` never reads ``HOME``."""
    target = tmp_path / "secrets-home" / ".mnemo" / "secrets.json"
    monkeypatch.setenv("MNEMO_SECRETS_PATH", str(target))
    return target


def test_path_follows_the_override(secrets_path):
    assert secrets.path() == secrets_path


def test_path_defaults_under_the_user_dir(monkeypatch):
    monkeypatch.delenv("MNEMO_SECRETS_PATH", raising=False)
    assert secrets.path() == secrets.Path(os.path.expanduser("~/.mnemo/secrets.json"))


def test_write_then_read_round_trips(secrets_path):
    secrets.write("typesafe", "sk-round-trip")
    assert secrets.read("typesafe") == "sk-round-trip"


def test_the_file_is_the_documented_shape(secrets_path):
    secrets.write("typesafe", "sk-shape")
    assert json.loads(secrets_path.read_text(encoding="utf-8")) == {
        "recall.rerank": {"typesafe": "sk-shape"}}


def test_a_second_provider_is_a_second_entry(secrets_path):
    secrets.write("typesafe", "sk-one")
    secrets.write("other", "sk-two")
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert data["recall.rerank"] == {"typesafe": "sk-one", "other": "sk-two"}


def test_writing_keeps_an_unrelated_section(secrets_path):
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text(json.dumps({"something.else": {"a": "b"}}), encoding="utf-8")
    secrets.write("typesafe", "sk-keep")
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert data["something.else"] == {"a": "b"}
    assert data["recall.rerank"]["typesafe"] == "sk-keep"


def test_missing_file_reads_as_no_key(secrets_path):
    assert secrets.read("typesafe") is None


@pytest.mark.parametrize("content", [
    "not json at all",
    "[1, 2, 3]",                                  # JSON, not an object
    '{"recall.rerank": "a string"}',              # section of the wrong type
    '{"recall.rerank": {"typesafe": 12}}',        # key of the wrong type
    '{"recall.rerank": {"typesafe": "   "}}',     # present and empty
    '{"recall.rerank": {}}',                      # section with no provider
    "",
])
def test_an_unusable_file_reads_as_no_key_and_never_raises(secrets_path, content):
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text(content, encoding="utf-8")
    assert secrets.read("typesafe") is None


def test_a_directory_where_the_file_should_be_reads_as_no_key(secrets_path):
    secrets_path.mkdir(parents=True)
    assert secrets.read("typesafe") is None


def test_remove_drops_the_entry_and_the_file(secrets_path):
    secrets.write("typesafe", "sk-gone")
    assert secrets.remove("typesafe") is True
    assert not secrets_path.exists(), "an empty envelope suggests a key that is not there"
    assert secrets.read("typesafe") is None


def test_remove_is_idempotent(secrets_path):
    secrets.write("typesafe", "sk-gone")
    assert secrets.remove("typesafe") is True
    assert secrets.remove("typesafe") is False


def test_remove_keeps_the_other_provider_and_the_file(secrets_path):
    secrets.write("typesafe", "sk-one")
    secrets.write("other", "sk-two")
    assert secrets.remove("typesafe") is True
    assert secrets_path.exists()
    assert secrets.read("other") == "sk-two"


def test_remove_keeps_an_unrelated_section(secrets_path):
    secrets.write("typesafe", "sk-one")
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    data["something.else"] = {"a": "b"}
    secrets_path.write_text(json.dumps(data), encoding="utf-8")
    secrets.remove("typesafe")
    assert json.loads(secrets_path.read_text(encoding="utf-8")) == {"something.else": {"a": "b"}}


@pytest.mark.skipif(WINDOWS, reason="Windows has no POSIX file modes")
def test_the_file_is_owner_only(secrets_path):
    secrets.write("typesafe", "sk-mode")
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(secrets_path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(WINDOWS, reason="Windows has no POSIX file modes")
def test_rewriting_a_loosened_file_tightens_it_again(secrets_path):
    """``os.open`` ignores its mode on a path that already exists.

    Writing in place would leave a file someone had made world-readable
    exactly as it was, so the write goes through a fresh sibling and a rename.
    """
    secrets.write("typesafe", "sk-one")
    os.chmod(str(secrets_path), 0o644)
    secrets.write("typesafe", "sk-two")
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600


def test_no_temp_file_is_left_behind(secrets_path):
    secrets.write("typesafe", "sk-clean")
    assert sorted(p.name for p in secrets_path.parent.iterdir()) == ["secrets.json"]
