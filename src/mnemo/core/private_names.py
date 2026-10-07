"""Refuse to publish text that names a private repository (#597).

The names and their aliases live in ``<vault>/.mnemo/private-names.tsv``, one
``name<TAB>alias`` per line (blank lines and ``#`` comments allowed). CI cannot
run this check — the list is private — so the PreToolUse(Bash) hook does: a
command that would publish text is denied when that text contains a listed
name, case-insensitively, the same match as the documented
``git grep -i -F -f <(cut -f1 private-names.tsv)``.

What counts as publishing:

* ``git push`` — the messages and added lines of the commits the remote does
  not have yet (``git log -p <pushed> --not --remotes=<remote>``);
* ``gh issue|pr|release create|comment|edit|review|merge`` — the whole command
  text (inline ``--body``/``--title``, heredocs) plus the file behind
  ``--body-file``/``--notes-file``/``-F``;
* ``gh api`` writes — the command text plus ``--input`` and ``-F key=@file``.

A target that itself names a private repo (``--repo me/<name>``, a remote URL
with the name in it) is private, so nothing is checked: naming a private repo
inside that repo publishes nothing.

Cost: a command with no ``git push``/``gh`` in it is decided on its text alone
— no file read, no subprocess. The list is read only for a publishing command,
and ``git`` runs only once the list exists. :func:`check` raises on a broken
list or a failing ``git``; the hook logs that and lets the command run.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

TSV_NAME = "private-names.tsv"
_DEFAULT_ALIAS = "a-private-repo"
_OPERATORS = frozenset({"&&", "||", "|", "&", ";", ";;", "(", ")", "\n", "|&"})
_GH_GROUPS = frozenset({"issue", "pr", "release"})
_GH_VERBS = frozenset({"create", "comment", "edit", "review", "merge"})
_GH_FILE_FLAGS = frozenset({"--body-file", "--notes-file", "-F"})
_GIT_TIMEOUT = 10
_MAX_FILE_BYTES = 1 << 20

Names = List[Tuple[str, str]]


def tsv_path(vault: Path) -> Path:
    return Path(vault) / ".mnemo" / TSV_NAME


def load_names(path: Path) -> Optional[Names]:
    """``(name, alias)`` rows, or ``None`` without the file.

    Raises ``ValueError`` on a line with no tab or an empty name — the message
    gives the line number, never the line, so the log cannot leak a name.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{TSV_NAME} is not UTF-8") from None
    out: Names = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, tab, alias = line.partition("\t")
        if not tab or not name.strip():
            raise ValueError(f"{TSV_NAME} line {n}: expected name<TAB>alias")
        out.append((name.strip(), alias.strip() or _DEFAULT_ALIAS))
    return out


def find_name(text: str, names: Names) -> Optional[Tuple[str, str]]:
    """The first listed ``(name, alias)`` *text* contains, ignoring case.

    Aliases are blanked out first so an alias that happens to contain its
    name (``acme`` → ``acme-x``) never counts as the name.
    """
    low = text.lower()
    for _name, alias in names:
        low = low.replace(alias.lower(), "\0")
    for name, alias in names:
        if name.lower() in low:
            return name, alias
    return None


def _tokens(command: str) -> List[str]:
    lx = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lx.whitespace = " \t\r"
    lx.whitespace_split = True
    try:
        return list(lx)
    except ValueError:  # unbalanced quote, e.g. an apostrophe in a heredoc
        return command.split()


def _invocations(tokens: List[str], cwd: str) -> Iterable[Tuple[str, List[str], str]]:
    """``(program, args, cwd)`` per simple command, following ``cd``."""
    seg: List[str] = []
    for tok in tokens + ["\n"]:
        if tok not in _OPERATORS:
            seg.append(tok)
            continue
        while seg and "=" in seg[0] and not seg[0].startswith("-"):
            seg.pop(0)  # FOO=bar git push
        if seg and seg[0] == "cd":
            target = seg[1] if len(seg) > 1 else "~"
            if target != "-":
                cwd = os.path.join(cwd, os.path.expanduser(target))
        elif seg:
            yield seg[0], seg[1:], cwd
        seg = []


def _git(cwd: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "color.ui=never", *args],
        cwd=cwd, capture_output=True, timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} exited {proc.returncode}")
    return proc.stdout.decode("utf-8", "replace")


def _remote_urls(cwd: str, remote: str = "") -> List[str]:
    if remote and ("/" in remote or ":" in remote):
        return [remote]  # a URL or a path, not a remote's name
    pattern = r"^remote\.%s\.(push)?url$" % (remote.replace(".", r"\.") if remote else ".*")
    proc = subprocess.run(
        ["git", "config", "--get-regexp", pattern],
        cwd=cwd, capture_output=True, timeout=_GIT_TIMEOUT,
    )
    lines = proc.stdout.decode("utf-8", "replace").splitlines()
    return [ln.split(" ", 1)[1] for ln in lines if " " in ln]


def _private_target(urls: Iterable[str], names: Names) -> bool:
    return any(find_name(u, names) for u in urls)


def _git_push(args: List[str], cwd: str, names: Names) -> Optional[Tuple[str, str]]:
    while args and args[0] != "push":
        flag = args.pop(0)
        if flag == "-C" and args:
            cwd = os.path.join(cwd, os.path.expanduser(args.pop(0)))
        elif flag == "-c" and args:
            args.pop(0)
        elif not flag.startswith("-"):
            return None  # some other subcommand
    if not args:
        return None
    rest, positional, revs = args[1:], [], []
    delete = False
    while rest:
        a = rest.pop(0)
        if a in ("--mirror",):
            revs.append("--all")
        elif a in ("--all", "--branches"):
            revs.append("--branches")
        elif a == "--tags":
            revs.append("--tags")
        elif a in ("-d", "--delete"):
            delete = True
        elif a in ("--repo", "-o", "--push-option", "--receive-pack", "--exec") and rest:
            rest.pop(0)
        elif not a.startswith("-"):
            positional.append(a)
    remote = positional[0] if positional else ""
    if delete:
        return None
    for spec in positional[1:]:
        src = spec.lstrip("+").split(":", 1)[0]
        if src:
            revs.append(src)
    if not revs:
        revs = ["HEAD"]
    if _private_target(_remote_urls(cwd, remote), names):
        return None
    exclude = "--remotes=%s" % remote if remote and not ("/" in remote or ":" in remote) else "--remotes"
    log = _git(cwd, "log", "-p", "--no-ext-diff", "--format=%x00%h%n%B", *revs, "--not", exclude, "--")
    for chunk in log.split("\0")[1:]:
        head, _, body = chunk.partition("\n")
        public = []
        in_diff = False
        for line in body.splitlines():
            if line.startswith("diff --git "):
                in_diff = True
            elif not in_diff or (line.startswith("+") and not line.startswith("+++")):
                public.append(line)
        hit = find_name("\n".join(public), names)
        if hit:
            return hit[0], (
                f"commit {head} names it (in its message or an added line). "
                f"Rewrite that commit so it says `{hit[1]}` instead "
                f"(`git commit --amend`, or `git rebase` for an older one), then push again."
            )
    return None


def _read_file(cwd: str, path: str) -> str:
    if not path or path == "-":
        return ""  # stdin: a heredoc is already in the command text
    try:
        with open(os.path.join(cwd, os.path.expanduser(path)), "rb") as fh:
            return fh.read(_MAX_FILE_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""  # gh fails on it too, publishing nothing


def _flag_values(args: List[str], flags: Iterable[str]) -> List[str]:
    flags = tuple(flags)
    out = []
    for i, a in enumerate(args):
        for f in flags:
            if a == f and i + 1 < len(args):
                out.append(args[i + 1])
            elif f.startswith("--") and a.startswith(f + "="):
                out.append(a[len(f) + 1:])
    return out


def _gh(args: List[str], cwd: str, command: str, names: Names) -> Optional[Tuple[str, str]]:
    if not args:
        return None
    files: List[str] = []
    target = _flag_values(args, ("--repo", "-R"))
    if args[0] in _GH_GROUPS:
        if len(args) < 2 or args[1] not in _GH_VERBS:
            return None
        files = _flag_values(args, _GH_FILE_FLAGS)
    elif args[0] == "api":
        rest = args[1:]
        endpoint = next((a for a in rest if not a.startswith("-")), "")
        method = (_flag_values(rest, ("-X", "--method")) or [""])[-1].upper()
        fields = _flag_values(rest, ("-f", "--raw-field", "-F", "--field"))
        inputs = _flag_values(rest, ("--input",))
        if endpoint == "graphql":
            if not any("mutation" in v for v in fields) and not inputs:
                return None
        elif method == "GET" or not (method or fields or inputs):
            return None
        files = inputs + [v.split("=", 1)[1][1:] for v in _flag_values(rest, ("-F", "--field"))
                          if "=@" in v]
        parts = endpoint.lstrip("/").split("/")
        if len(parts) >= 3 and parts[0] == "repos":
            target.append("/".join(parts[1:3]))
    else:
        return None
    if target:
        if _private_target(target, names):
            return None
    elif _private_target(_remote_urls(cwd), names):
        return None
    hit = find_name(command, names)
    if hit:
        return hit[0], f"the command text names it. Write `{hit[1]}` instead, then rerun."
    for f in files:
        hit = find_name(_read_file(cwd, f), names)
        if hit:
            return hit[0], f"the file {f} names it. Write `{hit[1]}` there instead, then rerun."
    return None


def _publishing(tokens: List[str], cwd: str) -> List[Tuple[str, List[str], str]]:
    return [(prog, args, d) for prog, args, d in _invocations(tokens, cwd)
            if (prog == "git" and "push" in args) or prog == "gh"]


def check(command: str, cwd: str, vault: Path) -> Optional[str]:
    """The deny reason when *command* would publish a listed name, else ``None``.

    The reason names the alias, never the name. Raises on a broken list or a
    failing ``git`` — the caller fails open.
    """
    if "push" not in command and "gh" not in command:
        return None
    pubs = _publishing(_tokens(command), cwd or os.getcwd())
    if not pubs:
        return None
    path = tsv_path(vault)
    names = load_names(path)
    if not names:
        return None
    names.sort(key=lambda na: -len(na[0]))
    for prog, args, d in pubs:
        hit = (_git_push(list(args), d, names) if prog == "git"
               else _gh(list(args), d, command, names))
        if hit:
            name, how = hit
            alias = next(a for n, a in names if n == name)
            reason = (
                f"mnemo: this would publish the name of a private repository "
                f"(listed in {path} as `{alias}`): {how} "
                f"Public text never names a private repository."
            )
            return _scrub(reason, names)
    return None


def _scrub(text: str, names: Names) -> str:
    """Belt and braces: no name survives into the deny message itself."""
    import re

    for name, alias in names:
        text = re.sub(re.escape(name), alias, text, flags=re.IGNORECASE)
    return text
