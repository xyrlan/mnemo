- **`mnemo backfill` harvests Grok Build sessions beside Claude Code's.** The
  sweep (and the install run) now asks discovery for every Grok home's
  sessions, and the backfill ledger, harvest and friction capture file each
  session under its real id rather than its file's stem — every Grok history
  is `chat_history.jsonl`, so a stem key would have lumped them into one entry
  and re-paid a model call for each on every sweep. A Claude session's key is
  unchanged, so an existing `backfill-state.json` still matches and a second
  sweep over the same sessions spends nothing. A Grok child's corrections are
  marked as a dispatched child's by its Grok short id. (#708)
