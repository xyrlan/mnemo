"""``mnemo stale`` — which live rules cite a file that is no longer there (#274).

A rule is written against the code as it stood. The code moves; the rule does
not. ``src/parser.py`` becomes ``src/mnemo/cli/parser.py`` and the rule keeps
sending the model to the old place. This module finds that.

What it checks, and why it is only this
---------------------------------------

**Paths only.** #274 proposed checking three kinds of citation: paths,
identifiers (backticked symbols), and ``--flags``. Measured against the real
vault (1704 live rules attributed to six local repos), only paths carry signal:

======================  ==========  ==========  ====================
citation kind           resolve     unresolved  of those, real
======================  ==========  ==========  ====================
``path`` / ``path:n``   360         32          most, and locatable
backticked identifier   2093        153         **0 of 153**
======================  ==========  ==========  ====================

Not one of the 153 unresolved identifiers had ever been *defined* anywhere in
its repo's history (``git log --all -G '(def|function|class|fn|...) <name>'``
over all six repos: 153 checked, 0 hits). They were never this codebase's
symbols to begin with. Each is a false positive for a reason no threshold can
fix:

* **third-party vocabulary** — ``login_customer_id`` (Google Ads),
  ``change_website_conversation_state`` (Crisp), ``period_end`` (Stripe),
  ``GetMessage()`` (Win32), ``formatDistanceToNow()`` (date-fns),
  ``os_kill_impl`` (CPython's own C source). Not in the repo because they were
  never the repo's to define.
* **names the rule is proposing** — "**How to apply:** create a helper (e.g.
  ``getTransactionContractsUsd()``)". The rule invents the name; absence is
  the rule being unfollowed, not stale.
* **names the rule says were deleted** — "this eliminates the old
  ``RedeApoioCard.lastSeen`` helpers entirely". Absence is the rule being
  *right*.

A grep of HEAD cannot separate those three from a genuine rename, because the
distinguishing information is in the prose around the citation, not in the
token. So identifiers are counted and reported as **skipped**, never flagged.
That is the honest answer, and #274 asked for precision over recall.

The same false-positive class reaches paths through the "e.g." / "create"
shapes, and there it *is* separable, because the marker sits in the prose right
before the citation — see :func:`is_hedged`. Suppressing those took the real
vault from 38 findings to 26 (1.5% of 1728 live rules across six repos).

A path is different: it is unambiguous about what it refers to, the repo is
authoritative about whether it exists, and when it is gone a basename search
usually recovers where it went — which is what makes the finding actionable
and what #271 needs from ``paths:``.

What is skipped even among paths
--------------------------------

Build output (``dist/``, ``node_modules/``), vault-internal files
(``.mnemo/``, ``shared/``), editor state (``.obsidian/``) and planning
artifacts (``docs/superpowers/``) are untracked by design; flagging them would
be noise. Bare prose that merely looks like a path (``mfull1/2/3.png``) is
excluded by requiring a real extension and rejecting a first segment that is
not plausibly a directory.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPORT_NAME = "stale-report.json"

# A backticked path citation: two or more segments, a real extension, and an
# optional `:line` / `:line-line` suffix (only 6% of real citations carry one,
# so the line number is parsed and kept for the report but never checked —
# a line that moved inside a file that still exists is not a broken citation).
# A leading `./` or `../` is stripped; a leading *dot segment* is not —
# `.github/workflows/ci.yml` must keep its dot, or the path stops matching
# anything tracked and every dotfile citation reads as stale.
# Directory segments may carry `()` and `[]`: Next.js route groups and dynamic
# segments are real tracked directories (`src/app/(auth)/_lib/format.ts`,
# `src/app/[id]/page.tsx`), and sg-imports has both. Excluding them silently
# dropped those citations instead of checking them.
_PATH_RE = re.compile(
    r"^(?:\.{1,2}/)*"
    r"(?P<path>(?:\.?[\w.\-@+()\[\]]+/)+\.?[\w.\-@+()\[\]]+\.(?P<ext>[A-Za-z][A-Za-z0-9]{0,7}))"
    r"(?::(?P<line>\d+(?:-\d+)?))?$"
)

# Extensions that name a source or config file somebody could open. A citation
# ending in anything else (`.png`, `.msi`, `.exe`) is either an asset nobody
# tracks or, more often, prose that happens to contain a slash and a dot.
CHECKABLE_EXTS: frozenset[str] = frozenset({
    "py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts",
    "c", "h", "cc", "cpp", "hpp", "cs", "rs", "go", "rb", "java", "kt",
    "swift", "lua", "php", "sh", "bash", "zsh", "ps1", "sql", "css", "scss",
    "html", "vue", "svelte", "md", "json", "jsonc", "yml", "yaml", "toml",
    "ini", "cfg", "conf", "env", "tape", "def", "plist", "gradle", "xaml",
    "proto", "graphql", "prisma", "tf", "dockerfile", "mk", "cmake",
})

# First path segment that means "not a tracked source file". Checked against
# the citation as written, so `dist/main.js` is skipped but
# `src/dist_helper.ts` is not.
_UNTRACKED_ROOTS: frozenset[str] = frozenset({
    "node_modules", "dist", "build", "out", "target", "coverage", "vendor",
    ".next", ".nuxt", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mnemo", ".obsidian", "shared", "bots", "briefings", "scratchpad",
})

# Planning artifacts: written once, superseded, never kept in sync. A rule
# citing the spec it was born from is not citing live code.
_UNTRACKED_PREFIXES: tuple[str, ...] = (
    "docs/superpowers/",
)


@dataclass(frozen=True)
class Citation:
    """One backticked path a rule names."""

    span: str          # as written, inside the backticks
    path: str          # the path part, `./` and `:line` stripped
    line: str | None   # the `:line` suffix, when there was one


@dataclass
class PageFinding:
    """One live rule and the verdict on every path it cites."""

    page: Path
    slug: str
    resolved: list[Citation] = field(default_factory=list)
    missing: list[tuple[Citation, str | None]] = field(default_factory=list)
    skipped: list[tuple[Citation, str]] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        return bool(self.missing)


@dataclass
class Report:
    """The whole run: what was checked, what is stale, what was not checkable."""

    project: str
    repo_root: str
    ref: str
    pages_scanned: int = 0
    findings: list[PageFinding] = field(default_factory=list)
    identifiers_skipped: int = 0
    paths_skipped: int = 0
    paths_resolved: int = 0

    @property
    def stale_pages(self) -> list[PageFinding]:
        return [f for f in self.findings if f.is_stale]

    @property
    def rate(self) -> float:
        if not self.pages_scanned:
            return 0.0
        return len(self.stale_pages) / self.pages_scanned


def _git(args: list[str], *, cwd: Path | str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - platform
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def tracked_paths(repo_root: Path | str, *, ref: str = "HEAD") -> set[str]:
    """Every path tracked at *ref*. Empty set when this is not a git repo."""
    done = _git(["ls-tree", "-r", "--name-only", ref], cwd=repo_root)
    if done.returncode != 0:
        return set()
    return {line for line in done.stdout.splitlines() if line}


def parse_citation(span: str) -> Citation | None:
    """The path *span* names, or ``None`` when it does not name one."""
    match = _PATH_RE.match(span.strip())
    if not match:
        return None
    if match.group("ext").lower() not in CHECKABLE_EXTS:
        return None
    return Citation(span=span.strip(), path=match.group("path"), line=match.group("line"))


def skip_reason(cite: Citation) -> str | None:
    """Why *cite* cannot be checked against the repo, or ``None`` if it can."""
    first = cite.path.split("/", 1)[0]
    if first in _UNTRACKED_ROOTS:
        return f"{first}/ is not tracked source"
    if any(cite.path.startswith(p) for p in _UNTRACKED_PREFIXES):
        return "planning artifact, not live code"
    return None


# Prose that marks the following path as one the rule is *proposing* or
# offering as an illustration, not one it is citing. Measured on the real
# vault, this is 12 of 48 otherwise-unresolvable path citations — the same
# false-positive class that makes identifiers uncheckable, and the only one
# that shows up in a path with a recognisable marker:
#
#   "**How to apply:** create `src/lib/orders/updateOrderStatusCore.ts`"
#   "a shared utility (e.g., `src/_lib/format.ts`)"
#   "- **Files** — path globs affected (e.g., `src/parser.py`, ...)"
#
# The file's absence is the rule being unfollowed, or generic advice using a
# placeholder. Neither is a citation that went stale.
# Anchored on word boundaries: an unanchored `like` also matched "unlike", and
# a bare `add` matched "we add the guard in `src/x.ts`" — both genuine
# citations. Only markers that actually introduce an example or a file the
# rule is asking someone to create are listed.
_HEDGE_RE = re.compile(
    r"(?:\be\.?g\.?|\bi\.?e\.?|\bfor example\b|\bfor instance\b|\bsuch as\b"
    r"|\bor equivalent\b|\bsimilar to\b|\b(?:files?|something|one) like\b"
    r"|\bcreate\b|\bcriar\b|\bcrie\b)"
    r"[^`]{0,48}$",
    re.IGNORECASE,
)


def _iter_backticked(body: str) -> Iterable[tuple[str, str]]:
    """Every backticked span with the prose that precedes it."""
    for match in re.finditer(r"`([^`\n]{1,120})`", body):
        start = max(0, match.start() - 110)
        yield match.group(1), body[start:match.start()]


def is_hedged(preceding: str) -> bool:
    """Whether the prose before a citation marks it as proposed or illustrative."""
    return bool(_HEDGE_RE.search(preceding))


def relocated(path: str, tracked: set[str]) -> str | None:
    """Where the file with this basename lives now, when exactly one does.

    The actionable half of the finding: ``src/parser.py`` is gone, but
    ``src/mnemo/cli/parser.py`` is right there. Only answered when the
    basename is unique at *ref* — two candidates is a guess, and a wrong
    "did you mean" is worse than none.
    """
    base = path.rsplit("/", 1)[-1]
    hits = [t for t in tracked if t.rsplit("/", 1)[-1] == base]
    return hits[0] if len(hits) == 1 else None


def check_page(
    page: Path, body: str, slug: str, tracked: set[str]
) -> PageFinding:
    """Every path *body* cites, split into resolved / missing / skipped."""
    finding = PageFinding(page=page, slug=slug)
    seen: set[str] = set()
    for span, preceding in _iter_backticked(body):
        cite = parse_citation(span)
        if cite is None:
            continue
        if cite.path in seen:
            continue
        seen.add(cite.path)
        reason = skip_reason(cite)
        if reason:
            finding.skipped.append((cite, reason))
            continue
        if is_hedged(preceding):
            finding.skipped.append((cite, "proposed or illustrative, not cited"))
            continue
        if cite.path in tracked or any(t.endswith("/" + cite.path) for t in tracked):
            finding.resolved.append(cite)
        else:
            finding.missing.append((cite, relocated(cite.path, tracked)))
    return finding


def count_identifiers(body: str) -> int:
    """How many backticked spans look like a symbol rather than a path.

    Reported, never flagged — see the module docstring. Counting them is the
    point: the report has to say how much it deliberately did not check.
    """
    total = 0
    for raw, _preceding in _iter_backticked(body):
        span = raw.strip()
        if parse_citation(span) is not None:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\(\)?", span):
            total += 1
        elif re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", span):
            total += 1
    return total


def run(
    vault_root: Path,
    *,
    project: str,
    repo_root: Path | str,
    ref: str = "HEAD",
) -> Report:
    """Check every live rule attributed to *project* against *repo_root* at *ref*."""
    from mnemo.core.filters import (
        derive_rule_slug,
        is_consumer_visible,
        iter_shared_pages,
    )
    from mnemo.core.reclassify_types import split_frontmatter
    from mnemo.core.rule_activation.index import projects_for_rule
    from mnemo.core.text_utils import retrieval_body

    report = Report(project=project, repo_root=str(repo_root), ref=ref)
    tracked = tracked_paths(repo_root, ref=ref)
    if not tracked:
        return report

    for page in iter_shared_pages(vault_root, include_inbox=False):
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, body = split_frontmatter(text)
        if not frontmatter or not is_consumer_visible(page, frontmatter, vault_root):
            continue
        sources = [s for s in (frontmatter.get("sources") or []) if isinstance(s, str)]
        if project not in projects_for_rule(sources, frontmatter=frontmatter):
            continue

        report.pages_scanned += 1
        slug = derive_rule_slug(frontmatter, page.stem)
        finding = check_page(page, retrieval_body(body), slug, tracked)
        report.identifiers_skipped += count_identifiers(retrieval_body(body))
        report.paths_skipped += len(finding.skipped)
        report.paths_resolved += len(finding.resolved)
        if finding.missing:
            report.findings.append(finding)
    return report


def format_report(report: Report) -> str:
    """The human view: what is stale, where it went, what was not checked."""
    lines: list[str] = []
    if not report.pages_scanned:
        if not tracked_paths(report.repo_root, ref=report.ref):
            lines.append(f"{report.repo_root} is not a git repository at {report.ref}.")
        else:
            lines.append(f"No live rules are attributed to `{report.project}`.")
        return "\n".join(lines)

    stale = report.stale_pages
    lines.append(
        f"{len(stale)} of {report.pages_scanned} live rules for `{report.project}` "
        f"cite a file that is not at {report.ref} ({100 * report.rate:.1f}%)."
    )
    lines.append("")
    for finding in stale:
        lines.append(f"  {finding.slug}")
        for cite, moved in finding.missing:
            suffix = f"  → now {moved}" if moved else ""
            lines.append(f"      ✗ {cite.span}{suffix}")
        lines.append(f"        {finding.page}")
    if stale:
        lines.append("")
    lines.append(
        f"  checked {report.paths_resolved + sum(len(f.missing) for f in report.findings)} "
        f"path citations; {report.paths_resolved} resolve."
    )
    lines.append(
        f"  not checked: {report.identifiers_skipped} backticked identifiers "
        f"(a symbol absent from HEAD is usually a third-party name, a name the "
        f"rule proposes, or one it says was deleted — see `mnemo stale --why`), "
        f"{report.paths_skipped} paths outside tracked source."
    )
    return "\n".join(lines)


WHY = """\
`mnemo stale` checks cited paths, not cited symbols.

Measured on the real vault (1704 live rules, six repos), backticked
identifiers absent from HEAD were false positives in all 153 cases — not one
had ever been *defined* anywhere in its repo's git history:

  third-party vocabulary  `login_customer_id` (Google Ads), `period_end`
                          (Stripe), `GetMessage()` (Win32), `os_kill_impl`
                          (CPython) — never this repo's to define
  a name being proposed   "**How to apply:** create a helper (e.g.
                          `getTransactionContractsUsd()`)"
  a name it says is gone  "this eliminates the old `RedeApoioCard.lastSeen`
                          helpers entirely" — absence is the rule being right

What separates those from a real rename is the prose around the citation, not
the token, so a grep of HEAD cannot do it. Flagging them would have produced
153 findings with no true positives at all, and a check that cries wolf gets
ignored. Paths are reported instead: unambiguous about what they refer to, and
when one is gone a unique basename says where it went.
"""
