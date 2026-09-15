- **A dispatched child's briefing is now documented and tested as the
  canonical project's, both ways.** It looked like children got no briefing:
  no `bots/<repo>-wt-*/` namespace holds one, and no inject event names a
  worktree. Their own transcripts say otherwise. Of 92 children, every one
  that could get a briefing did (71). The rest started before their project
  had any briefing (16) or hit the circuit breaker (5). All 26 briefings
  children wrote landed under the canonical project, and none were lost with
  a worktree. Nothing about the behaviour changes. New tests run the real
  hooks from a real `mnemo-wt-*` worktree, so a later change that gives
  children a namespace of their own fails loudly. (channels: child-briefing)
