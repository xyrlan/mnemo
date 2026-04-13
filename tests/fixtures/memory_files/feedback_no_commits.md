---
name: No automatic commits
description: Don't create git commits without explicit user approval
type: feedback
---

Do not run `git commit` unless the user explicitly asks for a commit. Editing files is fine; committing them is not.

**Why:** The user reviews changes before committing to avoid polluting history.

**How to apply:** Make file edits freely; stop before `git commit`; wait for the user to say "commit this".
