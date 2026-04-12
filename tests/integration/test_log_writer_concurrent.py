"""Critical test from spec § 10.3: 50 threads × 20 lines, no corruption."""
from __future__ import annotations

import re
import threading
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import log_writer


def test_concurrent_log_writes(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    threads = []
    n_threads = 50
    n_lines = 20

    def worker(tid: int) -> None:
        for i in range(n_lines):
            log_writer.append_line("agent", f"thread-{tid}-line-{i:02d}", cfg)

    for tid in range(n_threads):
        threads.append(threading.Thread(target=worker, args=(tid,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log_path = tmp_vault / "bots" / "agent" / "logs" / f"{date.today().isoformat()}.md"
    content = log_path.read_text()
    expected_lines = n_threads * n_lines
    matches = re.findall(r"thread-(\d+)-line-(\d+)", content)
    assert len(matches) == expected_lines, (
        f"expected {expected_lines} log entries, got {len(matches)}"
    )
    seen = {(int(t), int(l)) for t, l in matches}
    assert len(seen) == expected_lines, "lost or duplicated log entries"
    # Header must appear exactly once.
    assert content.count("tags: [log, agent]") == 1
