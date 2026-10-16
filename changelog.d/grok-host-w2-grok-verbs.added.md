- **Waking, stopping and following a child work on a child spawned on
  Grok.** `mnemo resume`, the automatic rewake after a rate limit and
  pr-follow's nudge send their turn through the child's own Grok leader, and
  `mnemo deliver` closes a finished Grok child's session there instead of
  running `claude stop`. Each verb reads which CLI and which Grok account a
  child was spawned on from the dispatch ledger, never from the account
  selected now, and a Claude child's verbs are unchanged. A Grok child is
  keyed in pr-follow by the last eight digits of its id, so two children
  dispatched together no longer share one follow. A leader that cannot be
  reached is reported and leaves the follow open to try again. `mnemo
  deliver --stop-done --agent CLI` and `mnemo resume --agent CLI` narrow to
  one CLI's children, `mnemo resume` names `grok --cwd … -r …` for a Grok
  child that needs a person, and `tree-sweep` removes a merged Grok child's
  tree once its leader has unloaded it. (#704)
