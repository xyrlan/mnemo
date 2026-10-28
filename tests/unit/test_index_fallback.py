"""An unreadable index falls back to the version before it (#767).

A BSOD on 2026-10-18 left ``reflex-index.json`` NUL-filled behind a
completed ``os.replace`` (NTFS kept the rename and lost the bytes, as #735
saw for ``sync-state.json``), and ``rule-activation-index.json`` both empty
and cut off mid-write. Every prompt that read it before the next rebuild was
silenced as ``index_missing``. The writers now flush before the rename and
keep the replaced version as ``<name>.prev``; the loaders read it when the
live file is there but unreadable.
"""
from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.core import rule_activation
from mnemo.core.atomic import atomic_write_bytes, previous_path
from mnemo.core.reflex import index as reflex_index

# What a crash leaves: an empty file, a NUL-filled one of the new size, and
# one cut off part-way through.
_DAMAGE = {
    "empty": lambda good: b"",
    "nul": lambda good: b"\x00" * len(good),
    "truncated": lambda good: good[: len(good) // 2],
}


def _seed(vault: Path, project: str = "myproject") -> None:
    feedback = vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / "use-prisma-mock.md").write_text(
        "---\nname: use-prisma-mock\n"
        "description: Always use jest-mock-extended to mock Prisma in tests\n"
        "tags:\n  - prisma\n  - testing\n"
        f"sources:\n  - bots/{project}/memory/mock.md\n"
        "stability: stable\n---\n"
        "Mock the Prisma client in tests using jest-mock-extended.\n",
        encoding="utf-8",
    )
    for i, (slug, desc) in enumerate([
        ("use-yarn", "Prefer yarn over npm for installs"),
        ("commit-strategy", "Small atomic commits with clear messages"),
        ("review-etiquette", "Be kind and specific in code reviews"),
        ("python-style", "Follow PEP8 and black formatting"),
    ]):
        (feedback / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: {desc}\n"
            f"sources:\n  - bots/noise{i}/memory/x.md\n"
            "stability: stable\n---\nBody.\n",
            encoding="utf-8",
        )


def _damage(path: Path, how: str) -> None:
    path.write_bytes(_DAMAGE[how](path.read_bytes()))


def _errors(vault: Path) -> list:
    log = vault / ".errors.log"
    if not log.exists():
        return []
    return [json.loads(line)["where"]
            for line in log.read_text(encoding="utf-8").splitlines() if line]


# --- the writer -------------------------------------------------------------


def test_keep_previous_keeps_the_replaced_version(tmp_path):
    target = tmp_path / "idx.json"
    atomic_write_bytes(target, b'{"v": 1}', keep_previous=True)
    assert not previous_path(target).exists(), "nothing was replaced yet"

    atomic_write_bytes(target, b'{"v": 2}', keep_previous=True)
    assert target.read_bytes() == b'{"v": 2}'
    assert previous_path(target).read_bytes() == b'{"v": 1}'


@pytest.mark.parametrize("how", ["empty", "nul"])
def test_a_crashed_file_is_not_kept_over_the_last_good_one(tmp_path, how):
    target = tmp_path / "idx.json"
    atomic_write_bytes(target, b'{"v": 1}', keep_previous=True)
    atomic_write_bytes(target, b'{"v": 2}', keep_previous=True)
    _damage(target, how)

    atomic_write_bytes(target, b'{"v": 3}', keep_previous=True)
    assert previous_path(target).read_bytes() == b'{"v": 1}'


def test_without_keep_previous_nothing_is_kept(tmp_path):
    target = tmp_path / "idx.json"
    atomic_write_bytes(target, b"1")
    atomic_write_bytes(target, b"2")
    assert not previous_path(target).exists()


def test_a_filesystem_without_hard_links_still_writes(tmp_path, monkeypatch):
    target = tmp_path / "d" / "idx.json"
    atomic_write_bytes(target, b"1", keep_previous=True)

    def no_links(*_a, **_k):
        raise OSError("hard links not supported")

    monkeypatch.setattr(os, "link", no_links)
    atomic_write_bytes(target, b"2", keep_previous=True)
    assert target.read_bytes() == b"2"
    assert not previous_path(target).exists()
    assert [p.name for p in target.parent.iterdir()] == ["idx.json"], "no staged link left"


def test_both_indexes_are_written_durably_keeping_the_previous(tmp_vault, monkeypatch):
    from mnemo.core import atomic

    seen = []
    real = atomic.atomic_write_bytes

    def spy(path, data, **kw):
        seen.append((Path(path).name, kw))
        real(path, data, **kw)

    monkeypatch.setattr(atomic, "atomic_write_bytes", spy)
    _seed(tmp_vault)
    rule_activation.write_index(tmp_vault, rule_activation.build_index(tmp_vault))
    reflex_index.write_index(tmp_vault, reflex_index.build_index(tmp_vault))
    assert seen == [
        (rule_activation.INDEX_FILENAME, {"durable": True, "keep_previous": True}),
        (reflex_index.INDEX_FILENAME, {"durable": True, "keep_previous": True}),
    ]


# --- the readers ------------------------------------------------------------


_INDEXES = [
    pytest.param(reflex_index, "reflex.index.load.parse", id="reflex"),
    pytest.param(rule_activation, "rule_activation.load_index.parse", id="rule_activation"),
]


@pytest.mark.parametrize("mod, where", _INDEXES)
@pytest.mark.parametrize("how", sorted(_DAMAGE))
def test_a_damaged_index_falls_back_to_the_previous(tmp_vault, mod, where, how):
    _seed(tmp_vault)
    first = mod.build_index(tmp_vault)
    mod.write_index(tmp_vault, first)
    mod.write_index(tmp_vault, dict(first, rebuilt=True))
    _damage(tmp_vault / ".mnemo" / mod.INDEX_FILENAME, how)

    loaded = mod.load_index(tmp_vault)
    assert loaded is not None
    assert "rebuilt" not in loaded, "the version before the damaged one"
    assert loaded["rules" if mod is rule_activation else "docs"]
    assert _errors(tmp_vault) == [where], "the damage is still reported"


@pytest.mark.parametrize("mod, where", _INDEXES)
def test_a_damaged_index_with_no_previous_is_none(tmp_vault, mod, where):
    _seed(tmp_vault)
    mod.write_index(tmp_vault, mod.build_index(tmp_vault))
    _damage(tmp_vault / ".mnemo" / mod.INDEX_FILENAME, "nul")

    assert mod.load_index(tmp_vault) is None


@pytest.mark.parametrize("mod, where", _INDEXES)
def test_a_removed_index_does_not_fall_back(tmp_vault, mod, where):
    """A missing file is not damage: nothing to report, nothing to recover."""
    _seed(tmp_vault)
    mod.write_index(tmp_vault, mod.build_index(tmp_vault))
    mod.write_index(tmp_vault, mod.build_index(tmp_vault))
    (tmp_vault / ".mnemo" / mod.INDEX_FILENAME).unlink()

    assert mod.load_index(tmp_vault) is None
    assert _errors(tmp_vault) == []


def test_readers_during_rebuilds_always_get_an_index(tmp_vault):
    """Concurrent read: a reader racing a writer gets the new index or the
    old one, never nothing. On Windows the read can lose to the rename with
    a sharing violation, which the fallback covers."""
    _seed(tmp_vault)
    index = reflex_index.build_index(tmp_vault)
    reflex_index.write_index(tmp_vault, index)
    reflex_index.write_index(tmp_vault, index)

    misses = []
    done = threading.Event()

    def read():
        while not done.is_set():
            if reflex_index.load_index(tmp_vault) is None:
                misses.append(1)

    readers = [threading.Thread(target=read) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for _ in range(50):
            reflex_index.write_index(tmp_vault, index)
    finally:
        done.set()
        for t in readers:
            t.join()
    assert misses == []


# --- the prompt -------------------------------------------------------------


def _enable_reflex(vault: Path, monkeypatch) -> None:
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    (vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(vault),
        "reflex": {"enabled": True},
    }), encoding="utf-8")


def test_a_prompt_against_a_zeroed_index_still_gets_its_rule(tmp_path, tmp_vault, monkeypatch):
    from mnemo.hooks import user_prompt_submit

    repo = tmp_path / "myproject"
    (repo / ".git").mkdir(parents=True)
    _seed(tmp_vault)
    index = reflex_index.build_index(tmp_vault)
    reflex_index.write_index(tmp_vault, index)
    reflex_index.write_index(tmp_vault, index)
    _damage(tmp_vault / ".mnemo" / reflex_index.INDEX_FILENAME, "nul")
    _enable_reflex(tmp_vault, monkeypatch)

    out = io.StringIO()
    payload = {"cwd": str(repo), "session_id": "s",
               "prompt": "How do I mock prisma in a jest test with typescript"}
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", out):
        assert user_prompt_submit.main() == 0
    assert "[[use-prisma-mock]]" in out.getvalue()
