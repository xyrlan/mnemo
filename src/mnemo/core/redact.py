"""Strip secrets and PII from text mnemo writes: credentials, API tokens, e-mails.

Two strengths, because the vault holds two kinds of text:

* :func:`redact` — secrets **and** e-mail addresses. What the LLM extraction
  loop runs on every page it writes; a rule the model phrased never needs the
  customer's address.
* :func:`redact_secrets` — secrets only. What runs on text mnemo copies from
  somewhere the user wrote it (mirrored auto-memory, briefings, imports): a
  test-account e-mail there is a login identifier the rule needs, a password
  next to it never is (#418).

Every credential pattern keeps its label and replaces only the value —
``Password: [redacted]`` still tells a reader a password exists and where it
went — so a rule about *which* account to use survives its secret.
"""
from __future__ import annotations

import re
from typing import List, NamedTuple, Optional, Tuple

REPLACEMENT = "[redacted]"

_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._%+-]+@localhost\b"

# A value wrapped in backticks or quotes. Newlines never belong to one: an
# unclosed backtick must not swallow the rest of the page.
_QUOTED = r"`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'"

# The word that labels a password field. Letters-only lookarounds instead of
# ``\b`` so ``DB_PASSWORD=`` and ``PGPASSWORD=`` match (``_`` is a word char)
# while ``passwords`` and ``passwordless`` do not. A word joined by one ``-``,
# ``/`` or ``.`` is a route or a field (``/auth/set-initial-password``,
# ``user.temporary_password``), not a label; ``--password`` is a flag and is.
# ``pass`` alone is left out: "pass `--no-verify`" is an instruction.
_PASSWORD_KEY = (
    r"(?<![A-Za-z/.])(?<![A-Za-z0-9]-)"
    r"(?i:password|passwd|pwd|passphrase|senha|contrase[ñn]a)(?![A-Za-z])"
)

# A bare value long enough to be one and carrying a digit or a symbol: "sem
# senha: usa o token" is prose, "senha: Abc12345" is not. A quoted value needs
# neither — the quotes already say it is a literal.
_BARE = r"(?=[^\s`'\",;)\]}]*[^A-Za-z\s])[^\s`'\",;)\]}]{4,}"

# Values that name where a secret lives rather than carrying one: env
# references, template slots, masks, and this module's own output — which is
# what makes :func:`redact_secrets` idempotent.
_PLACEHOLDER = re.compile(
    r"""^[`"']?(?:
        \$ | < | \{ | \[ | % | \*{3} | \.{3} | … | x{3,}
        | (?i:process\.env|os\.environ|env\b|none\b|null\b|redacted\b)
    )""",
    re.X,
)


class _Rule(NamedTuple):
    kind: str
    pattern: "re.Pattern[str]"
    # Lower-cased substrings one of which every match contains. A text with
    # none skips the regex: ``mnemo redact`` walks ~11 MB of pages and
    # briefings, and most of them hold no ``@`` pair and no password label.
    hints: Tuple[str, ...] = ()


def _applies(rule: _Rule, lowered: str) -> bool:
    return not rule.hints or any(h in lowered for h in rule.hints)


# Credential shapes first: the ``email / password`` pair needs the address
# still on the page to find the value beside it.
_SECRET_RULES: Tuple[_Rule, ...] = (
    # ``Password: `hunter2` ``, ``password `hunter2` ``, ``senha: hunter2``,
    # ``"password": "hunter2"``, ``DB_PASSWORD=hunter2``. A quoted value may
    # follow the label after plain whitespace; a bare one needs a ``:`` or
    # ``=``, or every "password reset flow" would lose a word.
    _Rule("password", re.compile(
        # With a separator, the label may close its own quotes first:
        # ``"password": "hunter2"``, ``senha: Abc12345``.
        _PASSWORD_KEY + r"""["'`]?[ \t]*(?:[:=]|\bis\b|\bé\b)[ \t]*"""
        + r"(?:(?P<secret>" + _QUOTED + r")|(?P<bare>" + _BARE + r"))"
        # Without one, only whitespace then a quoted literal — never a quote
        # straight after the label, or ``password`, `email`` reads as a
        # label and its value.
        + r"|" + _PASSWORD_KEY + r"[ \t]+(?P<spaced>" + _QUOTED + r")"
    ), ("passw", "pwd", "passphrase", "senha", "contrase")),
    # `` `qa@acme.io` / `hunter2` `` — the login pair with no label at all.
    _Rule("credential pair", re.compile(
        r"(?:" + _EMAIL + r")`?[ \t]*/[ \t]*(?!" + _PASSWORD_KEY + r")"
        + r"(?P<secret>" + _QUOTED + r"|[^\s`'\"/]+)"
    ), ("@",)),
    _Rule("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"), ("aiza",)),
    _Rule("OpenAI/Anthropic key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), ("sk-",)),
    _Rule("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
          ("ghp_", "github_pat_")),
    _Rule("Slack token", re.compile(r"\bxox[abp]-[A-Za-z0-9-]{8,}\b"), ("xox",)),
    _Rule("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), ("akia",)),
    # Exactly 32 hex chars: Cloudflare-style account ids. Deliberately NOT
    # {32,} — that swallowed 40-char git SHAs and content digests, which are
    # public identifiers, not secrets. A dashed UUID never matches: the
    # word-boundary run is broken by the dashes.
    _Rule("32-hex id", re.compile(r"\b[0-9a-f]{32}\b", re.I)),
)

_EMAIL_RULE = _Rule("e-mail", re.compile(_EMAIL), ("@",))

# RFC 2606 placeholder domains plus the SSH remote local part. The vault holds
# rules whose whole point is normalising ``user@example.com``, and every git
# remote reads ``git@github.com`` — redacting those destroys the rule.
_ALLOWED_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "localhost", "test", "invalid",
})
_ALLOWED_LOCAL_PARTS = frozenset({"git"})


def _is_allowlisted_email(match: str) -> bool:
    local, _, domain = match.rpartition("@")
    if local.lower() in _ALLOWED_LOCAL_PARTS:
        return True
    return domain.lower() in _ALLOWED_DOMAINS


class Finding(NamedTuple):
    """Where a secret sits — never what it is."""

    kind: str
    start: int
    end: int


def _span(rule: _Rule, m: "re.Match[str]") -> Optional[Tuple[int, int]]:
    """The span to replace for this match, or ``None`` when it is not a secret."""
    if rule is _EMAIL_RULE:
        return None if _is_allowlisted_email(m.group(0)) else m.span()
    groups = m.re.groupindex
    for name in ("secret", "bare", "spaced"):
        if name in groups and m.group(name) is not None:
            value = m.group(name)
            if _PLACEHOLDER.match(value) or "@" in value:
                return None
            start, end = m.span(name)
            if value[0] in "`\"'":
                # Keep the quotes: ``"password": "[redacted]"`` stays JSON.
                start, end = start + 1, end - 1
            return start, end
    return m.span()


def _rules(emails: bool) -> Tuple[_Rule, ...]:
    return _SECRET_RULES + ((_EMAIL_RULE,) if emails else ())


def find(text: str, *, emails: bool = False) -> List[Finding]:
    """Every secret :func:`redact` would replace, as kind + offsets.

    For reports about text already on disk: the caller can say *which* page
    and *what kind* without the value ever reaching a terminal or a log.
    Offsets are into ``text`` as given; overlapping hits are reported once.
    """
    found: List[Finding] = []
    lowered = text.lower()
    for rule in _rules(emails):
        if not _applies(rule, lowered):
            continue
        for m in rule.pattern.finditer(text):
            span = _span(rule, m)
            if span is None:
                continue
            if any(f.start < span[1] and span[0] < f.end for f in found):
                continue
            found.append(Finding(rule.kind, span[0], span[1]))
    return sorted(found, key=lambda f: f.start)


def redact(text: str, *, emails: bool = True) -> Tuple[str, int]:
    """Return (redacted_text, replacements)."""
    total = 0
    for rule in _rules(emails):
        # Re-lowered per rule: an earlier rule's replacement changes the text.
        if not _applies(rule, text.lower()):
            continue
        count = 0

        def _sub(m: "re.Match[str]", rule: _Rule = rule) -> str:
            nonlocal count
            span = _span(rule, m)
            if span is None:
                return m.group(0)
            count += 1
            start, end = span[0] - m.start(), span[1] - m.start()
            whole = m.group(0)
            return whole[:start] + REPLACEMENT + whole[end:]

        text = rule.pattern.sub(_sub, text)
        total += count
    return text, total


def redact_secrets(text: str) -> Tuple[str, int]:
    """:func:`redact` without the e-mail pass: for text the user wrote.

    A login address is the identifier a rule about a test account exists to
    carry; the password beside it is the part no page needs.
    """
    return redact(text, emails=False)
