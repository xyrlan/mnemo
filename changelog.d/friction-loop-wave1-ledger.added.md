- **A friction ledger records what contradicted the vault.** Every correction
  a session produces can now become a structured row in
  `<vault>/.mnemo/friction-ledger.jsonl` naming the rule it contradicts,
  instead of a line of briefing prose that marks no rule and retires nothing.
  Measured on the maintainer's vault, 97.9% of 1946 live rules have exactly
  one source and `mnemo replay` reports zero carried correction-backed
  injections — not for want of signal, since 100% of the briefings that were
  asked for corrections found some, at about 8.75 a day. This first piece is
  the on-disk shape alone (`mnemo.core.friction.ledger`): it runs no model,
  reads no rule and writes to no page. It follows `briefing-log.jsonl` — same
  telemetry switch, same 1 MiB rotation — and never raises, so a failed row
  never costs an extraction. A repeated `(session_id, quote)` is refused
  rather than appended, because the backfill that fills this ledger with
  history sweeps the same sessions on every rerun. Nothing writes to it yet;
  the contradiction pass, the backfill and `mnemo friction` follow.
  (friction-loop-wave1)
