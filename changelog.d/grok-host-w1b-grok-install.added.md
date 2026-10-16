- **`mnemo init --host grok` wires Grok Build, once.** Grok already reads
  Claude's `~/.claude.json` and `~/.claude/settings.json`, so where mnemo is
  installed for Claude, init says so and adds nothing (a second copy would run
  every hook twice). Where Grok sees no mnemo hook or MCP server — no Claude, or
  `[compat.claude]` switched off — it registers the server with `grok mcp add`
  and writes mnemo's hooks to `$GROK_HOME/hooks/mnemo.json` (`<repo>/.grok/hooks/`
  with `--project`); `grok inspect --json` decides which. `mnemo uninstall
  --host grok` removes those two and never touches Claude's files, and
  `mnemo export --host grok` writes `.grok/rules/mnemo.md` (`--target agents-md`
  for a block in `AGENTS.md`). `mnemo doctor` reports, when `grok` is on PATH,
  its version, each home and whether it is logged in (no secret read), whether
  mnemo's hooks and MCP are visible to Grok, and whether each home's leader
  socket path fits the platform's Unix-socket limit.
