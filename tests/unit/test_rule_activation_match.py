"""Unit tests for normalize_bash_command, match_bash_enforce, match_path_enrich."""
from __future__ import annotations

from mnemo.core.rule_activation import (
    EnforceHit,
    EnrichHit,
    _glob_matches,
    _glob_to_regex,
    match_bash_enforce,
    match_path_enrich,
    normalize_bash_command,
    parse_activates_on_block,
)


# ---------------------------------------------------------------------------
# Helpers: build minimal index dicts
# ---------------------------------------------------------------------------


def _make_index(
    enforce_rules: dict[str, list[dict]] | None = None,
    enrich_rules: dict[str, list[dict]] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "enforce_by_project": enforce_rules or {},
        "enrich_by_project": enrich_rules or {},
    }


def _enforce_rule(
    slug: str = "test-rule",
    deny_patterns: list[str] | None = None,
    deny_commands: list[str] | None = None,
    reason: str = "test reason",
) -> dict:
    return {
        "slug": slug,
        "tool": "Bash",
        "deny_patterns": deny_patterns or [],
        "deny_commands": deny_commands or [],
        "reason": reason,
        "source_files": [],
        "source_count": 1,
    }


def _enrich_rule(
    slug: str = "enrich-rule",
    tools: list[str] | None = None,
    path_globs: list[str] | None = None,
    source_count: int = 1,
    preview: str = "preview text",
) -> dict:
    return {
        "slug": slug,
        "tools": tools or ["Edit", "Write", "MultiEdit"],
        "path_globs": path_globs or ["**/*.py"],
        "topic_tags": [],
        "rule_body_preview": preview,
        "source_files": [],
        "source_count": source_count,
    }


# ---------------------------------------------------------------------------
# normalize_bash_command
# ---------------------------------------------------------------------------


def test_match_normalize_strips_leading_sudo():
    assert normalize_bash_command("sudo git commit -m msg") == "git commit -m msg"


def test_match_normalize_strips_sudo_with_flags():
    assert normalize_bash_command("sudo -E git commit -m msg") == "git commit -m msg"


def test_match_normalize_strips_env_var_prefix():
    """env FOO=bar git commit → git commit."""
    assert normalize_bash_command("env FOO=bar git commit") == "git commit"


def test_match_normalize_strips_env_multiple_vars():
    """env A=1 B=2 git push → git push."""
    result = normalize_bash_command("env A=1 B=2 git push")
    assert result == "git push"


def test_match_normalize_strips_shell_inline_env():
    """FOO=bar git commit → git commit."""
    assert normalize_bash_command("FOO=bar git commit") == "git commit"


def test_match_normalize_strips_multiple_inline_env():
    """FOO=bar BAZ=qux git commit → git commit."""
    assert normalize_bash_command("FOO=bar BAZ=qux git commit") == "git commit"


def test_match_normalize_lowercases_and_collapses_whitespace():
    """Multiple spaces and uppercase → lowercase, single space."""
    result = normalize_bash_command("GIT   COMMIT   -m   msg")
    assert result == "git commit -m msg"


def test_match_normalize_plain_command_unchanged():
    """A plain lowercase command with single spaces → unchanged."""
    assert normalize_bash_command("git push origin main") == "git push origin main"


def test_match_normalize_empty_string():
    assert normalize_bash_command("") == ""


def test_match_normalize_strips_double_sudo():
    """`sudo sudo git push` must collapse to `git push` (iterative strip)."""
    assert normalize_bash_command("sudo sudo git push") == "git push"


def test_match_normalize_strips_chained_env():
    """`env A=1 env B=2 git push` must collapse to `git push`."""
    assert normalize_bash_command("env A=1 env B=2 git push") == "git push"


def test_match_normalize_strips_sudo_with_user_flag():
    """`sudo -u root git push` must NOT leave `root` as the apparent command."""
    assert normalize_bash_command("sudo -u root git push") == "git push"


def test_match_normalize_strips_sudo_with_flag_then_env():
    """`sudo -E env FOO=1 git push` must collapse to `git push`."""
    assert normalize_bash_command("sudo -E env FOO=1 git push") == "git push"


def test_match_bash_enforce_double_sudo_bypass_now_hits():
    """Integration: `sudo sudo git push --force` hits deny_command `git push --force`."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_commands=["git push --force"],
                    reason="no force push",
                )
            ]
        }
    )
    hit = match_bash_enforce(index, "proj", "sudo sudo git push --force origin main")
    assert hit is not None
    assert hit.reason == "no force push"


def test_match_bash_enforce_chained_env_bypass_now_hits():
    """Integration: `env A=1 env B=2 git push --force` hits deny."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_commands=["git push --force"],
                    reason="no force push",
                )
            ]
        }
    )
    hit = match_bash_enforce(index, "proj", "env A=1 env B=2 git push --force")
    assert hit is not None


def test_match_bash_enforce_sudo_user_flag_bypass_now_hits():
    """Integration: `sudo -u root git push --force` hits deny."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_commands=["git push --force"],
                    reason="no force push",
                )
            ]
        }
    )
    hit = match_bash_enforce(index, "proj", "sudo -u root git push --force origin main")
    assert hit is not None


# ---------------------------------------------------------------------------
# match_bash_enforce
# ---------------------------------------------------------------------------


def test_match_bash_enforce_hits_co_authored_by_pattern():
    """Pattern matching 'Co-Authored-By' in a git commit command fires."""
    index = _make_index(
        enforce_rules={
            "mnemo": [
                _enforce_rule(
                    slug="no-co-authored-by",
                    deny_patterns=["git commit.*Co-Authored-By"],
                    reason="No Co-Authored-By trailers",
                )
            ]
        }
    )
    command = 'git commit -m "Fix bug\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'
    hit = match_bash_enforce(index, "mnemo", command)
    assert hit is not None
    assert isinstance(hit, EnforceHit)
    assert hit.slug == "no-co-authored-by"
    assert hit.project == "mnemo"


def test_match_bash_enforce_case_insensitive():
    """Pattern matching is case-insensitive (re.IGNORECASE)."""
    index = _make_index(
        enforce_rules={
            "mnemo": [
                _enforce_rule(
                    deny_patterns=["co-authored-by"],
                    reason="No co-authored trailers",
                )
            ]
        }
    )
    # Uppercase in command — should still match
    hit = match_bash_enforce(index, "mnemo", "git commit -m 'msg\n\nCO-AUTHORED-BY: x'")
    assert hit is not None


def test_match_bash_enforce_deny_command_prefix_match():
    """deny_command uses exact prefix matching on the normalised command."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_commands=["bun drizzle-kit generate"],
                    reason="No drizzle gen",
                )
            ]
        }
    )
    hit = match_bash_enforce(index, "proj", "bun drizzle-kit generate")
    assert hit is not None


def test_match_bash_enforce_deny_command_with_flags():
    """Command with extra flags after prefix still matches."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_commands=["bun drizzle-kit generate"],
                    reason="No drizzle gen",
                )
            ]
        }
    )
    hit = match_bash_enforce(index, "proj", "bun drizzle-kit generate --force")
    assert hit is not None


def test_match_bash_enforce_deny_command_no_partial_word_match():
    """deny_command prefix match requires a word boundary (space after prefix)."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_commands=["git push"],
                    reason="No push",
                )
            ]
        }
    )
    # "git pusher" should NOT match "git push" prefix (no space after)
    hit = match_bash_enforce(index, "proj", "git pusher origin")
    assert hit is None


def test_match_bash_enforce_ignores_other_project_rules():
    """Project A rule does NOT fire when called with project B."""
    index = _make_index(
        enforce_rules={
            "project-a": [
                _enforce_rule(
                    deny_patterns=["git push --force"],
                    reason="No force",
                )
            ]
        }
    )
    hit = match_bash_enforce(index, "project-b", "git push --force")
    assert hit is None


def test_match_bash_enforce_returns_first_hit():
    """When multiple rules match, the first one in list order is returned."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(slug="rule-first", deny_patterns=["git push"], reason="first"),
                _enforce_rule(slug="rule-second", deny_patterns=["git push"], reason="second"),
            ]
        }
    )
    hit = match_bash_enforce(index, "proj", "git push origin main")
    assert hit is not None
    assert hit.slug == "rule-first"


def test_match_bash_enforce_caps_command_at_4kb():
    """Commands longer than 4096 chars are truncated before matching."""
    # A marker placed strictly beyond the 4096 cap must not match.
    marker2 = "BEYONDCAP"
    long_prefix2 = "a" * 4096
    command2 = long_prefix2 + marker2  # marker starts at char 4096+

    index2 = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(
                    deny_patterns=[marker2],
                    reason="match beyond 4096",
                )
            ]
        }
    )
    # After capping at 4096, marker2 should not be present → no hit
    hit = match_bash_enforce(index2, "proj", command2)
    assert hit is None


def test_match_bash_enforce_no_match():
    """Command that matches no rule → None."""
    index = _make_index(
        enforce_rules={
            "proj": [
                _enforce_rule(deny_patterns=["rm -rf"], reason="no delete")
            ]
        }
    )
    assert match_bash_enforce(index, "proj", "git status") is None


def test_match_bash_enforce_empty_project():
    """No rules for project → None."""
    index = _make_index()
    assert match_bash_enforce(index, "unknown", "git push") is None


# ---------------------------------------------------------------------------
# match_path_enrich
# ---------------------------------------------------------------------------


def test_match_path_enrich_hits_nested_glob():
    """**/components/modals/** matches src/app/components/modals/user-modal.tsx."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(
                    path_globs=["**/components/modals/**"],
                    tools=["Edit"],
                )
            ]
        }
    )
    hits = match_path_enrich(
        index, "proj", "src/app/components/modals/user-modal.tsx", "Edit"
    )
    assert len(hits) == 1
    assert isinstance(hits[0], EnrichHit)


def test_match_path_enrich_hits_shallow_glob():
    """**/*modal*.tsx matches components/user-modal.tsx."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(
                    path_globs=["**/*modal*.tsx"],
                    tools=["Edit"],
                )
            ]
        }
    )
    hits = match_path_enrich(index, "proj", "components/user-modal.tsx", "Edit")
    assert len(hits) == 1


def test_match_path_enrich_hits_deeply_nested():
    """**/*modal*.tsx matches deeply nested path like a/b/c/d/user-modal.tsx."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(
                    path_globs=["**/*modal*.tsx"],
                    tools=["Write"],
                )
            ]
        }
    )
    hits = match_path_enrich(
        index, "proj", "src/app/features/auth/components/login-modal.tsx", "Write"
    )
    assert len(hits) == 1


def test_match_path_enrich_respects_tool_name_filter():
    """Rule declares tools {Edit, Write}, call with MultiEdit → no match."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(
                    path_globs=["**/*.tsx"],
                    tools=["Edit", "Write"],
                )
            ]
        }
    )
    hits = match_path_enrich(index, "proj", "src/component.tsx", "MultiEdit")
    assert hits == []


def test_match_path_enrich_orders_by_source_count_desc():
    """Rules with higher source_count appear first."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(slug="low-count", source_count=1, path_globs=["**/*.py"]),
                _enrich_rule(slug="high-count", source_count=5, path_globs=["**/*.py"]),
                _enrich_rule(slug="mid-count", source_count=3, path_globs=["**/*.py"]),
            ]
        }
    )
    hits = match_path_enrich(index, "proj", "src/main.py", "Edit")
    assert len(hits) == 3
    assert hits[0].slug == "high-count"
    assert hits[1].slug == "mid-count"
    assert hits[2].slug == "low-count"


def test_match_path_enrich_caps_at_3():
    """5 matching rules → only 3 returned."""
    rules = [
        _enrich_rule(slug=f"rule-{i}", source_count=i, path_globs=["**/*.py"])
        for i in range(5)
    ]
    index = _make_index(enrich_rules={"proj": rules})
    hits = match_path_enrich(index, "proj", "main.py", "Edit")
    assert len(hits) == 3


def test_match_path_enrich_ignores_other_project_rules():
    """Rules from another project don't match."""
    index = _make_index(
        enrich_rules={
            "project-a": [
                _enrich_rule(path_globs=["**/*.py"])
            ]
        }
    )
    hits = match_path_enrich(index, "project-b", "main.py", "Edit")
    assert hits == []


def test_match_path_enrich_no_match():
    """Non-matching path → empty list."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(path_globs=["**/*.tsx"])
            ]
        }
    )
    hits = match_path_enrich(index, "proj", "main.py", "Edit")
    assert hits == []


def test_match_path_enrich_slug_tiebreak():
    """When source_count is equal, rules are sorted by slug ascending."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(slug="z-rule", source_count=2, path_globs=["**/*.py"]),
                _enrich_rule(slug="a-rule", source_count=2, path_globs=["**/*.py"]),
            ]
        }
    )
    hits = match_path_enrich(index, "proj", "main.py", "Edit")
    assert hits[0].slug == "a-rule"
    assert hits[1].slug == "z-rule"


def test_glob_negated_class_excludes_dotfiles():
    """`[!.]*` should match `foo.py` (starts with non-dot) and NOT `.hidden`."""
    assert _glob_matches("[!.]*", "foo.py") is True
    assert _glob_matches("[!.]*", ".hidden") is False


def test_glob_character_class_basic():
    """`[abc]*.py` matches `apple.py` but not `zebra.py`."""
    assert _glob_matches("[abc]*.py", "apple.py") is True
    assert _glob_matches("[abc]*.py", "zebra.py") is False


def test_glob_unterminated_bracket_rejected_at_parse_time():
    """An unterminated bracket like `foo[` must be rejected at parse time."""
    fm = {"activates_on": {"tools": ["Edit"], "path_globs": ["foo["]}}
    assert parse_activates_on_block(fm) is None


def test_glob_unterminated_bracket_in_build_index_malformed(tmp_vault):
    """A rule with an unterminated glob lands in `malformed`, not `enrich_by_project`."""
    from mnemo.core.rule_activation import build_index

    content = (
        "---\n"
        "name: bad-glob-rule\n"
        "stability: stable\n"
        "tags:\n  - auto-promoted\n"
        "sources:\n  - bots/mnemo/memory/bad-glob-rule.md\n"
        "activates_on:\n"
        "  tools: [Edit]\n"
        "  path_globs: ['foo[']\n"
        "---\n\nbody\n"
    )
    target_dir = tmp_vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "bad-glob-rule.md").write_text(content, encoding="utf-8")

    index = build_index(tmp_vault)
    malformed_paths = [e["path"] for e in index["malformed"]]
    assert any("bad-glob-rule.md" in p for p in malformed_paths)
    # Error message should reference the invalid glob.
    errors = [e["error"] for e in index["malformed"]]
    assert any("path_glob invalid" in err for err in errors)
    # And must NOT appear in enrich_by_project.
    assert not index["enrich_by_project"]


def test_glob_to_regex_returns_none_for_unterminated():
    """Direct _glob_to_regex contract: unterminated bracket returns None."""
    assert _glob_to_regex("foo[") is None


def test_match_path_enrich_glob_no_double_star_matches_single_segment():
    """A pattern without ** does not match across directory separators."""
    index = _make_index(
        enrich_rules={
            "proj": [
                _enrich_rule(
                    path_globs=["*.py"],  # no **, only matches root-level .py
                    tools=["Edit"],
                )
            ]
        }
    )
    # src/main.py has a directory prefix — plain *.py should not match
    hits_nested = match_path_enrich(index, "proj", "src/main.py", "Edit")
    hits_flat = match_path_enrich(index, "proj", "main.py", "Edit")
    # Flat match should work (fnmatch *.py matches main.py)
    assert len(hits_flat) == 1
    # Nested should NOT match with plain *.py
    assert len(hits_nested) == 0
