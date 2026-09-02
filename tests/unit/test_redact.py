"""Unit tests for core/redact — PII shapes stripped from extracted rules."""
from __future__ import annotations

from mnemo.core.redact import redact


def test_redacts_emails_tokens_and_long_hex():
    text = ("mail me at ana.silva@acme-corp.io, key sk-abc123DEF456ghi789jkl012, "
            "gh ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789, slack xoxb-1234-5678-abcd, "
            "aws AKIAIOSFODNN7EXAMPLE, id 0123456789abcdef0123456789abcdef")
    out, n = redact(text)
    assert n == 6
    assert "acme-corp.io" not in out and "sk-abc" not in out and "ghp_" not in out
    assert "xoxb" not in out and "AKIA" not in out and "0123456789abcdef0123456789abcdef" not in out
    assert out.count("[redacted]") == 6


def test_leaves_clean_text_alone():
    assert redact("use yarn, run `git status`, commit a1b2c3d") == ("use yarn, run `git status`, commit a1b2c3d", 0)


def test_git_sha_survives():
    """40 hex chars is a git SHA — a public identifier, not a secret."""
    sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
    assert redact(f"see commit {sha}") == (f"see commit {sha}", 0)


def test_dashed_uuid_survives():
    """The dashes break the 32-hex run, so a UUID never matches."""
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    assert redact(f"request {uuid}") == (f"request {uuid}", 0)


def test_bare_32_hex_id_is_redacted():
    """Cloudflare-style account ids are exactly 32 hex chars."""
    out, n = redact("account 0123456789abcdef0123456789abcdef")
    assert n == 1
    assert out == "account [redacted]"


def test_placeholder_and_git_addresses_survive():
    """RFC 2606 domains and SSH remotes are documentation, not PII.

    The vault holds rules whose whole point is normalising user@example.com.
    """
    for addr in ("user@example.com", "a@example.org", "b@example.net",
                 "c@test", "d@invalid", "git@github.com"):
        assert redact(f"contact {addr} today") == (f"contact {addr} today", 0), addr
