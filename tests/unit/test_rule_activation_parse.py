"""Unit tests for parse_enforce_block and parse_activates_on_block."""
from __future__ import annotations

from mnemo.core.rule_activation import parse_enforce_block, parse_activates_on_block


# ---------------------------------------------------------------------------
# parse_enforce_block
# ---------------------------------------------------------------------------


def _enforce_fm(**kwargs) -> dict:
    """Helper: wrap enforce kwargs into a frontmatter-shaped dict."""
    return {"enforce": kwargs}


def test_parse_enforce_valid_deny_pattern():
    """Single-pattern case returns a normalized dict."""
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern="git commit.*Co-Authored-By",
        reason="No Co-Authored-By trailers",
    )
    result = parse_enforce_block(fm)
    assert result is not None
    assert result["tool"] == "Bash"
    assert result["deny_patterns"] == ["git commit.*Co-Authored-By"]
    assert result["deny_commands"] == []
    assert result["reason"] == "No Co-Authored-By trailers"


def test_parse_enforce_valid_deny_command_list():
    """Only deny_command present returns a valid dict."""
    fm = _enforce_fm(
        tool="Bash",
        deny_command=["bun drizzle-kit generate", "bun drizzle-kit push"],
        reason="Do not run drizzle migrations directly",
    )
    result = parse_enforce_block(fm)
    assert result is not None
    assert result["deny_patterns"] == []
    assert result["deny_commands"] == ["bun drizzle-kit generate", "bun drizzle-kit push"]


def test_parse_enforce_valid_both():
    """Both deny_pattern AND deny_command can coexist."""
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern="git commit.*--amend",
        deny_command=["git push --force"],
        reason="No force operations",
    )
    result = parse_enforce_block(fm)
    assert result is not None
    assert len(result["deny_patterns"]) == 1
    assert len(result["deny_commands"]) == 1


def test_parse_enforce_requires_pattern_or_command():
    """Neither deny_pattern nor deny_command present → None."""
    fm = _enforce_fm(tool="Bash", reason="something")
    assert parse_enforce_block(fm) is None


def test_parse_enforce_rejects_uncompilable_regex():
    """Invalid regex pattern → None, no exception raised."""
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern="[unclosed",
        reason="bad pattern",
    )
    result = parse_enforce_block(fm)
    assert result is None


def test_parse_enforce_caps_pattern_length():
    """Pattern longer than 500 chars → None."""
    long_pattern = "x" * 501
    fm = _enforce_fm(tool="Bash", deny_pattern=long_pattern, reason="too long")
    assert parse_enforce_block(fm) is None


def test_parse_enforce_rejects_catastrophic_regex_heuristic():
    """Patterns matching catastrophic backtracking heuristics → None."""
    catastrophic_patterns = [
        "(.*)+" ,
        "(.+)+",
        "(.+)*",
        "(.*)*",
        ".*.*something",
    ]
    for pat in catastrophic_patterns:
        fm = _enforce_fm(tool="Bash", deny_pattern=pat, reason="catastrophic")
        assert parse_enforce_block(fm) is None, f"Should have rejected: {pat!r}"


def test_parse_enforce_rejects_non_bash_tool():
    """tool: Edit → None (only Bash is valid in v1)."""
    fm = _enforce_fm(tool="Edit", deny_pattern="some.*pattern", reason="wrong tool")
    assert parse_enforce_block(fm) is None


def test_parse_enforce_truncates_long_reason():
    """reason > 300 chars is silently truncated to 300 in the output."""
    long_reason = "x" * 400
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern="git commit",
        reason=long_reason,
    )
    result = parse_enforce_block(fm)
    assert result is not None
    assert len(result["reason"]) == 300


def test_parse_enforce_missing_tool_field():
    """tool field absent → None."""
    fm = {"enforce": {"deny_pattern": "git commit", "reason": "no tool"}}
    assert parse_enforce_block(fm) is None


def test_parse_enforce_missing_enforce_block():
    """No enforce key in frontmatter → None."""
    assert parse_enforce_block({}) is None
    assert parse_enforce_block({"name": "foo"}) is None


def test_parse_enforce_rejects_redos_pattern_via_timing_probe():
    """A pattern that bypasses the substring heuristic but hangs at match time
    (e.g. `(a+)+b`) MUST be rejected by the empirical timing probe."""
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern="(a+)+b",
        reason="bypasses substring heuristic",
    )
    assert parse_enforce_block(fm) is None


def test_parse_enforce_accepts_benign_pattern_under_probe():
    """A benign pattern that the probe can evaluate quickly must parse."""
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern="git commit.*Co-Authored-By",
        reason="no co-authored trailers",
    )
    result = parse_enforce_block(fm)
    assert result is not None
    assert result["deny_patterns"] == ["git commit.*Co-Authored-By"]


def test_parse_enforce_list_of_patterns():
    """deny_pattern can be a list of strings."""
    fm = _enforce_fm(
        tool="Bash",
        deny_pattern=["git commit.*Co-Authored-By", "git push --force"],
        reason="Two patterns",
    )
    result = parse_enforce_block(fm)
    assert result is not None
    assert len(result["deny_patterns"]) == 2


# ---------------------------------------------------------------------------
# parse_activates_on_block
# ---------------------------------------------------------------------------


def _activates_fm(**kwargs) -> dict:
    return {"activates_on": kwargs}


def test_parse_activates_on_valid_globs():
    """Happy path: valid tools and path_globs."""
    fm = _activates_fm(
        tools=["Edit", "Write"],
        path_globs=["**/*modal*.tsx", "src/components/**"],
    )
    result = parse_activates_on_block(fm)
    assert result is not None
    assert set(result["tools"]) == {"Edit", "Write"}
    assert "**/*modal*.tsx" in result["path_globs"]


def test_parse_activates_on_rejects_empty_globs():
    """Empty path_globs list → None."""
    fm = _activates_fm(tools=["Edit"], path_globs=[])
    assert parse_activates_on_block(fm) is None


def test_parse_activates_on_rejects_unknown_tool():
    """tools: [Bash] → None (Bash is not a valid enrich tool)."""
    fm = _activates_fm(tools=["Bash"], path_globs=["**/*.py"])
    assert parse_activates_on_block(fm) is None


def test_parse_activates_on_rejects_mixed_valid_invalid():
    """If any tool is unknown, the whole block is rejected."""
    fm = _activates_fm(tools=["Edit", "Bash"], path_globs=["**/*.py"])
    assert parse_activates_on_block(fm) is None


def test_parse_activates_on_rejects_missing_tools():
    """No tools field → None."""
    fm = _activates_fm(path_globs=["**/*.py"])
    assert parse_activates_on_block(fm) is None


def test_parse_activates_on_rejects_empty_tools_list():
    """Empty tools list → None."""
    fm = _activates_fm(tools=[], path_globs=["**/*.py"])
    assert parse_activates_on_block(fm) is None


def test_parse_activates_on_rejects_missing_activates_block():
    """No activates_on key → None."""
    assert parse_activates_on_block({}) is None
    assert parse_activates_on_block({"name": "foo"}) is None


def test_parse_activates_on_all_valid_tools():
    """All three valid tool names are accepted."""
    fm = _activates_fm(
        tools=["Edit", "Write", "MultiEdit"],
        path_globs=["**/*.ts"],
    )
    result = parse_activates_on_block(fm)
    assert result is not None
    assert set(result["tools"]) == {"Edit", "Write", "MultiEdit"}


def test_parse_both_blocks_coexist_independently():
    """Frontmatter has both enforce and activates_on; parse each separately."""
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_pattern": "git commit.*--force",
            "reason": "No force commits",
        },
        "activates_on": {
            "tools": ["Edit", "Write"],
            "path_globs": ["**/*.tsx"],
        },
    }
    enforce_result = parse_enforce_block(fm)
    enrich_result = parse_activates_on_block(fm)

    assert enforce_result is not None
    assert enrich_result is not None
    assert enforce_result["tool"] == "Bash"
    assert "Edit" in enrich_result["tools"]
