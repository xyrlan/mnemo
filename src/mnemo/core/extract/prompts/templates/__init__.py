"""Prompt template modules — system prompts, schema, and few-shot examples.

Split out of the legacy ``mnemo.core.extract.prompts`` monolith in v0.9
PR F2. New code should import from these concrete sub-modules; the
parent package's ``__init__.py`` keeps the pre-v0.9 surface alive via
re-exports.
"""
