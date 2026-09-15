- **Unblock markers whose transcript is gone are retired instead of retried
  forever, and every deferred marker now says why.** `mnemo sessions
  --consume-unblocks` retires a marker (`ConsumeReport.retired`) only when no
  `<session_id>.jsonl` exists in any Claude Code project directory, so a
  session whose transcript is still on disk is never discarded. A marker
  that fails for any other reason stays pending, with `attempts` and
  `last_error` on it in `.mnemo/session-queue.json`, and one `.errors.log`
  line (`unblocks.consume`) each time its error changes. On the real vault
  all 27 stuck markers still have their transcripts, so none retire: they
  fail because `learn` cannot find a worktree session's transcript from its
  `cwd`, and that is a separate bug. (channels)
