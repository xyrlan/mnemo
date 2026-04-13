---
name: China Portal Design Decisions
description: Key architectural decisions for the China Operator Portal
type: project
---

Phase 3 China Operator Portal design decisions (agreed 2026-03-30):

1. **Timezone-aware crons**: Inngest crons must use `Asia/Shanghai`.
2. **Sourcing chat instead of notes**: Replace `notes` field with `sourcingComments` table.

**Why:** These decisions came from the user's domain expertise in Chinese import operations.

**How to apply:** When implementing Phase 3, these are hard constraints.
