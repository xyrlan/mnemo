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


def _rules_entry_with_enforce(
    slug: str = "test-rule",
    *,
    deny_patterns: list[str] | None = None,
    deny_commands: list[str] | None = None,
    reason: str = "test reason",
    project: str = "proj",
) -> dict:
    return {
        "type": "feedback",
        "name": slug,
        "topic_tags": [],
        "source_files": [],
        "source_count": 1,
        "projects": [project],
        "universal": False,
        "body_preview": "",
        "enforce": {
            "slug": slug,
            "tool": "Bash",
            "deny_patterns": deny_patterns or [],
            "deny_commands": deny_commands or [],
            "reason": reason,
            "source_files": [],
            "source_count": 1,
        },
        "activates_on": None,
    }


def _rules_entry_with_enrich(
    slug: str = "enrich-rule",
    *,
    tools: list[str] | None = None,
    path_globs: list[str] | None = None,
    source_count: int = 1,
    preview: str = "preview text",
    project: str = "proj",
) -> dict:
    return {
        "type": "feedback",
        "name": slug,
        "topic_tags": [],
        "source_files": [],
        "source_count": source_count,
        "projects": [project],
        "universal": False,
        "body_preview": preview,
        "enforce": None,
        "activates_on": {
            "slug": slug,
            "tools": tools or ["Edit", "Write", "MultiEdit"],
            "path_globs": path_globs or ["**/*.py"],
            "topic_tags": [],
            "rule_body_preview": preview,
            "source_files": [],
            "source_count": source_count,
        },
    }


def _make_index(
    rules: dict[str, dict] | None = None,
    by_project: dict[str, dict] | None = None,
    universal: dict | None = None,
) -> dict:
    return {
        "schema_version": 2,
        "rules": rules or {},
        "by_project": by_project or {},
        "universal": universal or {"slugs": [], "topics": []},
        "malformed": [],
    }


def _make_enforce_index(proj: str, *rule_entries: dict) -> dict:
    """Build a v2 index from one or more enforce rule entries for *proj*."""
    rules = {e["name"]: e for e in rule_entries}
    local_slugs = list(rules.keys())
    return _make_index(
        rules=rules,
        by_project={proj: {"local_slugs": local_slugs, "topics": []}},
    )


def _make_enrich_index(proj: str, *rule_entries: dict) -> dict:
    """Build a v2 index from one or more enrich rule entries for *proj*."""
    rules = {e["name"]: e for e in rule_entries}
    local_slugs = list(rules.keys())
    return _make_index(
        rules=rules,
        by_project={proj: {"local_slugs": local_slugs, "topics": []}},
    )


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
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_commands=["git push --force"],
            reason="no force push",
        ),
    )
    hit = match_bash_enforce(index, "proj", "sudo sudo git push --force origin main")
    assert hit is not None
    assert hit.reason == "no force push"


def test_match_bash_enforce_chained_env_bypass_now_hits():
    """Integration: `env A=1 env B=2 git push --force` hits deny."""
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_commands=["git push --force"],
            reason="no force push",
        ),
    )
    hit = match_bash_enforce(index, "proj", "env A=1 env B=2 git push --force")
    assert hit is not None


def test_match_bash_enforce_sudo_user_flag_bypass_now_hits():
    """Integration: `sudo -u root git push --force` hits deny."""
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_commands=["git push --force"],
            reason="no force push",
        ),
    )
    hit = match_bash_enforce(index, "proj", "sudo -u root git push --force origin main")
    assert hit is not None


# ---------------------------------------------------------------------------
# match_bash_enforce
# ---------------------------------------------------------------------------


def test_match_bash_enforce_hits_co_authored_by_pattern():
    """Pattern matching 'Co-Authored-By' in a git commit command fires."""
    index = _make_enforce_index(
        "mnemo",
        _rules_entry_with_enforce(
            "no-co-authored-by",
            deny_patterns=["git commit.*Co-Authored-By"],
            reason="No Co-Authored-By trailers",
            project="mnemo",
        ),
    )
    command = 'git commit -m "Fix bug\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'
    hit = match_bash_enforce(index, "mnemo", command)
    assert hit is not None
    assert isinstance(hit, EnforceHit)
    assert hit.slug == "no-co-authored-by"
    assert hit.project == "mnemo"


def test_match_bash_enforce_case_insensitive():
    """Pattern matching is case-insensitive (re.IGNORECASE)."""
    index = _make_enforce_index(
        "mnemo",
        _rules_entry_with_enforce(
            deny_patterns=["co-authored-by"],
            reason="No co-authored trailers",
            project="mnemo",
        ),
    )
    # Uppercase in command — should still match
    hit = match_bash_enforce(index, "mnemo", "git commit -m 'msg\n\nCO-AUTHORED-BY: x'")
    assert hit is not None


def test_match_bash_enforce_deny_command_prefix_match():
    """deny_command uses exact prefix matching on the normalised command."""
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_commands=["bun drizzle-kit generate"],
            reason="No drizzle gen",
        ),
    )
    hit = match_bash_enforce(index, "proj", "bun drizzle-kit generate")
    assert hit is not None


def test_match_bash_enforce_deny_command_with_flags():
    """Command with extra flags after prefix still matches."""
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_commands=["bun drizzle-kit generate"],
            reason="No drizzle gen",
        ),
    )
    hit = match_bash_enforce(index, "proj", "bun drizzle-kit generate --force")
    assert hit is not None


def test_match_bash_enforce_deny_command_no_partial_word_match():
    """deny_command prefix match requires a word boundary (space after prefix)."""
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_commands=["git push"],
            reason="No push",
        ),
    )
    # "git pusher" should NOT match "git push" prefix (no space after)
    hit = match_bash_enforce(index, "proj", "git pusher origin")
    assert hit is None


def test_match_bash_enforce_ignores_other_project_rules():
    """Project A rule does NOT fire when called with project B."""
    index = _make_enforce_index(
        "project-a",
        _rules_entry_with_enforce(
            deny_patterns=["git push --force"],
            reason="No force",
            project="project-a",
        ),
    )
    hit = match_bash_enforce(index, "project-b", "git push --force")
    assert hit is None


def test_match_bash_enforce_returns_first_hit():
    """When multiple rules match, the first one in list order is returned."""
    r1 = _rules_entry_with_enforce("rule-first", deny_patterns=["git push"], reason="first")
    r2 = _rules_entry_with_enforce("rule-second", deny_patterns=["git push"], reason="second")
    # local_slugs order determines match priority; put rule-first first
    index = _make_index(
        rules={"rule-first": r1, "rule-second": r2},
        by_project={"proj": {"local_slugs": ["rule-first", "rule-second"], "topics": []}},
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

    index2 = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(
            deny_patterns=[marker2],
            reason="match beyond 4096",
        ),
    )
    # After capping at 4096, marker2 should not be present → no hit
    hit = match_bash_enforce(index2, "proj", command2)
    assert hit is None


def test_match_bash_enforce_no_match():
    """Command that matches no rule → None."""
    index = _make_enforce_index(
        "proj",
        _rules_entry_with_enforce(deny_patterns=["rm -rf"], reason="no delete"),
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
    index = _make_enrich_index(
        "proj",
        _rules_entry_with_enrich(
            path_globs=["**/components/modals/**"],
            tools=["Edit"],
        ),
    )
    hits = match_path_enrich(
        index, "proj", "src/app/components/modals/user-modal.tsx", "Edit"
    )
    assert len(hits) == 1
    assert isinstance(hits[0], EnrichHit)


def test_match_path_enrich_hits_shallow_glob():
    """**/*modal*.tsx matches components/user-modal.tsx."""
    index = _make_enrich_index(
        "proj",
        _rules_entry_with_enrich(
            path_globs=["**/*modal*.tsx"],
            tools=["Edit"],
        ),
    )
    hits = match_path_enrich(index, "proj", "components/user-modal.tsx", "Edit")
    assert len(hits) == 1


def test_match_path_enrich_hits_deeply_nested():
    """**/*modal*.tsx matches deeply nested path like a/b/c/d/user-modal.tsx."""
    index = _make_enrich_index(
        "proj",
        _rules_entry_with_enrich(
            path_globs=["**/*modal*.tsx"],
            tools=["Write"],
        ),
    )
    hits = match_path_enrich(
        index, "proj", "src/app/features/auth/components/login-modal.tsx", "Write"
    )
    assert len(hits) == 1


def test_match_path_enrich_respects_tool_name_filter():
    """Rule declares tools {Edit, Write}, call with MultiEdit → no match."""
    index = _make_enrich_index(
        "proj",
        _rules_entry_with_enrich(
            path_globs=["**/*.tsx"],
            tools=["Edit", "Write"],
        ),
    )
    hits = match_path_enrich(index, "proj", "src/component.tsx", "MultiEdit")
    assert hits == []


def test_match_path_enrich_orders_by_source_count_desc():
    """Rules with higher source_count appear first."""
    r_low = _rules_entry_with_enrich("low-count", source_count=1, path_globs=["**/*.py"])
    r_high = _rules_entry_with_enrich("high-count", source_count=5, path_globs=["**/*.py"])
    r_mid = _rules_entry_with_enrich("mid-count", source_count=3, path_globs=["**/*.py"])
    index = _make_index(
        rules={"low-count": r_low, "high-count": r_high, "mid-count": r_mid},
        by_project={"proj": {"local_slugs": ["low-count", "high-count", "mid-count"], "topics": []}},
    )
    hits = match_path_enrich(index, "proj", "src/main.py", "Edit")
    assert len(hits) == 3
    assert hits[0].slug == "high-count"
    assert hits[1].slug == "mid-count"
    assert hits[2].slug == "low-count"


def test_match_path_enrich_caps_at_3():
    """5 matching rules → only 3 returned."""
    rule_entries = [
        _rules_entry_with_enrich(f"rule-{i}", source_count=i, path_globs=["**/*.py"])
        for i in range(5)
    ]
    rules = {e["name"]: e for e in rule_entries}
    local_slugs = [e["name"] for e in rule_entries]
    index = _make_index(
        rules=rules,
        by_project={"proj": {"local_slugs": local_slugs, "topics": []}},
    )
    hits = match_path_enrich(index, "proj", "main.py", "Edit")
    assert len(hits) == 3


def test_match_path_enrich_ignores_other_project_rules():
    """Rules from another project don't match."""
    index = _make_enrich_index(
        "project-a",
        _rules_entry_with_enrich(path_globs=["**/*.py"], project="project-a"),
    )
    hits = match_path_enrich(index, "project-b", "main.py", "Edit")
    assert hits == []


def test_match_path_enrich_no_match():
    """Non-matching path → empty list."""
    index = _make_enrich_index(
        "proj",
        _rules_entry_with_enrich(path_globs=["**/*.tsx"]),
    )
    hits = match_path_enrich(index, "proj", "main.py", "Edit")
    assert hits == []


def test_match_path_enrich_slug_tiebreak():
    """When source_count is equal, rules are sorted by slug ascending."""
    r_z = _rules_entry_with_enrich("z-rule", source_count=2, path_globs=["**/*.py"])
    r_a = _rules_entry_with_enrich("a-rule", source_count=2, path_globs=["**/*.py"])
    index = _make_index(
        rules={"z-rule": r_z, "a-rule": r_a},
        by_project={"proj": {"local_slugs": ["z-rule", "a-rule"], "topics": []}},
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
    # And must NOT appear in rules with activates_on.
    assert all(rule.get("activates_on") is None for rule in index["rules"].values())


def test_glob_to_regex_returns_none_for_unterminated():
    """Direct _glob_to_regex contract: unterminated bracket returns None."""
    assert _glob_to_regex("foo[") is None


def test_match_path_enrich_glob_no_double_star_matches_single_segment():
    """A pattern without ** does not match across directory separators."""
    index = _make_enrich_index(
        "proj",
        _rules_entry_with_enrich(
            path_globs=["*.py"],  # no **, only matches root-level .py
            tools=["Edit"],
        ),
    )
    # src/main.py has a directory prefix — plain *.py should not match
    hits_nested = match_path_enrich(index, "proj", "src/main.py", "Edit")
    hits_flat = match_path_enrich(index, "proj", "main.py", "Edit")
    # Flat match should work (fnmatch *.py matches main.py)
    assert len(hits_flat) == 1
    # Nested should NOT match with plain *.py
    assert len(hits_nested) == 0


# ---------------------------------------------------------------------------
# log_enrichment: tool_name persistence (regression for v0.5.1 polish)
# ---------------------------------------------------------------------------


def test_log_enrichment_persists_tool_name_field(tmp_path):
    """Regression: log_enrichment must write the tool_name passed by the hook,
    not read it from tool_input (which never has that key)."""
    import json as _json
    from mnemo.core.rule_activation import EnrichHit, log_enrichment

    hit = EnrichHit(slug="my-rule", project="myproj", rule_body_preview="body")
    tool_input = {"file_path": "/tmp/foo.tsx"}

    log_enrichment(tmp_path, [hit], "Edit", tool_input)

    log_path = tmp_path / ".mnemo" / "enrichment-log.jsonl"
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip()
    entry = _json.loads(line)

    assert entry["tool_name"] == "Edit"
    assert entry["file_path"] == "/tmp/foo.tsx"
    assert entry["project"] == "myproj"
    assert entry["hit_slugs"] == ["my-rule"]


def test_log_enrichment_persists_multiedit_tool_name(tmp_path):
    """tool_name must be whatever the hook passes — Write, MultiEdit, not just Edit."""
    import json as _json
    from mnemo.core.rule_activation import EnrichHit, log_enrichment

    hit = EnrichHit(slug="other", project="p", rule_body_preview="b")
    log_enrichment(tmp_path, [hit], "MultiEdit", {"file_path": "/x.md"})

    line = (tmp_path / ".mnemo" / "enrichment-log.jsonl").read_text("utf-8").strip()
    assert _json.loads(line)["tool_name"] == "MultiEdit"


def test_log_enrichment_never_raises_on_bad_vault(tmp_path):
    """log_enrichment must swallow OSError — fail-open contract."""
    from mnemo.core.rule_activation import EnrichHit, log_enrichment

    # Pass a path that can't be written to (use a file as if it were a dir)
    bad_vault = tmp_path / "file-not-dir"
    bad_vault.write_text("not a dir", encoding="utf-8")
    hit = EnrichHit(slug="s", project="p", rule_body_preview="b")

    # Must return cleanly, no exception
    log_enrichment(bad_vault, [hit], "Edit", {"file_path": "/x"})


# ---------------------------------------------------------------------------
# v2 layout tests (Task 5a)
# ---------------------------------------------------------------------------


def test_match_bash_enforce_reads_v2_layout(tmp_vault):
    """Match helper must iterate by_project[proj].local_slugs + universal.slugs,
    reading the enforce block from rules[slug]."""
    from mnemo.core.rule_activation import (
        build_index, match_bash_enforce, INDEX_VERSION,
    )
    from tests.unit.test_rule_activation_index import _write_rule

    _write_rule(
        tmp_vault,
        "feedback_no_force.md",
        name="no-force-push",
        sources=["bots/mnemo/memory/g.md"],
        enforce=(
            "enforce:\n"
            "  tool: Bash\n"
            "  deny_pattern: git push --force\n"
            "  reason: never force push\n"
        ),
    )
    idx = build_index(tmp_vault)
    assert idx["schema_version"] == INDEX_VERSION
    hit = match_bash_enforce(idx, "mnemo", "git push --force origin main")
    assert hit is not None
    assert hit.slug == "no-force-push"


def test_match_path_enrich_reads_v2_layout(tmp_vault):
    from mnemo.core.rule_activation import build_index, match_path_enrich
    from tests.unit.test_rule_activation_index import _write_rule

    _write_rule(
        tmp_vault,
        "feedback_py_rule.md",
        name="python-style",
        sources=["bots/mnemo/memory/p.md"],
        activates_on=(
            "activates_on:\n"
            "  tools:\n"
            "    - Edit\n"
            "  path_globs:\n"
            "    - \"**/*.py\"\n"
        ),
    )
    idx = build_index(tmp_vault)
    hits = match_path_enrich(idx, "mnemo", "src/foo/bar.py", "Edit")
    assert len(hits) == 1
    assert hits[0].slug == "python-style"


def test_index_version_is_2():
    from mnemo.core.rule_activation import INDEX_VERSION
    assert INDEX_VERSION == 2


