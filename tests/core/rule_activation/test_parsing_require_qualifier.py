from mnemo.core.rule_activation.parsing import parse_enforce_block


def test_bare_deny_command_without_pattern_rejected():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_command": "git push",
            "reason": "Don't push",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert parsed is None
    assert err is not None
    assert "qualifier" in err.lower() or "deny_pattern" in err


def test_deny_command_with_pattern_accepted():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_command": "git commit",
            "deny_pattern": "Co-Authored-By",
            "reason": "No coauthor trailers",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert err is None
    assert parsed is not None
    assert parsed["deny_commands"] == ["git commit"]
    assert parsed["deny_patterns"] == ["Co-Authored-By"]


def test_deny_pattern_alone_still_accepted():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_pattern": "rm -rf /",
            "reason": "Don't wipe root",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert err is None
    assert parsed is not None


def test_bare_deny_command_list_also_rejected():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_command": ["git push", "git commit"],
            "reason": "nope",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert parsed is None
    assert err is not None
