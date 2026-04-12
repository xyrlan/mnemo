# Configuration

mnemo's config lives at `~/mnemo/mnemo.config.json` (or wherever your vault root is).

```json
{
  "vaultRoot": "~/mnemo",
  "capture": {
    "sessionStartEnd": true,
    "userPrompt": true,
    "fileEdits": true
  },
  "agent": {
    "strategy": "git-root",
    "overrides": {}
  },
  "async": {
    "userPrompt": true,
    "postToolUse": true
  }
}
```

## Keys

| Key | Default | Meaning |
|---|---|---|
| `vaultRoot` | `~/mnemo` | Where the vault lives. Tilde is expanded. |
| `capture.sessionStartEnd` | `true` | Log 🟢/🔴 markers at session boundaries |
| `capture.userPrompt` | `true` | Log first line of each prompt |
| `capture.fileEdits` | `true` | Log Write/Edit tool calls |
| `agent.strategy` | `git-root` | How agent names are derived (only `git-root` in v0.1) |
| `agent.overrides` | `{}` | Reserved for future use |
| `async.userPrompt` | `true` | Run UserPromptSubmit hook async (no visible latency) |
| `async.postToolUse` | `true` | Run PostToolUse hook async (no visible latency) |

## Environment overrides

- `MNEMO_CONFIG_PATH` — load config from this path instead of the default

## Disabling capture entirely

Set every `capture.*` to `false` and run `/mnemo status` to confirm hooks no longer write.
