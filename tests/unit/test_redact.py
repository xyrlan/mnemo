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


# --- #592: shapes that passed through redact_secrets ---------------------------
#
# Synthetic values, assembled at runtime so no secret-shaped literal sits in the
# repo for a scanner to trip over.

_B62 = "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zC1dF3"  # 36 chars, mixed


def test_every_github_token_prefix_is_redacted():
    for prefix in ("gho_", "ghs_", "ghu_", "ghr_"):
        assert redact_secrets(f"token {prefix}{_B62} here") == ("token [redacted] here", 1), prefix


def test_jwt_is_redacted():
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert redact_secrets(f"cookie {jwt} expired") == ("cookie [redacted] expired", 1)


def test_pem_private_key_body_is_redacted_and_markers_kept():
    body = "\n" + ("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 2) + "\nAbCdEf0123456789+/==\n"
    begin, end = "-----BEGIN RSA PRIVATE" + " KEY-----", "-----END RSA PRIVATE" + " KEY-----"
    out, n = redact_secrets(f"key:\n{begin}{body}{end}\nafter")
    assert (out, n) == (f"key:\n{begin}\n[redacted]\n{end}\nafter", 1)


def test_unlabelled_pem_private_key_is_redacted():
    begin, end = "-----BEGIN PRIVATE" + " KEY-----", "-----END PRIVATE" + " KEY-----"
    out, n = redact_secrets(f"{begin}\nMC4CAQAwBQYDK2VwBCIEIA0123456789abcdef\n{end}")
    assert (out, n) == (f"{begin}\n[redacted]\n{end}", 1)


def test_url_credentials_keep_user_and_host():
    assert redact_secrets("DATABASE_URL is postgres://app:s3cretPw@db.internal:5432/app") == (
        "DATABASE_URL is postgres://app:[redacted]@db.internal:5432/app", 1)
    assert redact_secrets("connect to postgres://user:pass@host/db") == (
        "connect to postgres://user:[redacted]@host/db", 1)


def test_url_credentials_placeholders_and_ssh_remotes_survive():
    for text in (
        "postgres://user:${DB_PASS}@host/db",
        "postgres://user:<password>@host/db",
        "ssh://git@github.com:22/org/repo.git",
        "git@github.com:org/repo.git",
        "https://github.com/org/repo",
    ):
        assert redact_secrets(text) == (text, 0), text


def test_generic_assignment_keeps_the_name():
    assert redact_secrets("MY_SERVICE_TOKEN=tk_9f8e7d6c5b4a3210") == ("MY_SERVICE_TOKEN=[redacted]", 1)
    assert redact_secrets("export OPENAI_API_KEY=proj_" + _B62) == ("export OPENAI_API_KEY=[redacted]", 1)
    assert redact_secrets("APP_SECRET='q8W!e7R%t6Y#'") == ("APP_SECRET='[redacted]'", 1)
    assert redact_secrets("STRIPE_KEY: 9f8e7d6c5b4a3210") == ("STRIPE_KEY: [redacted]", 1)


def test_generic_assignment_without_a_secret_survives():
    for text in (
        "set `OPENAI_API_KEY=` before running",
        "GITHUB_TOKEN=${{ secrets.GITHUB_TOKEN }}",
        "MNEMO_RERANK_KEY=$KEY",
        "SORT_KEY=name",
        "SSH_KEY=~/.ssh/id_rsa",
        "keep the deploy key in ~/.ssh/id_rsa, never in the repo",
    ):
        assert redact_secrets(text) == (text, 0), text


def test_stripe_keys_are_redacted():
    for prefix in ("sk_live_", "sk_test_", "rk_live_"):
        assert redact_secrets(f"stripe {prefix}{_B62[:24]} ok") == ("stripe [redacted] ok", 1), prefix


def test_aws_temporary_key_is_redacted():
    assert redact_secrets("creds ASIA" + "Y34FZKBOKMUTVV7A" + " expire") == ("creds [redacted] expire", 1)


def test_asia_words_survive():
    for text in ("ASIAPACIFICREGION", "deploy to ASIAPACIFICSOUTHEASTX", "ASIA region"):
        assert redact_secrets(text) == (text, 0), text


def test_new_patterns_are_idempotent():
    text = ("t gho_" + _B62 + " u postgres://u:s3cretPw@h/d MY_SERVICE_TOKEN=tk_9f8e7d6c5b4a3210 "
            "s sk_live_" + _B62[:24])
    once, n = redact_secrets(text)
    assert n == 4
    assert redact_secrets(once) == (once, 0)
