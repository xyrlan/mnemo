---
name: Use yarn as package manager
description: Prefer yarn over npm for all JavaScript/TypeScript projects
type: feedback
---

Always use yarn for dependency management in this project. Never run `npm install`, `npm add`, or any npm command.

**Why:** The project's lockfile is `yarn.lock`; mixing package managers leads to inconsistent dependency resolution.

**How to apply:** When adding dependencies, use `yarn add <pkg>` or `yarn add -D <pkg>`. Never run npm commands.
