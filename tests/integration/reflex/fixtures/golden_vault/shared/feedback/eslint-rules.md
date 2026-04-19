---
name: eslint-rules
description: Extend a shared eslint config and override only project-specific rules
tags:
  - eslint
  - linting
  - javascript
aliases:
  - lint
  - linter
sources:
  - bots/projA/memory/eslint-config.md
  - bots/projB/memory/lint-rules.md
stability: stable
---
Keep a single shared eslint config package that every repo extends. Project-specific eslint rules should be overrides layered on top, not a fresh rule set per repo. A shared eslint baseline keeps style drift low, and a central eslint rule change rolls out to every project on the next dependency bump. Avoid disabling eslint rules file-by-file — when an eslint rule is genuinely wrong for a project, disable it once in the project eslint config with a comment explaining the tradeoff. Never commit eslint disable comments without a reason.
