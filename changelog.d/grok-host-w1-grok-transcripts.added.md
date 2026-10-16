- **mnemo reads Grok Build sessions like Claude Code ones.** A Grok
  `chat_history.jsonl` is converted to the events every reader already
  understands, so the session briefing, its verified corrections and `mnemo
  learn` work on a Grok session unchanged: the briefing is named after the
  session's directory, Grok's `write`, `search_replace` and `hashline_edit`
  count as file edits, and only the text inside `<user_query>` (prompts and
  mid-turn interjections) counts as the person's words — system reminders,
  project instructions and the `<user_info>` prefix Grok injects never do.
  Discovery finds Grok sessions in every Grok home (`~/.grok`, `$GROK_HOME`,
  `~/.grok-<account>`), filed under the same project as the repo's Claude
  sessions. `mnemo learn` takes Grok's newest session when run from a Grok
  session or with `MNEMO_AGENT=grok`, and `--session` accepts a Grok id or its
  short id; from Claude Code it picks exactly what it picked before. `mnemo
  backfill` stays Claude-only until its ledger keys sessions by id. (#696)
