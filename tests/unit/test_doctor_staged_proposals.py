"""``mnemo doctor`` surfaces the ``_inbox`` proposal backlog (#159).

After #156 relocated the strays, ``shared/_inbox/`` held 33 ``.proposed.md``
rewrites nobody had looked at. Nothing reported they existed. Step (1) of
#159: doctor counts them, names the oldest, and says where they are.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from mnemo.cli.commands.doctor_checks import rules as doctor_rules


def _page(vault: Path, rel: str, *, age_days: float = 0.0) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nname: n\ntype: project\n---\n\nbody\n", encoding="utf-8")
    if age_days:
        ts = time.time() - age_days * 86400
        os.utime(p, (ts, ts))
    return p


def test_quiet_when_inbox_has_no_proposals(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/project/a__x.md")
    # A plain staged page is not a proposal; it has its own review path.
    _page(tmp_path, "shared/_inbox/reference/b__y.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True
    assert "no staged rewrites awaiting review" in capsys.readouterr().out


def test_counts_proposals_across_inbox_types(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")
    _page(tmp_path, "shared/_inbox/project/a__y.update-proposed.md")
    _page(tmp_path, "shared/_inbox/feedback/a__z.proposed.md")
    # Strays beside live rules belong to ``stray_proposed``, not here.
    _page(tmp_path, "shared/project/a__w.proposed.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "3 staged rewrites awaiting review in shared/_inbox/" in out


def test_names_the_oldest_proposal(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/project/a__new.proposed.md", age_days=1)
    _page(tmp_path, "shared/_inbox/project/a__old.proposed.md", age_days=12)

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "2 staged rewrites awaiting review" in out
    assert "oldest a__old.proposed.md, 12 days" in out


def test_singular_wording(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    assert "1 staged rewrite awaiting review" in capsys.readouterr().out


def test_registered_in_doctor_checks() -> None:
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    names = [n for n, _ in DOCTOR_CHECKS]
    assert "staged_proposals" in names
    # Right after the shadowing check: strays first, then the backlog they join.
    assert names.index("staged_proposals") == names.index("stray_proposed") + 1
