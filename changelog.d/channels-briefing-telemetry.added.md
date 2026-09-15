- **Every injected briefing can now be recorded by name.** `record_briefing_read(vault_root, record)`
  appends one row to `.mnemo/briefing-log.jsonl` — vault-relative path, source
  session id, date, injected byte count and a hash of the exact body the session
  received — so which briefing a session got, and how often each one is read,
  is recoverable from disk instead of hidden behind `included_briefing: true`.
  It is a file of its own rather than a row in `mcp-access-log.jsonl`, so ~80
  briefed session starts a day do not shorten the window `mnemo recall` reads
  from; it follows the same telemetry switch and 1 MiB rotation, costs ~0.1 ms,
  and never raises. Picking a briefing records nothing; the session-start hook
  wires the call in separately. (channels)
