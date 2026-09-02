"""Unit tests for core/redact — PII shapes stripped from extracted rules."""
from __future__ import annotations

from mnemo.core.redact import redact


def test_redacts_emails_tokens_and_long_hex():
    text = ("mail me at ana.silva@example.com, key sk-abc123DEF456ghi789jkl012, "
            "gh ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789, slack xoxb-1234-5678-abcd, "
            "aws AKIAIOSFODNN7EXAMPLE, id 0123456789abcdef0123456789abcdef")
    out, n = redact(text)
    assert n == 6
    assert "example.com" not in out and "sk-abc" not in out and "ghp_" not in out
    assert "xoxb" not in out and "AKIA" not in out and "0123456789abcdef0123456789abcdef" not in out
    assert out.count("[redacted]") == 6


def test_leaves_clean_text_alone():
    assert redact("use yarn, run `git status`, commit a1b2c3d") == ("use yarn, run `git status`, commit a1b2c3d", 0)
