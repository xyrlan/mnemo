---
name: No commit without permission
description: Never create git commits without explicit approval
type: feedback
---

Never create a git commit unless the user explicitly asks. This includes scenarios where a task feels "done" — still wait for the go-ahead.

**Why:** The user has been burned by automated commits polluting history and wants manual control.

**How to apply:** When a task completes, describe the changes and wait. Do not run `git commit` on your own.
