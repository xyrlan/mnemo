---
name: jest-snapshot
description: Use jest snapshot tests sparingly and only for stable serialisable output
tags:
  - jest
  - testing
  - snapshot
aliases:
  - snapshot
  - inline-snapshot
sources:
  - bots/projA/memory/jest-snapshot.md
  - bots/projB/memory/snapshot-tests.md
stability: stable
---
Reach for a jest snapshot only when the output under test is stable and readable. A jest snapshot over a giant dom tree or a multi-screen api payload is a landmine: every innocent refactor churns the snapshot and reviewers learn to rubber-stamp snapshot updates. Prefer targeted jest assertions on specific fields. Inline snapshots keep the expected value next to the jest test, which makes the failure obvious in code review. Never commit a jest snapshot that was updated without reading the diff — that is how regressions get blessed.
