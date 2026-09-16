- **`mnemo friction` reports the friction ledger, and `--backfill` recovers the
  corrections the vault threw away.** With no flags it is read-only. It shows
  corrections by project and origin, how many live rules stand contradicted
  (and which named slugs are not live rules at all), how many links an
  injection in the same session corroborated, and the rank at which each
  confirmed link sat among the candidates, so the candidate count can be
  revisited against evidence. `--backfill` sweeps every session with a
  transcript on disk, newest first, not only the briefed ones. Each session
  gets one briefing call into `.mnemo/friction-backfill/`, reused on a rerun
  unless `--fresh`. Every quote is checked against what the user typed, then
  linked. Each session is reported as *corrections found (N)*, *none found*,
  *transcript gone* or *briefing failed*. The plan is saved to
  `.mnemo/friction-backfill-plan.json` after every session, so an interrupted
  sweep resumes where it stopped. `--apply` writes exactly that plan with
  `backfilled: true` and no second model call, and running it twice writes each
  row once. `--since` and `--project` bound any of them; `--json` for all.
  Link ranks go to `.mnemo/friction-link-ranks.jsonl` beside the ledger.
  (friction-loop wave 2)
