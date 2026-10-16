- **Seams for driving Grok Build beside Claude Code.** `mnemo.core.agent_cli`
  decides which agent CLI a process drives (`--agent`, `MNEMO_AGENT`, a Grok
  session's `GROK_SESSION_ID`, else Claude); `mnemo.core.grok.paths` says where
  Grok keeps homes, sessions and its leader socket; `Session.agent`, the
  `--agent` flags and a `mnemo grok` stub exist for the round's pieces to fill.
  Nothing changes for Claude. Spec: `design/specs/2026-10-16-grok-host.md`.
