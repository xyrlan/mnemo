"""Strip the PII shapes that showed up in extracted rules: e-mails, API tokens, long hex ids."""
from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),          # e-mail
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                                # OpenAI/Anthropic-style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{8,}\b"),                            # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                     # AWS access key
    re.compile(r"\b[0-9a-f]{32,}\b", re.I),                                  # long hex ids
)
REPLACEMENT = "[redacted]"


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, replacements)."""
    total = 0
    for pat in _PATTERNS:
        text, n = pat.subn(REPLACEMENT, text)
        total += n
    return text, total
