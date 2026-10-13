"""The machine's own ``git``, run the same way on every machine (#642).

mnemo stores no credential and speaks no protocol: it runs ``git`` and lets
the user's SSH agent or credential helper answer the hub. What it does pin,
on every call, is everything that would make two machines disagree about a
vault's bytes or make a sync wait for a person:

* no line-ending conversion, whatever ``core.autocrlf`` the machine carries
  (Git for Windows ships it on) — a page's bytes are what extraction hashed;
* an identity of sync's own, so a commit never needs ``user.name`` set;
* no hooks, no signing, no prompt — a ``pre-push`` hook or a passphrase
  dialog must not decide whether a vault syncs.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: The executable. A module attribute so a test can name one that is missing.
GIT = "git"

IDENT_NAME = "mnemo sync"
IDENT_EMAIL = "sync@mnemo.invalid"

#: Seconds a call that talks to the hub may take before it counts as unreachable.
NETWORK_TIMEOUT = 120.0
_LOCAL_TIMEOUT = 300.0

# Variables that would point this git at some other repository or index — set
# when mnemo is itself started from inside a git hook.
_LEAKY = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
)


_RESOLVED: Dict[str, str] = {}


def _executable() -> str:
    """What to run for :data:`GIT`.

    On macOS ``/usr/bin/git`` is a shim that locates the real binary through
    ``xcrun`` on every call, which costs several times what git itself does,
    and a pull is about ten calls. So the binary the shim would find is asked
    for once per process and run directly after that. Elsewhere, and whenever the
    question fails, :data:`GIT` is run as it is — on Windows the wrapper is
    what puts ssh and the credential helper on git's PATH.
    """
    if sys.platform != "darwin":
        return GIT
    found = _RESOLVED.get(GIT)
    if found is None:
        found = GIT
        try:
            proc = subprocess.run([GIT, "--exec-path"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                  check=False, timeout=30)
            real = Path(proc.stdout.decode("utf-8", errors="replace").strip()) / "git"
            if proc.returncode == 0 and real.is_file() and os.access(str(real), os.X_OK):
                found = str(real)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _RESOLVED[GIT] = found
    return found


class GitMissing(RuntimeError):
    """There is no ``git`` on this machine's PATH."""


class GitError(RuntimeError):
    """A git call failed. ``stderr`` is git's own words, secrets redacted."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {args[0] if args else ''} failed ({returncode}): {stderr}")


class GitTimeout(GitError):
    """A git call ran past its bound."""


def _tail(raw: bytes) -> str:
    """The last line git said, with anything secret-shaped redacted.

    A remote URL can carry ``user:token@``, and git repeats the URL in its
    errors; this text reaches a terminal, the state file and the log.
    """
    from mnemo.core.redact import redact_secrets

    lines = [ln.strip() for ln in raw.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    text, _ = redact_secrets(lines[-1] if lines else "")
    return text[:300]


class Git:
    """``git`` bound to one work tree."""

    def __init__(self, work_tree: Path) -> None:
        self.work_tree = Path(work_tree)
        self.git_dir = self.work_tree / ".git"

    def _config(self) -> List[str]:
        pairs = (
            ("core.autocrlf", "false"),
            ("core.safecrlf", "false"),
            ("core.fileMode", "false"),
            ("core.quotePath", "false"),
            ("core.longPaths", "true"),
            ("core.precomposeUnicode", "true"),
            # A directory that does not exist: no hook of the user's runs.
            ("core.hooksPath", str(self.git_dir / "mnemo-sync-no-hooks")),
            ("core.fsmonitor", "false"),
            ("commit.gpgSign", "false"),
            ("gc.auto", "0"),
            ("advice.detachedHead", "false"),
        )
        out: List[str] = []
        for key, value in pairs:
            out += ["-c", f"{key}={value}"]
        return out

    def _env(self, extra: Optional[Dict[str, str]]) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _LEAKY}
        env.update({
            "GIT_AUTHOR_NAME": IDENT_NAME,
            "GIT_AUTHOR_EMAIL": IDENT_EMAIL,
            "GIT_COMMITTER_NAME": IDENT_NAME,
            "GIT_COMMITTER_EMAIL": IDENT_EMAIL,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        })
        if extra:
            env.update(extra)
        return env

    def run(
        self,
        *args: str,
        check: bool = True,
        stdin: Optional[bytes] = None,
        timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[Path] = None,
    ) -> "subprocess.CompletedProcess[bytes]":
        """Run one git command; stdout and stderr come back as bytes."""
        argv = [_executable(), *self._config(), *args]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd or self.work_tree),
                input=stdin,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env(env),
                timeout=timeout or _LOCAL_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            # Either the executable or the directory to run it in. The second
            # is the caller's bug; the first is the user's machine.
            if not Path(cwd or self.work_tree).is_dir():
                raise
            raise GitMissing(GIT) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitTimeout(args, -1, f"timed out after {exc.timeout:.0f}s") from exc
        if check and proc.returncode != 0:
            raise GitError(args, proc.returncode, _tail(proc.stderr))
        return proc

    def out(self, *args: str, **kwargs) -> str:
        """Stdout of one command, decoded and stripped."""
        return self.run(*args, **kwargs).stdout.decode("utf-8", errors="surrogateescape").strip()

    def ok(self, *args: str, **kwargs) -> bool:
        return self.run(*args, check=False, **kwargs).returncode == 0

    def rev(self, name: str) -> Optional[str]:
        """The object ``name`` resolves to, or ``None``."""
        proc = self.run("rev-parse", "--verify", "-q", name, check=False)
        value = proc.stdout.decode("ascii", errors="replace").strip()
        return value if proc.returncode == 0 and value else None

    def blobs(self, shas: Sequence[str]) -> Dict[str, bytes]:
        """The bytes of every blob named, through one ``cat-file --batch``."""
        wanted = list(dict.fromkeys(shas))
        if not wanted:
            return {}
        raw = self.run("cat-file", "--batch", stdin=("\n".join(wanted) + "\n").encode("ascii")).stdout
        found: Dict[str, bytes] = {}
        pos = 0
        for sha in wanted:
            end = raw.index(b"\n", pos)
            header = raw[pos:end].split()
            pos = end + 1
            if len(header) != 3:
                continue  # "<sha> missing"
            size = int(header[2])
            found[sha] = raw[pos:pos + size]
            pos += size + 1
        return found

