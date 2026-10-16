- **mnemo's hooks work in a Grok Build session.** Grok fires the hooks in
  `~/.claude/settings.json`, but it names its tools its own way and drops
  what SessionStart and UserPromptSubmit return, so enforcement, enrichment
  and the memory-path guard did nothing there, and neither the briefing nor
  the reflex reached the model. Now the hooks map Grok's tools to Claude's
  (`run_terminal_command`, `read_file`, `write`, `search_replace`,
  `hashline_edit`). The reflex block and held child notices go out with the
  turn's first tool call. A project's briefing is written to
  `.grok/rules/mnemo-briefing.md` in each of its worktrees; Grok loads that
  file when the next session starts, and `info/exclude` keeps it out of
  every commit. Grok loads it only in folders it trusts; a session that
  started without it gets the briefing on its first tool call. SessionEnd
  hands its work to a detached worker inside Grok's 1.5 s limit, and briefs
  from `chat_history.jsonl`. When mnemo's hooks are wired from two hook
  files, an event runs once. Claude sessions are unchanged.
  `tools/measure_grok_reach.py` reports how often each channel reached the
  model, from the sessions' own histories. (grok-host-w1)
