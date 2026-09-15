- **Path enrichment, measured: its 2 lifetime firings are the correct count,
  but installs from before #271 never trigger it on `Read`.** Replaying every
  real file tool call since #271 through the live matcher reproduces both
  logged notes exactly. The window was 21 hours, spent mostly in repos whose
  rules name no files. But `mnemo init` wrote the `PreToolUse` matcher without
  `Read`, and nothing rewrites it or reports the gap: over a full week, `Read`
  doubles the notes delivered (40 against 23). `mnemo doctor` now reports
  the gap and `mnemo init --hooks-only` closes it (#303). Findings and recommendation are in
  `docs/superpowers/specs/2026-09-15-enrichment-status.md`.
  (channels/investigate-enrichment)
