"""Strip the PII shapes that showed up in extracted rules: e-mails, API tokens, long hex ids."""
from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._%+-]+@localhost\b"),  # e-mail
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                                # OpenAI/Anthropic-style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{8,}\b"),                            # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                     # AWS access key
    # Exactly 32 hex chars: Cloudflare-style account ids. Deliberately NOT
    # {32,} — that swallowed 40-char git SHAs and content digests, which are
    # public identifiers, not secrets. A dashed UUID never matches: the
    # word-boundary run is broken by the dashes.
    re.compile(r"\b[0-9a-f]{32}\b", re.I),
)
REPLACEMENT = "[redacted]"

# RFC 2606 placeholder domains plus the SSH remote local part. The vault holds
# rules whose whole point is normalising ``user@example.com``, and every git
# remote reads ``git@github.com`` — redacting those destroys the rule.
_ALLOWED_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "localhost", "test", "invalid",
})
_ALLOWED_LOCAL_PARTS = frozenset({"git"})

_EMAIL_PATTERN = _PATTERNS[0]


def _is_allowlisted_email(match: str) -> bool:
    local, _, domain = match.rpartition("@")
    if local.lower() in _ALLOWED_LOCAL_PARTS:
        return True
    return domain.lower() in _ALLOWED_DOMAINS


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, replacements)."""
    total = 0
    for pat in _PATTERNS:
        count = 0

        def _sub(m: re.Match) -> str:
            nonlocal count
            if pat is _EMAIL_PATTERN and _is_allowlisted_email(m.group(0)):
                return m.group(0)
            count += 1
            return REPLACEMENT

        text = pat.sub(_sub, text)
        total += count
    return text, total
