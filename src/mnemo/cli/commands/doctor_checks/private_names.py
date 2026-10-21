"""Doctor check — publishing is not checked for private names (#746).

The PreToolUse(Bash) hook denies a publishing command that names a private
repository listed in ``<vault>/.mnemo/private-names.tsv`` (#597). Without that
file the hook lets every command through, which is right for someone with no
private repos and wrong for someone whose list just isn't on this machine.
This row says so. It is silent once the file exists — a comment-only file is
the explicit "nothing private" — and a broken file is
:func:`mnemo.core.private_names.check`'s to report, not this row's.
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_private_names(vault: Path) -> bool:
    """Doctor-registry adapter — always True: a missing list is legitimate."""
    from mnemo.core import private_names

    path = private_names.tsv_path(vault)
    try:
        if path.exists():
            return True
    except OSError:  # a diagnostic must not abort the run
        return True
    print(f"  ℹ Private names: no {path}, so publishing commands are not checked for private repo names")
    print("       → one name<TAB>alias per line; a file with only # comments means nothing is private")
    return True
