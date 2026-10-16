- **`mnemo dispatch` spawns children on Grok Build when the selected account is
  a Grok one.** The CLI comes from `--agent`, then `MNEMO_AGENT`, then a Grok
  session's own `GROK_SESSION_ID`. A Grok child is a session in Grok's leader,
  started in its own worktree under the dispatching `GROK_HOME`. It gets the
  same branch, grant and parent link as a Claude child, and a prompt that is
  the same except for how it finishes: it ends its last turn with its closing
  report and is never told a `claude` command. Its full id, home and tree go
  into `.mnemo/child-agents.jsonl`, and every ledger keys it by the last eight
  hex digits of its id. Before any worktree exists, dispatch refuses what Grok
  cannot take:
  - no `grok` on PATH, or no login;
  - a `--model` that is not in the account's model list (a Claude name like
    `haiku` is not);
  - an `--effort` that the model does not take (`max` on `grok-4.7`);
  - `--read-only` and `--twins`, which are not supported on Grok yet.

  A spawn the leader refuses removes its tree and branch, and the error says
  what to run. The report's `attach:` line is `grok --resume <id>`. A Grok
  session that runs `mnemo dispatch` is recorded as the children's parent, with
  its home. With no Grok in the environment, nothing changes. (#702)
