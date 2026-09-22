"""Unit tests for core/redact — PII shapes stripped from extracted rules."""
from __future__ import annotations

from mnemo.core.redact import find, redact, redact_secrets


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


# --- #418: credentials, Google keys, and the secrets-only pass ---------------
#
# Every value below is synthetic. The Google key is assembled at runtime so no
# key-shaped literal sits in the repo for a secret scanner to trip over.

GOOGLE_KEY = "AIza" + "Sy" + "B7xQ-k_9" * 4 + "a"  # 4 + 35 chars


def test_google_key_is_39_chars_and_redacted():
    assert len(GOOGLE_KEY) == 39
    assert redact_secrets(f"maps key {GOOGLE_KEY} in .env") == ("maps key [redacted] in .env", 1)


def test_a_longer_run_is_not_a_google_key():
    """35 is the length: a 40-char run starting ``AIza`` is something else."""
    assert redact_secrets(f"id {GOOGLE_KEY}XYZ")[1] == 0


def test_labelled_password_in_backticks():
    assert redact_secrets("QA login — Password: `Tr0ub4dor&3`") == (
        "QA login — Password: `[redacted]`", 1)


def test_password_then_backticks_without_a_colon():
    assert redact_secrets("log in with password `Tr0ub4dor&3` on staging") == (
        "log in with password `[redacted]` on staging", 1)


def test_email_slash_password_pair_keeps_the_address():
    """A test-account address is the identifier the rule needs; its password is not."""
    assert redact_secrets("use `qa.admin@acme-corp.io` / `Tr0ub4dor&3` for admin") == (
        "use `qa.admin@acme-corp.io` / `[redacted]` for admin", 1)


def test_email_senha_pair_redacts_only_the_password():
    out, n = redact_secrets("cliente: email: maria.souza@gmail.com / senha: Abc12345!")
    assert (out, n) == ("cliente: email: maria.souza@gmail.com / senha: [redacted]", 1)


def test_full_redact_also_takes_the_address():
    out, n = redact("use `qa.admin@acme-corp.io` / `Tr0ub4dor&3` for admin")
    assert "acme-corp.io" not in out and "Tr0ub4dor" not in out
    assert n == 2


def test_env_json_and_flag_shapes():
    assert redact_secrets("DB_PASSWORD=s3cr3t-Pw;") == ("DB_PASSWORD=[redacted];", 1)
    assert redact_secrets('{"password": "s3cr3t"}') == ('{"password": "[redacted]"}', 1)
    assert redact_secrets("mysql --password=s3cr3t!") == ("mysql --password=[redacted]", 1)


def test_prose_about_passwords_survives():
    """Shapes from the real vault that a first draft of these patterns flagged
    and that are not credentials: field names, routes, env references."""
    for text in (
        "the password reset flow sends a link",
        "passwords must be hashed with bcrypt",
        "sem senha: usa o token do header",
        "redact by field name (e.g., `password`, `token`)",
        "fields: `email`, `phoneNumber`, `password`, `name`",
        "POST /auth/set-initial-password `{token}`",
        "`forgot-password/reset-password` routes",
        "never read `/etc/passwd` in tests",
        "set `DB_USERNAME`/`DB_PASSWORD` in the env",
        "pass `--no-verify` only when asked",
        "password: $DB_PASSWORD",
        "password: `<your password>`",
        "a@acme-corp.io / b@acme-corp.io both get the mail",
    ):
        assert redact_secrets(text) == (text, 0), text


def test_redact_secrets_is_idempotent():
    once, n = redact_secrets(f"Password: `Tr0ub4dor&3` and key {GOOGLE_KEY}")
    assert n == 2
    assert redact_secrets(once) == (once, 0)


def test_find_reports_kind_and_span_not_value():
    text = f"x Password: `Tr0ub4dor&3` y {GOOGLE_KEY}"
    hits = find(text)
    assert [h.kind for h in hits] == ["password", "Google API key"]
    assert text[hits[0].start:hits[0].end] == "Tr0ub4dor&3"
    assert text[hits[1].start:hits[1].end] == GOOGLE_KEY


def test_find_skips_emails_unless_asked():
    assert find("mail qa@acme-corp.io") == []
    assert [h.kind for h in find("mail qa@acme-corp.io", emails=True)] == ["e-mail"]
