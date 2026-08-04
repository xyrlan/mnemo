"""Ledger: idempotency, resume, re-harvest on change, 3-strike skip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.backfill import ledger


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _transcript(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.jsonl"
    p.write_text(text, encoding="utf-8")
    return p


def test_unseen_transcript_should_harvest(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    assert ledger.should_harvest(led, t) is True


def test_marking_done_makes_it_skip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)
    assert ledger.should_harvest(led, t) is False


def test_survives_a_save_load_roundtrip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)
    ledger.save(vault, led)

    reloaded = ledger.load(vault)
    assert ledger.should_harvest(reloaded, t) is False


def test_changed_transcript_is_reharvested(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)

    t.write_text('{"type":"user"}\n{"type":"assistant"}\n', encoding="utf-8")
    assert ledger.should_harvest(led, t) is True


def test_three_failures_permanently_skip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    for _ in range(2):
        ledger.mark_failed(led, t, "boom")
    assert ledger.should_harvest(led, t) is True

    ledger.mark_failed(led, t, "boom")
    assert ledger.should_harvest(led, t) is False


def test_corrupt_ledger_file_starts_clean(vault, tmp_path):
    (vault / ".mnemo" / "backfill-state.json").write_text("{not json", encoding="utf-8")
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    assert ledger.should_harvest(led, t) is True


def test_save_writes_schema_version(vault, tmp_path):
    led = ledger.load(vault)
    ledger.save(vault, led)
    data = json.loads((vault / ".mnemo" / "backfill-state.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert "sessions" in data


# --- the install-run spawn lock --------------------------------------------
#
# `load -> check installRunDone -> save` is a plain TOCTOU. Two Claude Code
# windows opened in the same instant both read False and both spawn: 2x
# installCap LLM calls on the user's account, and two children racing on
# `save`'s shared temp file. O_CREAT|O_EXCL is the atomic test-and-set that a
# read-modify-write cannot be.

def test_only_one_of_two_racing_sessions_wins_the_lock(vault):
    first = ledger.acquire_spawn_lock(vault)
    second = ledger.acquire_spawn_lock(vault)
    assert [first, second] == [True, False]


def test_concurrent_acquires_elect_exactly_one_winner(vault):
    """The real shape of the race: threads, not sequential calls."""
    import threading

    barrier = threading.Barrier(8)
    wins: list[bool] = []
    lock = threading.Lock()

    def go():
        barrier.wait()
        won = ledger.acquire_spawn_lock(vault)
        with lock:
            wins.append(won)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert wins.count(True) == 1, f"expected exactly one winner, got {wins}"


def test_releasing_lets_the_next_session_take_it(vault):
    assert ledger.acquire_spawn_lock(vault) is True
    ledger.release_spawn_lock(vault)
    assert ledger.acquire_spawn_lock(vault) is True


def test_releasing_a_lock_that_is_not_held_is_harmless(vault):
    ledger.release_spawn_lock(vault)  # must not raise


def test_an_abandoned_lock_is_stolen_after_the_ttl(vault):
    """A hard-killed sweep must not wedge the feature forever."""
    import os
    import time

    assert ledger.acquire_spawn_lock(vault) is True
    path = ledger.spawn_lock_path(vault)
    old = time.time() - ledger.SPAWN_LOCK_TTL_SECONDS - 60
    os.utime(path, (old, old))

    assert ledger.acquire_spawn_lock(vault) is True


def test_a_lock_younger_than_the_ttl_is_believed(vault):
    """A sweep that is merely slow must not have its lock stolen — that is the
    double-spend the lock exists to prevent."""
    import os
    import time

    assert ledger.acquire_spawn_lock(vault) is True
    path = ledger.spawn_lock_path(vault)
    recent = time.time() - ledger.SPAWN_LOCK_TTL_SECONDS + 600
    os.utime(path, (recent, recent))

    assert ledger.acquire_spawn_lock(vault) is False


def test_spawn_lock_age_is_none_without_a_lock(vault):
    assert ledger.spawn_lock_age(vault) is None
    ledger.acquire_spawn_lock(vault)
    age = ledger.spawn_lock_age(vault)
    assert age is not None and age < 60


def test_marking_the_install_run_preserves_the_session_records(vault, tmp_path):
    """The marker shares a file with the sweep that just wrote it."""
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=2)
    ledger.save(vault, led)

    ledger.mark_install_run_done(vault)

    after = ledger.load(vault)
    assert after["installRunDone"] is True
    assert after["sessions"]["sess-a"]["produced"] == 2


def test_a_fresh_ledger_has_no_completed_install_run(vault):
    assert ledger.load(vault)["installRunDone"] is False


def test_a_future_dated_lock_is_still_reaped(vault):
    """A backward clock jump must not wedge the lock forever.

    Signed elapsed time goes negative for a lock dated in the future — an NTP
    correction, a copied vault, a machine that booted with a bad RTC — and a
    negative age is permanently below the TTL, so a lock left behind by a
    killed process could never be stolen and the install backfill would never
    run again on that vault.
    """
    import os
    import time

    assert ledger.acquire_spawn_lock(vault) is True
    path = ledger.spawn_lock_path(vault)
    future = time.time() + ledger.SPAWN_LOCK_TTL_SECONDS + 3600
    os.utime(path, (future, future))

    assert ledger.acquire_spawn_lock(vault) is True


def test_a_slightly_future_dated_lock_is_still_believed(vault):
    """Small skew is not a licence to steal a running sweep's lock."""
    import os
    import time

    assert ledger.acquire_spawn_lock(vault) is True
    path = ledger.spawn_lock_path(vault)
    future = time.time() + 60
    os.utime(path, (future, future))

    assert ledger.acquire_spawn_lock(vault) is False


def test_spawn_lock_age_is_never_negative(vault):
    import os
    import time

    ledger.acquire_spawn_lock(vault)
    future = time.time() + 3600
    os.utime(ledger.spawn_lock_path(vault), (future, future))

    age = ledger.spawn_lock_age(vault)
    assert age is not None and age >= 0
