from mnemo.core.extract.prompts.templates.system_feedback import FEEDBACK_SYSTEM_PROMPT


def test_prompt_names_the_blocking_intent_requirement():
    # The prompt MUST tell the LLM that enforce: requires the source briefing
    # to use explicit blocking language (never allow / always refuse / hook
    # should block). Mentioning a command in backticks is NOT enough.
    assert "explicit" in FEEDBACK_SYSTEM_PROMPT.lower()
    assert any(kw in FEEDBACK_SYSTEM_PROMPT.lower() for kw in ("never allow", "always refuse", "hook should"))


def test_prompt_requires_deny_pattern_qualifier():
    # The prompt MUST require deny_pattern when deny_command is used, to
    # match the schema validator from Task 1.
    assert "deny_pattern" in FEEDBACK_SYSTEM_PROMPT
    assert "qualifier" in FEEDBACK_SYSTEM_PROMPT.lower() or "narrow" in FEEDBACK_SYSTEM_PROMPT.lower()


def test_prompt_defaults_to_omission():
    # "When in doubt, omit" (or equivalent) must be preserved.
    assert "omit" in FEEDBACK_SYSTEM_PROMPT.lower()
