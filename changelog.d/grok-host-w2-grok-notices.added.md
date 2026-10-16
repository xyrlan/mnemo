- **A parent learns that its Grok child finished, and a Grok parent hears
  about every child.** A Grok child ends its last turn with its closing
  report and goes dormant, and a dormant session fires no SessionEnd. So
  the `child-notices` watcher now reads each dispatched Grok child's
  `events.jsonl`. When the last turn ended `completed` and has stayed
  ended for a minute, the watcher runs the hook's whole end-of-session
  pipeline for that child: the parent's report card, the PR follow,
  extraction and the briefing. That pipeline runs once per stop, whether a
  watcher, a restarted watcher or a later SessionEnd gets there first
  (`.mnemo/grok-finishes.jsonl`). The card's closing report and PR come
  from the child's `chat_history.jsonl`. A Grok parent is found from its
  session on disk, starting with the home dispatch recorded. Its card
  arrives as a user turn through Grok's leader, which wakes the parent if
  it is dormant. A parent that a Grok TUI has open outside the leader
  never gets a turn this way, because loading it would fork the open
  session. Its notice is held, as is any notice the leader cannot take, and
  the parent gets it with its next prompt. Claude children and parents
  are unchanged. (grok-host-w2, #705)
