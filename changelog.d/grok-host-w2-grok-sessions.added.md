- **`mnemo sessions`, `mnemo session <id>`, the statusline count and
  `deliver --review` show Grok children beside Claude ones.** A child
  dispatched on Grok (read from the `child_agents` ledger) gets a row whose
  `state`, `tempo`, `needs`, `live`, `model`, `effort` and `context_tokens`
  mean what they mean for a Claude child, read from the leader's roster and
  the session's own files; a field Grok has no equivalent for stays empty.
  Listing never starts a Grok leader: a child whose leader is down or whose
  home is gone is shown with what its files say. `mnemo sessions --json`
  carries `agent` (`claude` or `grok`), and the `attach:` hint opens a Grok
  child with `grok --resume`. (grok-host-w2)
- **A Grok child stopped by its account's limit is filed under STALLED, and
  `mnemo resume` and the automatic wake take it.** Grok writes the limit to
  `updates.jsonl` (`stop_reason: "rate_limit"`) with no reset time; the stall
  is dated by the rolling window the API's message names (24 hours on the free
  tier), the latest moment the usage it counted has aged out. (grok-host-w2)
