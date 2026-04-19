---
name: pnpm-workspace
description: Use pnpm workspace protocol workspace:* to link local packages in a monorepo
tags:
  - pnpm
  - monorepo
  - workspace
aliases:
  - workspace
  - monorepo
sources:
  - bots/projA/memory/pnpm-workspace.md
  - bots/projB/memory/monorepo.md
stability: stable
---
In a pnpm monorepo, reference sibling packages with the workspace protocol: "workspace:*" in each package json. Pnpm rewrites the workspace specifier to a real version at publish time, so consumers never see the workspace marker. Plain version ranges in a pnpm workspace silently fall back to the registry and defeat the whole monorepo wiring. Keep one pnpm workspace yaml at the root that lists every package path and filter scripts through pnpm --filter to target a single workspace. Avoid mixing npm and pnpm in the same monorepo; the lockfiles will fight each other.
