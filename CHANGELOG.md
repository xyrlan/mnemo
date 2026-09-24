# Changelog

All notable changes to mnemo will be documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.7.0] — 2026-09-24

### Added

- **`mnemo deliver --stop-done` stops every finished child in the repo's
  dispatch worktrees, delivered or not.** A briefing is made from the session,
  not from the pull request, and only a stopped child fires `SessionEnd` — so
  the child that judged its task wrong and published nothing is exactly the one
  delivery-shaped stopping could never reach, and its reasoning is the only
  copy there is. It pushes nothing and approves nothing, and is refused
  alongside named ids or `--review` because each of those is a different
  command. `mnemo deliver --review` now also prints a `TERMINADAS, NÃO
  PARADAS` group naming the finished children still running. (#349)
- **`mnemo land` refuses a piece whose pull request has a failing check.** It
  reads `gh pr checks --json name,bucket` and judges check by check, not the
  run's conclusion or the PR's rollup: a repository may mark a job
  non-blocking, and such a job fails while both aggregates still report
  success. Pending is not failure, and an unreadable answer refuses nothing —
  the gate stands on evidence, never on the absence of it. (#349)

- **`mnemo dispatch --effort <level>` sets a child's reasoning effort, and `mnemo sessions` reports it.** The levels are Claude Code's own (`low`, `medium`, `high`, `xhigh`, `max`). mnemo checks the level before spawning, because the CLI only warns about an unknown one and then runs the default. A contract piece may name its own `effort:`, which wins over the flag, as `model:` does. The value is read back from the child's `respawnFlags`: `mnemo sessions --json` has an `effort` field, `mnemo session <id>` shows it, and the queue gets an `esforço:` footer once any child has one. `null` means the default, since Claude Code records an effort only when one was passed. With no `--effort`, the spawn command is byte-identical to before. mnemo does not choose the effort itself yet: nobody has measured whether it changes child outcomes. (#351)

- **A finished child tells the session that dispatched it.** `mnemo dispatch`
  spawns detached children and nothing woke the dispatcher when one exited, so
  the maintainer was the message bus between two of his own sessions. On a
  dispatched child's `SessionEnd`, mnemo now looks up its `parent_session` and
  delivers one line over that session's inbox socket. Switchable off with
  `dispatch.notifyParent`. Delivery is best-effort by design: a parent that
  has already exited is not queued for.

  Reaching a session by id needed a map that did not exist. The socket is
  named by pid (`/tmp/cc-socks/<pid>.sock`), a transcript records no pid, and
  `~/.claude/daemon/roster.json` — which does map session to pid, and is how
  `mnemo-desktop` reaches a child — lists only daemon workers: measured
  2026-09-17 it held 4, and 0 of the 13 `parent_session` ids ever recorded
  were among them. So each session writes its own address down at
  `SessionStart`, the one moment it knows it. A row keeps the pid *and* its
  start time, because `/tmp/cc-socks` held 93 sockets of which only 8 named a
  live process, and a recycled pid must not inherit someone else's notice.

  The notice opens with a marker the unblock detector skips: the sweep that
  looks for the maintainer answering a blocked session runs in the very hook
  that sends this, and would otherwise have recorded mnemo's own message as a
  human unblocking the parent. It reports an exit and points at
  `mnemo sessions`; it carries no authority and says so in its own text.

- **Model calls now go through a provider named by `extraction.provider`.**
  Briefings, extraction, backfill harvest, reclassify and the friction
  contradiction pass all used to call `claude --print` directly, so no other
  host could learn rules. They now call the provider the config names, which
  defaults to `claude-cli`, the same `claude --print` call as before, so
  Claude Code users see no change. `claude-cli` is still the only provider;
  this change is the seam a Codex or API provider will plug into. An unknown
  value fails before the run writes anything. `llm.call` telemetry rows now
  record which provider answered. (#358)

- **`mnemo dispatch <issue> --read-only` spawns a child that investigates the issue and comments its finding, instead of building it.** The child gets the analysis prompt rather than the implementation one: no test suite to run, no changelog fragment to write, and a closing that posts to the issue with `gh issue comment` rather than checking for commits to publish. Its file-editing tools are closed at spawn — `Edit`, `Write` and `NotebookEdit`, its own subagents included — so an investigation does not drift into a branch out of habit. It can still write through the shell, measured rather than assumed, which is why it keeps a worktree of its own: the restriction prevents drift, it does not contain a child that means to build. Because it publishes nothing, `--may` is refused alongside it by name rather than ignored. A contract piece may ask for the same posture with `- **read-only:** yes`, which wins over the flag as `may:` does. With no `--read-only`, the spawn command is byte-identical to before. (#371)

- **`mnemo inbox` — the review queue for staged pages, and the two acts that
  clear it.** `shared/_inbox/` fills by itself and drained only by a hand `mv`:
  194 plain pages on the maintainer's vault, median age 5.3 days, oldest 16 —
  every one of them invisible to recall while it waited, because location is
  what decides whether a page is served. `mnemo inbox` lists what is staged for
  the project you are standing in (`--all` for every project, `--show KEY` for
  one page), `--promote KEY` moves a page into `shared/<type>/` and rebuilds the
  indexes so recall reaches it at once, and `--drop KEY` archives it and takes
  it out of the queue. Both write the extractor's ledger, which the `mv` never
  did: a promoted page no longer comes back as an `.update-proposed.md` on the
  next extraction, and a dropped one does not come back at all. `--stats`
  prints queue depth, median age and what drained in the last seven days.
  (#380)
- **A staged page reaches you at session start instead of waiting to be looked
  for.** When pages are staged for the project you just opened, mnemo adds a
  `[mnemo staged for review]` block naming the oldest of them, each with the
  one command that acts on it — the disclosure half of extraction, next to the
  `[mnemo learned]` block that already announces what it promoted. The
  decision stays yours: nothing promotes, accepts or drops a page on its own.
  Three numbers bound the noise, under `inbox` in `mnemo.config.json`:
  `offerMax` (2) bullets per block, one block per project per
  `offerIntervalHours` (24), and no page repeated inside `offerCooldownDays`
  (7). `offerOnSessionStart: false` silences the block and leaves the command.
  (#380)

- **`mnemo recall` now says whether a buried rule could have been reached at
  all.** Each queried case records how many of its query's tokens the expected
  rule actually indexes and the rule's BM25F score, and the report splits the
  cases outside the top 5 into the ones whose rule shares no token with the
  query — unreachable by any re-ranking — and the ones that are scored and
  still lose. Six of the twelve on the live vault are the former, so most of
  the miss list was never a ranking problem, and a fix aimed at ranking cannot
  move it. (#381)

- **`tools/measure_exploration.py --by-kind` says what a dispatched child's
  pre-mutation window was spent *on*, not just how big it was.** #269 measured
  the size (median 18 tool uses, +43k tokens before the first tree change);
  this splits it by the question each use asked — `list` (*what is here*),
  `search` (*where does X live*), `read`, `run`, `context`, `vault` — and
  reports the **ceiling**: the `list` + `search` share, which is the most any
  repo map, index or extra opening-prompt context could ever remove, next to
  the sample size an A/B would need to see a cut that size. A use that asks two
  questions counts half in each, because resolving mixed commands to one kind
  handed a map 96% of the bytes the co-located reads had returned.

  Measured on the 176 dispatch children on disk on 2026-09-19, the ceiling is
  **16% of the bytes and 4 of 18 uses**: reading the files the child is about
  to change is 71%, and no map replaces it. #382 asked for a cached repo map
  and is refused on this arithmetic — on every repo dispatch runs in, an index
  dense enough to answer the `search` half costs more tokens in the prompt than
  the whole ceiling is worth, and an A/B would need n≈141 children per arm to
  see even a perfect, free one. `mnemo dispatch` is unchanged. (#382)

- **`tools/measure_refusal_timing.py` dates a dispatch child's doubt about
  its own issue.** For every dispatch transcript on disk it reports whether
  the child recorded a refusal, a corrected premise or a re-scope, and how
  many tool uses had run when that doubt first appears in the child's own
  words — plus how often a child was answered by the maintainer at all, split
  by what actually arrived, since a peer session's message and Claude Code's
  own restart notice both land as human turns. `--list` prints the sentence
  every marker matched so a count can be read rather than trusted. First
  result, over 180 children: 33 refusals, of which 4 stated the doubt within
  their first ten tool uses and 20 said nothing until the closing report.
  (#383)

- **Every child of an issue dispatch is now told which other issues that run
  started.** `mnemo dispatch 382 383 384` gives each child a short roster —
  number and title, nothing else — so a child knows who is editing the repo
  beside it and can read their issue before landing on the same file. A child
  dispatched alone reads the prompt it read before, unchanged. Measured over
  118 past children: 30 of 36 batches started more than one child, and
  siblings wrote the same file in 3 of the 16 multi-child *issue* batches
  against 0 of the 13 *contract* batches, where each piece is already told
  what it may not touch — so contract pieces deliberately get no roster.
  `tools/measure_dispatch_siblings.py` is that count; `--list` prints every
  pair. What the dispatching session *reasoned* is still not passed: its
  written decisions already travel in the issue, which 68 of 68 prompted
  children read, and passing its unwritten ones is the #187 failure — a
  prescribed conclusion overriding a correct refusal. (#384)

- **`tools/measure_child_procedures.py` counts what dispatched children get
  wrong about running work in your repo**, and `CLAUDE.md` now states it for
  this one. A procedure — how the suite runs, where changelog entries go — is
  a boundary, not an approach, and a child that has to rediscover it pays
  every time: measured over 182 children on four repos, 12 mnemo children
  worked out `PYTHONPATH=src` mid-run and 2 never did, reporting a green
  suite that had imported the main checkout. No new file format ships for
  this. Claude Code already attaches the repo's `CLAUDE.md` to every child —
  where clubinho states a flag there, 24 of 24 runs carried it; the heap size
  it states nowhere was missed by 13 of 14 children. `mnemo dispatch` holds
  no repo's procedure and is unchanged. (#385)

- **`mnemo procedures` — what children keep rediscovering, as a proposed `CLAUDE.md` line.** #385 found that a repo's `CLAUDE.md` reaches every dispatched child while a ranked rule only reaches the ones whose prompt matches, and then wrote that file by hand. This reads the transcripts instead: a command shape two or more children of a repo ran bare and then re-ran with an environment variable in front is a procedure they paid to learn, and the command proposes the line that would have told them. `--show` prints it and who paid, `--accept` appends it (the only write, and it only ever appends), `--drop` takes it out of the queue for good. A `doctor` row carries the count, because a command you have to know about is one nobody runs. Detection uses no per-repo probes: the shapes every repo's children run — `git log`, `gh issue` — are excluded by counting repos, not by a list. (#392)
- **`tools/measure_rediscovered_procedures.py`.** The count the command is gated on: across the 184 dispatch children on disk on 2026-09-19, five procedures were rediscovered by two or more children of the same repo and one of them (`PYTHONPATH` on mnemo's suite) was already written down. `--rejected` prints the other half of the finding — the same bar over flags would have proposed eleven more, led by `cargo test --nocapture`, which is a way of reading output and not a boundary. So flags are counted and excluded rather than guessed at, and a procedure that lives in one stays invisible. (#392)

- **`mnemo resume` wakes the dispatched children a reset has freed, in one
  command.** The recovery was two commands per child — `claude respawn <id>`,
  then a cross-session message telling it the limit had reset — the second of
  which is a socket message, a channel that can never carry authority and only
  worked because the child's grant was already in its opening prompt. One
  `mnemo resume` now does all of them: it wakes each child with `claude --bg
  --resume <full session id>`, which restores every saved option (a read-only
  child does not regain `Edit`, a lean child does not lose mnemo's hooks) and
  continues the same session in the same worktree rather than forking a copy —
  passing the *short* id silently starts a second session in the tree, so it
  is refused. The message the child reads is a constant with no parameter to
  pass anything through it: it grants nothing and says so, and points back at
  the opening prompt, which stays the only instruction the child has.
  Only a child whose process is gone and whose stall a clock frees is woken,
  and only past the reset — read as a unix epoch from the child's own
  transcript (`quotaLimits.resetsAt`). `--dry-run` shows what a bare `mnemo
  resume` would wake and spends nothing; naming ids or issue numbers narrows
  it. What bounds re-spending the window is stated rather than implied: no
  `claude` subcommand reports how much of it is left, so past the reset the
  window is fresh, and N children spend it N times faster rather than more.
  (#393)

- **A rate-limited child is woken when its own reset passes, with nobody
  there to type `mnemo resume`.** #393 made the recovery one command; it still
  waited for a person who knew to run it, and on 2026-09-19 six children sat
  stalled for nearly four hours past their reset. mnemo now wakes them itself.
  Switchable off with `resume.auto`; `resume.maxPerPass` bounds one pass.

  The trigger is not a hook, and the measurement is why.
  `tools/measure_wake_latency.py` reads every rate limit on disk against every
  hook mnemo has ever recorded running (525 transcripts, 5372 hook events, 15
  limits carrying a reset epoch): the first hook after each reset came 15,
  109, 229 and 499 minutes late, and for four of the five resets *no hook fired
  at all* between the stall and the reset — the account limit stops every
  session on the machine, so the thing that would notice is stopped by the
  same wall. A SessionStart pass would have saved the incident nothing. What
  the same measurement does show is a hook firing 9 to 86 minutes *before*
  each stall, so SessionStart now starts a small watcher process instead: it
  makes no API call, survives the limit that killed everything else, wakes
  each child as its clock comes round, and exits when there is nothing left to
  watch — which on a machine running no dispatched children is immediately.
  `mnemo resume --watch` is the same thing run by hand.

  It is deliberately narrower than what `mnemo resume` will wake: only a rate
  limit that named its own `resetsAt`, past that epoch. A stall needing a
  person is never touched, and neither is a transient one or a per-model
  credit cap — with no epoch there is no window, and no window means nothing
  bounds a retry. What bounds re-spending the limit is the epoch itself, used
  as an idempotency key: a child is woken automatically **at most once per
  reset window**, so one that re-stalls immediately waits for the next
  boundary rather than being woken in a loop. A machine-wide lock makes a
  second concurrent pass a no-op and keeps a hundred session starts to one
  watcher (#330). Every wake is recorded in `.mnemo/resume-wakes.json`, in the
  day log of the repo the child was working in, and in a `mnemo sessions`
  footer — not only in the child's own transcript. (#396)

- **An undecided procedure reaches you at session start instead of waiting for
  a command you have to know about.** `mnemo procedures` (#392) proposes the
  `CLAUDE.md` line two or more dispatched children of a repo worked out for
  themselves, and until now it reached only whoever ran it or its `doctor` row
  — both pulls, and #385 priced a pull at 5 of 182 children. Open a session in
  a repo with an undecided candidate and mnemo now adds a
  `[mnemo procedure candidate]` block naming it, with the command that accepts
  it; open one with none and nothing appears. Measured on the maintainer's own
  vault: 398 bytes for `mnemo`'s one candidate, 547 for `mnemo-desktop`'s three
  — roughly 100–140 tokens against a briefing that costs ~1783 at 90.9% of
  session starts — and 0.5 ms of hook time. The decision stays yours: the block
  offers, `mnemo procedures --accept KEY` writes, and nothing edits a
  `CLAUDE.md` on its own. Bounds live under `procedures` in
  `mnemo.config.json`: `offerMax` (**1**, not the inbox's 2, because accepting
  writes a permanent line every session in that repo then pays for), one block
  per repo per `offerIntervalHours` (24), no candidate repeated inside
  `offerCooldownDays` (7). `offerOnSessionStart: false` silences it and leaves
  the command working. (#397)
- **One offer block per session start, alternating between the two review
  queues.** `[mnemo staged for review]` (#380) and the new
  `[mnemo procedure candidate]` both ask for a decision, and two of them on one
  prompt is a nag — so they share a single slot, which goes to whichever has
  gone longest without it. A queue that wins the slot with nothing to say hands
  it straight back, so alternating never costs you an offer, and a repo with no
  candidates sees exactly what it saw before. Worst case on the prompt is one
  block. (#397)
- **`mnemo procedures --refresh` and `--stats`.** Finding candidates means
  reading every dispatch transcript on disk — ~1.0 s over the 184 there on
  2026-09-19 — which the session-start path must not pay, so the scan runs
  detached at most once per `procedures.refreshIntervalHours` (24) and the
  block reads its cache under `.mnemo/procedure-candidates.json`. `--refresh`
  rebuilds that cache in the foreground; it writes nothing to any repo. The two
  staleness cases that would matter are read live rather than from the cache: a
  candidate you already accepted or dropped, and a line you wrote into
  `CLAUDE.md` by hand, are both silent immediately. `--stats` reads the
  `offered` rows now appended to `.mnemo/procedure-decisions.jsonl` and reports
  what was offered, accepted and dropped in the last seven days, and the median
  time from a candidate being shown to being decided — the number that says
  whether the offer is what gets candidates decided at all. (#397)

- **`recall.rerank`: an opt-in second stage for `list_rules_by_topic`, off by default.** With `recall.rerank.provider` set to `typesafe`, a list call that carries a `query` posts that query and the first 800 characters of each rule in the topic to a pair-reading model (`jev-1.13.0`, pinned) and returns the list in its order; the key comes from `TYPESAFE_API_KEY`, never from the config file. One request per call, `urllib` only. A missing key, a timeout, an HTTP error or a malformed answer returns the BM25F order, and the access log records which (`rerank.status`). The per-prompt reflex, `mnemo recall` and every hook are untouched — the MCP server is the only caller. **This is the first recall feature that can leave the machine**, so the README's privacy paragraph now names both switches that can. Measured with `tools/measure_rerank_judges.py`, which asks the same question of the same rule text: +0.077 nDCG@5 [+0.029, +0.124] under a different model's labels over 38 replayed queries. On 85 real queried calls the same reordering is not established (+0.081 [-0.024, +0.185]), which is why the stage ships as a *marker* rather than a reordering — see #404. (#401)

- **`recall.rerank` marks what is worth reading instead of just reordering it.** Every rule the judge scored comes back with `relevant: true|false` on it, the marked ones first, and **nothing is ever dropped** — a rule the judge got wrong is one line further down, not gone. A rule it did not score (past `maxRules`, empty body, skipped) carries no `relevant` key at all: absent means "not judged", never "irrelevant". The signal is the judge's probability plus `bm25Weight` (0.5) times the local BM25F score over the best one in that list, marked at `relevantAt` (0.69); both keys are new under `recall.rerank` and a bad value falls back to the default. `list_rules_by_topic`'s description now tells the agent to read the marked rules first, and that none marked is a real answer — nothing in this topic is about this task. With the stage off (the default) the list is exactly what it was and no item carries the key. Why: the list an agent gets is 15 rules, 75% of them about something else, and it picks from slugs alone, so reordering is worth little (+0.081 nDCG@5 [-0.024, +0.185], not established) while the same signal as a filter turns 15.0 rules per query into 2.3 and 75% junk into 13% without losing any of the 24 rules the labels call "should read". `tools/measure_rerank_filter.py` is that measurement and ships with it: 85 real queried calls mined from session transcripts, 1,536 labelled pairs, thresholds fixed on a dev split of 55 queries and the remaining 30 opened once. (#404)

- **`mnemo rerank` — the opt-in recall rerank can now be turned on without a shell.** The stage shipped reading its key from one place, an environment variable, and the process that reads it is the MCP server, which Claude Code spawns: an `export` in `.zshrc` reached it only when `claude` was started from that shell, and never from an app opened from the Dock. `mnemo rerank --setup` asks for the key without echoing it, proves it with one request to the provider, and stores it in `~/.mnemo/secrets.json` — owner-only where the platform has file modes, outside the vault and outside `mnemo.config.json`, which is committed in a repo-local install. The environment variable still wins when it is set. `mnemo rerank --off` reverses both halves. (#406)
- **The silent fallback is now visible to whoever configured it.** `mnemo rerank` reports the provider, where the key comes from and the last 14 days of the stage's access-log rows by status (`--json`, `--days`); `mnemo doctor` fails a `rerank` row when the provider is set and no key resolves, which is the state where the list looks unchanged because every call fell back; `mnemo status` carries the same summary in one line. None of the three makes a network call, and none of them can print the key. (#406) The provider request now goes through a verifying TLS context that, when Python's own CA store is empty — the python.org macOS build before its `Install Certificates.command` is run, where every HTTPS request dies with `CERTIFICATE_VERIFY_FAILED` and the stage would report `error` on every call — loads the operating system's bundle (`/etc/ssl/cert.pem` and its Linux equivalents) instead; verification is never relaxed and an explicit `SSL_CERT_FILE` wins.

- **`mnemo dedup-rules --judge` — a queue for the duplicates no token gate can see.** Two rules that state one lesson in different words share no `name:` and no token overlap, so every dedupe mnemo has waves them through: #187 measured a real duplicate at Jaccard 0.136 against a p90 of 0.131 over 79,800 unrelated pairs, and no threshold separates those. This asks a model that reads the pair, over every pair of live rules inside one topic bucket of one project — the list `list_rules_by_topic` answers with, where since #404 a near-copy costs one of the few slots that are not already about something else. A pair is asked about once however many topics or projects hold both rules. It is a **dry run unless `--send`**: the default prints buckets, pairs, estimated tokens and cost, and how many pairs the Jaccard gate catches on its own (on a 2,203-rule vault: 1 of 48,090). `--max-pairs` (default 5,000) refuses a send over the cap and names the largest buckets. `--send` posts both rule bodies of every pair (1,200 characters each, link section removed) to `api.typesafe.ai` — the same provider, key and verifying TLS context as `recall.rerank`, but it does **not** require that stage to be on, and with no key anywhere it says `mnemo rerank --setup` and exits non-zero. Answers land in `.mnemo/dedupe-answers.json` as they arrive, so an interrupted run keeps what it paid for and a re-run asks only for what is missing; a pair the provider could not answer is counted apart and asked again rather than read as "not a duplicate". The result is `.mnemo/dedupe-queue.json` (`--json`): pairs at or over `--at` (default 1.5), best first, with both slugs, both names, the opening 200 characters of both bodies, the score, the Jaccard ratio and its rank inside the bucket — a duplicate Jaccard ranks 41st of 435 is the finding. (#409)
- **`mnemo dedup-rules --merge KEEP DROP` acts on one pair, and only when you name both slugs.** Nothing in `--judge` deletes, merges or stages anything — there is no labelled set of duplicates in a vault, so it has no precision to quote and is a queue for a curator, never a merge list. `--merge` executes `reclassify`'s own `merge` verdict: `DROP`'s sources are unioned onto `KEEP`, `DROP` is archived with a byte-exact original under `shared/_archive/reclassify-<RUN_ID>/`, and `mnemo reclassify --undo <RUN_ID>` restores both. It works across page types — the pair #187 found was a `feedback` page and its `reference` twin — which `reclassify` alone could not do: a merge target it could not find silently became a demotion of the other page, and a merged-away page's extraction-state entry was keyed under `feedback/` whatever type it had, which would have let the next extraction write it back. (#409)
- **The question `tools/measure_jev_dedupe.py` measures is now the one the command sends.** The question, the tokenisation, the pair enumeration, the cost arithmetic and the shape of one request moved to `mnemo.core.dedup_judge` and the tool imports them, with a test pinning them to the same objects — the pattern #404 used for `rerank.question`. The body judged moved with them: the link section is dropped the way the recall stage drops it, because rules in one cluster link to each other and those shared `[[wikilinks]]` are overlap that says nothing about what a page claims (on `mnemo`/`measurement`, 435 pairs, keeping it moves the Jaccard p90 from 0.117 to 0.142 and changes 5 of the top 20 pairs by ratio). (#409)

- **`reflex.judge`: an opt-in judge reads the (prompt, rule) pair and decides
  what the per-prompt reflex injects.** It replaces the lexical accept step
  rather than stacking on it, and ships off with its own consent (`mnemo
  rerank --reflex on`) because it is the only thing on the prompt path that
  can leave the machine: the first 1,200 characters of the prompt you typed,
  plus up to three candidate rules. Measured over 300 sampled prompts and 891
  blind-labelled pairs (`tools/measure_reflex_gate.py`): at the shipped bar,
  111 injections instead of 298, 10% noise instead of 56%, 42% on-point
  instead of 11%, and 47 of the 64 on-point rules kept against 32. Every
  failure — no key, a timeout, an HTTP error, a malformed answer — falls back
  to exactly what the gates decide today, and the timeout is a hard wall.
  (#412)
- **`mnemo rerank` reports the per-prompt stage beside the list stage.**
  Prompts judged, status counts, rules asked and injected, median and p90
  milliseconds, read from `reflex-log.jsonl`. `mnemo rerank --off` now turns
  both stages off, and `mnemo doctor`'s `rerank` row fails when either
  provider is set and no key resolves. (#412)

- **The access log now says which rules the rerank stage marked, and which session asked.** A judged `list_rules_by_topic` row's `rerank` object gains `relevant_slugs` (the rules marked `relevant: true`, in the order they were handed back) and `scores` (`[slug, signal]` for every rule judged, best first — the shape `reflex-log.jsonl` already uses); both are empty lists on every status but `ok`, and the object still holds no query and no rule text. Every MCP tool row gains `session_id`, read per call from `~/.claude/sessions/<pid>.json` — found through the messaging socket Claude Code exports, and trusted only when that file names the same socket — with the spawn environment's `CLAUDE_CODE_SESSION_ID` as the fallback and `null` outside Claude Code. The environment alone is not enough: the server outlives a `/clear`, and on 2026-09-22 two of the eight running mnemo servers still carried the id of a session their process had already left (`mcp-server-session` in `claude_cli.ASSUMPTIONS`). Why: `relevant` was a count, so "did the agent read what the judge marked?" — the question the stage exists to answer — could not be asked of the log, and list → read joins had only project + a time window to go on. (#416)

- **A finished child's notice is a report card, and a second one follows when
  its checks settle.** The notice #357 posts into the dispatching session said
  only that the child finished, so the parent fetched the rest itself: on the
  transcripts on disk, 56 of 58 notices were followed by a lookup of the queue,
  the PR or its checks, and 12 by a wait for CI
  (`tools/measure_notice_followups.py`). The notice now carries the child's PR
  (open, draft or merged; `+adds −dels in N files`), its checks by bucket with
  the names of any that failed, whether its tree holds work the PR lacks, and
  its closing report in its own words, bounded. Every line is read from git,
  `gh` or the transcript, and a line whose source fails is left out.

  The header's `state="…"` names what those facts add up to — `ready`,
  `ci-red`, `ci-running`, `draft`, `unpublished`, `no-change`, `merged`,
  `closed`, `unknown`. `ready` means open, not a draft and every check passed,
  never that the change is right. While checks are running, a detached
  `mnemo child-report` polls them every 30 s and posts once more when they
  settle; it stops early if the parent exits and gives up after
  `dispatch.watchChecksMinutes` (30; `0` sends the card alone). It runs `git`
  and `gh` only, never `claude`. Each notice is logged to
  `.mnemo/child-reports.jsonl`, and the measuring tool counts parents' lookups
  after thin and card notices apart, so the effect can be read after the next
  round of dispatches. (#426)

- **A finished child's PR that goes red, gets a review or conflicts with its
  base is handed back to that child.** A dispatched child stops once its PR is
  open, so whatever the PR needed afterwards was fixed by hand: 4 of 46 mnemo
  child PRs and 5 of 35 mnemo-desktop ones carry commits made after the
  child's last turn (`tools/measure_post_done_commits.py`). For a child
  granted `push`, mnemo now follows its PR from a detached `mnemo pr-follow`
  watcher and, on red checks, a comment or asking review from an owner, member
  or collaborator, or a conflict, wakes the child in its own session
  (`claude --bg --resume`, the rate-limit wake's channel) with a fixed note
  naming the event. The child keeps its conversation, its tree and its opening
  prompt, which still decides what it may publish; the note grants nothing and
  forbids force-pushing. mnemo stands down and tells the dispatching session
  instead when someone else has pushed to the branch, the tree is gone or
  dirty, or the child is running or blocked (a rate-limited child stays
  `mnemo resume`'s). It wakes a child at most `dispatch.followPR.attempts`
  times (2) within `dispatch.followPR.hours` of its first stop (24), polls
  each PR every 5 minutes, wakes at most 3 children a pass, and runs one
  watcher per vault behind a lock; `dispatch.followPR.enabled: false` turns
  it off. Wakes and hand-backs are notices to the parent, lines in the day
  log and entries in `.mnemo/pr-follow.json`. (#436)

- **One issue can run as two blind twins, and the maintainer's preference
  between them is recorded.** #439 sized a child-level vault A/B at 85 to 580
  pairs and named the two unknowns that decide where: how far apart two runs
  of the same issue land, and how often a reader cannot choose between their
  diffs. Nothing had ever been run twice. `mnemo dispatch <n> --twins` starts
  two children in `<repo>-wt-<n>-<tag>` on `fix/issue-<n>-<tag>` (a random
  six-hex-digit tag, so neither can tell which run it is), with one prompt
  byte for byte (its sha256 is recorded), one base commit resolved before
  either tree exists, the same model, effort and profile, and no grant, so
  neither publishes and #436's PR follow never wakes them. `mnemo twins show
  <pair>` prints the two diffs as `A` and `B` in an order drawn once, with
  each run's tag, branch, tree and id scrubbed out, and refuses while either
  twin is still working; `mnemo twins prefer <pair> A|B|tie` records one
  answer and only then reveals which run was which. `mnemo deliver <id>`
  refuses a twin until its pair is answered, and a second twin once one is
  delivered; `mnemo deliver <n>` names neither twin and says so. A twin's
  SessionEnd briefing, and the autopilot proposer's analysis, are held: only
  the delivered twin is briefed, because the other run's work never shipped
  and teaching it to the vault is the contamination that sank #439's replay
  design. `tools/measure_child_pairs.py` reports the run-to-run log-sd of
  output tokens and wall time, the tie rate, each with a 95% interval, and
  the pairs the full study needs at 70/30, 65/35 and 60/40, with #439's sizing
  reproduced to the pair. Existing `-wt-<n>` and `-wt-c-<slug>` children keep
  their names, labels and delivery. (#449)

- **`mnemo inbox` lists staged pages as JSON, decides them in batch, and reviews
  them in a terminal.** `mnemo inbox --json [--origin backfill] [--project P]`
  prints one document per call: each page's key, type, name, description, the
  first 300 characters of its body with secrets redacted, when it was staged,
  and when it expires (`inbox.heldExpiryDays`, 14 by default; `null` for a
  page the queue never sheds). `--promote` and `--drop` now take several keys,
  or `--keys-stdin`; a key that fails is reported in `failed` and never stops
  the others, and every batch decision is recorded in
  `.mnemo/inbox-offers.jsonl` with `"via": "review"`. `mnemo inbox --review`
  is a checklist grouped by type with every page checked: toggle by number,
  keep the checked and drop the rest, or quit and decide nothing. Off a
  terminal it prints the list and decides nothing. The text listing is
  unchanged. This is the interface the install review in mnemo-desktop reads.
  (#495)

- **`mnemo backfill` can run for the install review.** `--dry-run --json`
  prints the estimate as one JSON document (`sessions`, `calls_estimate`,
  `api_price_estimate_usd`, and the harvest/extraction split behind it) and
  sends nothing. `--yes --extract --progress-json` harvests, then runs the
  first extraction for that project only, and prints one JSON line per step
  (`harvest`, `extract`, `done` with `staged`, `live` and `failed`). It exits
  0 when the sweep finished, even if some sessions failed, and 2 with an
  `error` line when it could not run. `--extract` works without the JSON too.
  Without `--yes` it still asks first. The call count now counts only the
  sessions that reach the model, so sessions below
  `backfill.minFileMutations` no longer inflate it. (#496)
- **Backfill pages nobody decided now expire after 14 days.** A staged page
  with the backfill origin that was neither kept nor dropped is archived 14
  days after staging. It goes through the same sweep, ledger row (`expired`)
  and `inbox.heldExpiryDays` setting as the judge-held pages from #429, and
  `mnemo inbox --restore KEY` brings it back. `mnemo extract` reports these as
  `backfill expired: N`. (#496)

- **A friction ledger records what contradicted the vault.** Every correction
  a session produces can now become a structured row in
  `<vault>/.mnemo/friction-ledger.jsonl` naming the rule it contradicts,
  instead of a line of briefing prose that marks no rule and retires nothing.
  Measured on the maintainer's vault, 97.9% of 1946 live rules have exactly
  one source and `mnemo replay` reports zero carried correction-backed
  injections — not for want of signal, since 100% of the briefings that were
  asked for corrections found some, at about 8.75 a day. This first piece is
  the on-disk shape alone (`mnemo.core.friction.ledger`): it runs no model,
  reads no rule and writes to no page. It follows `briefing-log.jsonl` — same
  telemetry switch, same 1 MiB rotation — and never raises, so a failed row
  never costs an extraction. A repeated `(session_id, quote)` is refused
  rather than appended, because the backfill that fills this ledger with
  history sweeps the same sessions on every rerun. Nothing writes to it yet;
  the contradiction pass, the backfill and `mnemo friction` follow.
  (friction-loop-wave1)

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

- **A contradiction pass names the rule a correction contradicts.**
  `mnemo.core.friction.candidates.rank` scores a correction's quote and rule
  against the project's **whole** eligible pool with the reflex index and
  BM25F, and returns the top 40 rules with their bodies. It does not use the
  `existing_rules` hint's `source_count`-ordered top 80, which covers only
  37.9% of mnemo's pool and settles a tie by slug order.
  `mnemo.core.friction.link.resolve` then makes one `claude --print` call
  (hooks off, #329) with its own prompt, which is separate from the
  consolidation prompt. The prompt makes the model label each rule as
  *contradicts*, *refines* or *unrelated*, and only *contradicts* counts, so
  a refinement never retires a rule. A slug the model invents is dropped. A
  missing CLI, a timeout or a malformed reply gives an unlinked result
  (`link_basis: "none"`) and never an exception, so the correction is still
  recorded. A link to a rule the reflex injected in that session is marked
  `extractor+injected`, which records corroboration without requiring it.
  Nothing calls the pass yet: the backfill and `mnemo friction` build on it.
  (friction-loop-wave2)

- **A rule a user correction contradicted can now be retired, and nothing
  about it is deleted.** `mnemo.core.friction.retire` writes three
  frontmatter keys on the contradicted page (`superseded_by`,
  `superseded_at`, `superseded_by_friction`) and `supersedes` on the page
  that replaced it, and changes nothing else; `undo(<friction id>)` puts both
  pages back byte for byte. A retired rule is no longer injected, and `replay`
  and `mnemo why` no longer count it: the reflex index marks it, and
  `candidates_for_project` is the only filter. `list_rules_by_topic` withholds
  it and counts what it withheld (`include_retired=True` returns it).
  `read_mnemo_rule` always returns it, opening with what replaced it and the
  quote that retired it. The existing-rules hint keeps listing it, marked
  retired, so the extractor does not learn the rule again from a fresh
  transcript. A retirement counts only when its `superseded_by_friction` id
  is in the friction ledger, so a hand-edited key retires nothing. Writes are
  refused when there is no replacement page, when they would create a
  supersession cycle, or when a run would retire more than
  `MAX_RETIREMENTS_PER_RUN` (5) rules. **Ships inert:** automatic retirement
  runs only when `friction.autoRetire` is `true` (default off), so
  `replay`'s historical counts do not move until you switch it on.
  (friction-loop wave 2)

### Changed

- **Reflex no longer goes silent when two rules are relevant at once:
  `reflex.thresholds.relativeGap` now defaults to `1.0` (off), down from
  `1.5`.** The gate treated a near-tie between the top two rules as an
  ambiguous prompt and injected nothing, yet the same near-tie is when the
  runner-up gets injected. Replayed over a real vault (2723 prompts), prompts
  with a near-tie turned out to be the *better* injections: 56% of them
  came from an earlier session, against 44% for prompts with a clear winner.
  Rules brought forward from earlier sessions went from 162 to 683. The
  absolute floor still decides whether anything is relevant at all, and at
  most two rules are still injected per prompt. Any value above `1.0` turns
  the old gate back on, and values you set in global config or in a
  per-project `reflex-config` file still apply. The calibrator's sweep now
  starts at `1.0`, so it can no longer raise a gap above the default without
  measuring the default. Briefing selection keeps its own gap of `1.5`,
  because it can carry only one briefing. With the gate off, the per-session
  cap of 10 injections is reached much more often. (#332)

- **The reflex calibrator targets carried injections, measured by `mnemo replay`, instead of a 3–12% emit rate.** The old band was never validated and could not tell a *carried* injection (a rule from an earlier session — the only thing the vault can claim) from *hindsight* (extracted from the session it fires in). It was also fitted while `relative_gap` was mis-posed, so it would have tightened a hand-loosened gate straight back: the live emit rate is 5.5% at gap 1.3276 and 6.7% at 1.5, both inside the band, but 20.0% at 1.15. `mnemo autopilot tune reflex` now replays the vault once per candidate value and reports the carried curve per project. (#333)
- **A threshold moves only for a peak strictly inside the safe range that clears the noise floor.** Measured over 2708 prompts, carried only falls as either knob tightens — on `mnemo`, gap 1.1 carries 78 and gap 1.5 carries 18 — so the curve has no interior maximum and hill-climbing it would just pick whichever bound the objective pointed at. The calibrator now says `monotone` and writes nothing, rather than proposing a number the data never chose. (#333)
- **`.mnemo/reflex-config.{project}.json` may carry `"pinned": true`, and the calibrator will never rewrite it.** For a threshold you have set yourself and measured; the gate reader ignores the key. (#333)

- **A dispatched child now ends itself: it reports, publishes when git says
  there is work, and stops.** The closing clause is rendered into both the
  issue prompt and the contract-piece prompt, because the opening prompt is the
  one message a child reads as the maintainer's own. The order is
  load-bearing — the closing report reaches the transcript first, the child
  asks `git` whether there is anything to publish (clean tree, own branch, at
  least one commit ahead of the base) rather than deciding it from memory, and
  the stop comes last. Only a stopped child fires `SessionEnd`, which is where
  its briefing is written; a child left running holds a few hundred megabytes
  and leaves no memory behind. (#349)
- **`mnemo dispatch --may` defaults to `pr`; pass `--may none` to withhold.**
  Every grant ever recorded in the vault was `push` or `push,pr`, and the
  empty default left children finished, unpublished and still running. The
  default lives at the command line only: every `core` entry point still takes
  `may: Grant = ()`, so a programmatic caller that says nothing still gets no
  grant. `--may none` restores the withheld prompt byte for byte, and `merge`
  is still refused. (#349)

- **`mnemo sessions`, `mnemo session`, `mnemo deliver` and `mnemo land` now
  print English only.** They had drifted into Portuguese, often beside English
  lines in the same file. The queue buckets are now `WAITING ON YOU`,
  `WORKING`, `DONE` and `ABANDONED`. `deliver` groups are `READY`,
  `NOT READY` and `FINISHED, NOT STOPPED`. `land` uses one vocabulary:
  `contract … (N pieces, in landing order)`, `CANNOT LAND`, `REHEARSAL`,
  `LANDING`, `contract landed`. A script that matched the old Portuguese
  headers needs the new ones. `--json` output is unchanged. (#355)

- **A child is still not asked to rate its confidence before it explores, and
  now there is a measurement saying why.** The proposal (#383) was that a low
  rating should comment and stop rather than build. The transcripts say the
  doubt worth acting on is the exploration's product, not a precondition: the
  four children that showed it early read it out of `gh issue view --comments`
  — which the opening prompt already puts first — and the only child that ever
  refused without touching its tree needed 29 tool uses to get there. The
  refusal, its numbers and the conditions under which it should be revisited
  are in `docs/specs/2026-09-19-dispatch-confidence-timing.md` and in
  `core/dispatch.py`'s module docstring. No prompt changed. (#383)

- **An inferred `reference` page no longer goes live on the model's say-so.**
  Extraction now asks a judge what each such page is — system knowledge,
  a transferable technique, a generic aphorism or a session narrative — and
  only the first two reach `shared/reference/`; the rest stage in
  `shared/_inbox/reference/` for review (and expire after 14 days
  unreviewed, undoably — #429). Pages already live are
  untouched and still reinforced. Measured on the 2026-09-22 audit sample
  (142 rules two blind raters agreed on), held-out half: 30 of 31 junk pages
  staged, 3 of 41 good ones; the live junk share goes from 43% to 3%. One
  extra model call per extraction chunk that yields reference pages, on
  `extraction.referenceGate.model` (`claude-sonnet-5`); set
  `extraction.referenceGate.enabled: false` for the old behaviour.
  Reproduce with `tools/measure_reference_gate.py`. (#417)

- **A reference page the judge held now leaves `shared/_inbox/` on its own
  after 14 days unreviewed, and `mnemo inbox --restore KEY` brings it back.**
  #417 staged the judge's generic and narrative pages for review, but the
  queue had no exit anyone took: 44 session-start offers over four days
  produced 0 decisions, so "staged" meant kept forever and invisible to recall.
  Such a page now carries `reference_gate: generic|narrative` in its
  frontmatter, and each extraction run archives the ones untouched for
  `inbox.heldExpiryDays` (default 14, `0` = off) under
  `shared/_archive/expired-<run>/`, marking the entry `dismissed` the way a
  drop does. The ledger records `expired`, not `dropped`, so `mnemo inbox
  --stats` keeps them apart from human decisions, and `mnemo extract` prints
  `reference expired: N`. Only the judge's pages expire, because its verdict
  is the measured one; evidence-gate demotions (128 of the 142 staged pages
  on the maintainer's vault) still wait for a human. `--restore` also undoes
  a `--drop`, and a restored page never expires again. (#429)

- **The reference judge now also judges the evidence gate's demotions, and every staged page it answered says what it decided.** A demoted feedback page still stages, because its quote was never found. It now also carries `reference_gate: generic|narrative|technique|system`, like the judge's own pages. A `generic` or `narrative` demotion expires after `inbox.heldExpiryDays`, and a `technique` or `system` one waits for a human. On the maintainer's vault that is 72 of the 130 staged demotions. The count `reference held` still means inferred pages the judge held, not demotions. To stamp demotions staged before this change, run `tools/measure_demotions.py --stamp`. It is a dry run until you add `--apply`. The expiry clock starts at the stamp. (#432)

- **The session-start staged-page offer now puts the pages worth a decision
  first.** It used to offer the oldest staged pages, and the judge rated most
  of the queue's 130 evidence-gate demotions generic or narrative (72) —
  pages that expire on their own — so most of the two daily slots went to
  pages the queue would shed anyway. A page stamped
  `reference_gate: technique|system` is now offered first, unjudged pages
  next, `generic|narrative` last, oldest first within each; the three offer
  bounds are unchanged. The offer bullet and the `mnemo inbox` listing show
  the verdict, e.g. `(5d, demotion, judge: system knowledge)`. (#433)

- **`mnemo --help`/`-h` now show the same curated command list `mnemo help` does.** Advanced and internal commands stay hidden by default (`mnemo help --all` still reaches them); nothing was removed, so every documented `mnemo <verb>` invocation still works exactly as before. The one-line description shown there, in `pyproject.toml`, and in the plugin manifest now agrees with the README's opening line instead of the unrelated "Obsidian that populates itself" tagline. (#437)

- **`mnemo twins show` prints each twin's closing report under its diff, and a
  pair records what reached either twin besides its prompt.** In #439's
  six-pair pilot, two twins committed nothing on purpose (the issue had
  already shipped; a schema change needed approval), and `show` said only "no
  commits — this run delivered nothing". It now prints the last text each
  twin wrote, scrubbed of its names like the diff, and the no-commit line
  points there. The first `show` of a finished pair (and any later one, for
  a pair shown before this) reads both transcripts and records answered
  questions and typed turns during the run, whether the sibling's tree,
  branch or id appears anywhere, and writes to Claude Code auto-memory;
  `mnemo twins prefer` lists them after the reveal. Over the six pilot pairs
  that found one pair a person answered, three where a twin named its
  sibling (via `ps`, `git worktree list`/`git branch`, or the sibling's
  memory note) and three with auto-memory writes.
  `tools/measure_child_pairs.py` counts them, and `--exclude
  human-input|sibling|memory` leaves those pairs out: without the answered
  pair the wall-time log-sd drops from 1.04 to 0.14. Twins now start with
  auto-memory off (`autoMemoryEnabled: false` in the child's own settings
  file, which a wake keeps), so neither reads the maintainer's memory nor
  leaves a note the other, or the vault, picks up; ordinary children are
  spawned exactly as before. Seeing the sibling is recorded, not prevented:
  hiding it would take separate clones or a sandbox. (#453)

- **The reflex judge's bar drops from 0.6 to 0.4, and `mnemo rerank --setup`
  offers the judge in the same run.** At 0.4 an on-point rule reaches the
  prompt on 57.6% / 34.6% of the prompts that hold one (Sonnet labels / Fable's
  where it labelled, Sonnet's elsewhere), against 36.0% / 28.6% at 0.6 and 48.8% / 21.8% for the lexical
  gates. It is the only bar that beats the lexical gates under both raters.
  The cost is noise among injected rules: 15% instead of 10% (209 injected
  on the 300-prompt sample instead of 111). A config that already sets
  `reflex.judge.injectAt` keeps its value. Once `--setup` has stored and
  tested the key, it prints the judge's own consent paragraph, which says
  your typed prompt leaves the machine, and asks `Turn on the per-prompt
  judge too? [Y/n]`. A yes to the list stage is never taken as a yes to this
  one. Off a tty or with `--key-stdin`, the judge stays off unless `--judge`
  is passed. Reproduce with `tools/measure_reflex_reach.py` and
  `tools/measure_reflex_gate.py`, both from cached labels. (#461)

### Fixed

- **`mnemo replay` now scores a delivered dispatch child against its repo's
  rules.** Once a child's tree (`<repo>-wt-<n>`, `<repo>-wt-c-<slug>`) was
  removed, replay filed its prompts under the tree's own name, which no rule
  is filed under, so they were ranked against the universal rules alone (11
  candidates instead of 409 on one repo). The live hook was never affected:
  while the tree exists, its `.git` pointer already leads to the repo. Replay
  now folds a removed child into its repo the same way `learn` has since #301.
  On the maintainer's transcripts, worktree-named sessions fall from 97 to 26,
  and prompts replayed as fired go from 1095 to 1117. A removed hand-made
  tree, or a child of one, still keeps its own name. (#334)

- **A hook matcher this version widened now reaches installs that already
  exist.** `mnemo init` writes each matcher once, so the `Read` that #271 added
  to `PreToolUse` never reached anyone already installed: enrichment kept
  running at roughly half reach — 23 notes over a week where the current
  matcher delivers 40 — while `mnemo status` called the hook healthy and only
  `mnemo doctor`, which is opt-in, could say otherwise. Session start now
  repairs the drift it finds, with the same narrow write `mnemo init
  --hooks-only` performs (mnemo's hook entries only; config, statusLine, MCP
  and other tools' hooks untouched, previous file backed up) and a line on
  stderr saying what changed. It runs once per distinct drift, so a matcher you
  narrow by hand stays narrowed; `install.autoRepairHooks: false` turns it off.
  `mnemo status` reports the drift and the one-line fix either way. (#337)

- **A retired rule no longer shows up on the vault's HOME dashboard or in
  `mnemo recall`'s session harness.** The recall harness copied the reflex's
  candidate filter instead of calling it, so it kept ranking rules the reflex
  had stopped serving, and its numbers no longer described what the reflex
  actually does. It now ranks the reflex's own candidate pool. It also stops
  expecting a retired rule, which it could never find and would have counted
  as a miss. The dashboard asks `is_retired` like every other surface that
  reads rule pages, so a retired rule's replacement is listed in its place. (#344)

- **`mnemo friction --backfill` no longer reads a harness probe as the user
  correcting a rule.** A session whose working directory is a background
  Claude Code job's scratch dir (`~/.claude/jobs/<id>/tmp`) was started by
  another session — "Use the Bash tool to run exactly this command: touch
  probe-file-86 . Do nothing else." — and two such turns had been linked to
  `run-git-commands-yourself`, a rule the user still wants, where
  `friction.autoRetire` could act on them. These sessions are now listed as
  *scratch session*, cost no briefing, and never reach the ledger; a plan
  saved earlier is not reused for them. On the maintainer's machine this sets
  aside 13 sessions, all probes or rehearsals, and takes the backfill from 14
  links to 12. Rows an earlier `--apply` already wrote stay in the ledger. (#348)

- **`mnemo doctor` no longer says the daemon keeps a finished child for 8 h.**
  Every `bg retire` line in `~/.claude/daemon.log` (2026-08-13 → 2026-09-16)
  shows 60–61 m in 34 of 45, 8 h only in 5 swept just after a daemon start,
  and 11–44 m in 6 under `[low memory]`. The doctor line now says "usually
  after ~60m idle, sooner on low memory", and the `deliver` docstring states
  all three figures: the chance to stop a child and get its briefing usually
  closes within the hour, not eight. (#350)

- **A resumed session no longer receives the last briefing a second time.**
  SessionStart read Claude Code's `source` only to label a log line, so
  `claude --resume`, a forked session and a compaction each got the whole
  `[last-briefing]` block again, even though the context already had it or
  had just been compacted to make room. The briefing now goes out only on
  `startup` and `clear`. `/clear` empties the context, so it counts as a
  cold start. `resume`, `fork` and `compact` get the topic envelope without
  the briefing. The `session_start.inject` access-log row now records
  `source`, so the effect can be read straight off the log. On the
  maintainer's transcripts, all 45 repeated briefings came from `resume` (44)
  or `fork` (1). (#352)

- **`briefing-log.jsonl` and `mnemo telemetry` now say which session received a
  briefing, not just which one wrote it.** A briefing row's `session_id` is the
  briefing's author. Newest-wins hands the same briefing to every session a
  project starts, so one `session_id` repeated 23 times meant 12 different
  readers, not one session briefed 23 times. Rows now also carry
  `reader_session_id` and `source` (`startup`, `clear`, …), and each
  `session_start.inject` row carries `session_id`. `mnemo telemetry` now labels
  its counts as starts and adds a distinct-sessions line alongside them
  (`distinct_sessions`, `distinct_sessions_with_briefing` in `--json`). Every
  read keeps its row; nothing is deduplicated. (#359)

- **A `!` shell command is no longer a correction.** A `<bash-input>` turn (or
  its `<bash-stdout>`/`<bash-stderr>` output) is the user acting on a shell,
  not telling the assistant anything, but `verify` accepted a quote found
  there: 10 of the 90 backfilled corrections were shell blocks, and three
  `gh pr merge … --admin` runs were linked as contradicting
  `merge-requires-admin`, the largest cluster in the ledger. `verify` now
  skips shell-mode turns, a reused backfill plan and `--apply` drop the ones
  an older sweep saved, and ledger readers (`mnemo friction`, retirement,
  `is_retired`) stop counting rows already written. The append-only ledger
  file is not rewritten. The issue's proposed timestamp guard was not built: no
  live rule page carries `modified`, and comparing against a page's last
  rewrite would have dropped 9 of the 14 links, mostly the genuine ones.
  (#360)

- **The reflex no longer withholds a rule from a session because a different
  session was told it.** The "already injected" cache was shared by every
  session on the vault for the whole day, so once one session got a rule,
  every other session that day was silenced on it, even when it was the
  strongest match. On the maintainer's vault that was about two thirds of all
  `deduped` silences (199 of 288), and the rules held back scored a median of
  16.7 against a floor of 2.0. The cache now works per session, as the reflex
  design spec intended: a session still hears each rule at most once a day,
  including after `--resume`, and other sessions hear it too. `mnemo why` now
  says "this session was already told it today". A cache written by an older
  mnemo has no session attached, so it is ignored, which allows at most one
  repeat per session until midnight. (#361)

- **The autopilot's telemetry checks can fire again.** `mnemo autopilot
  self-fix telemetry` looked for `llm.call` rows under an `event` key and read
  flat `cost_usd` / `prompt_tokens` fields, none of which the access log
  writes, so both checks always found nothing. They now read the row
  `record_llm_call` writes: `tool`, `usage.input_tokens`, and a new top-level
  `cost_usd` taken from what the claude CLI reported for the call (null when it
  reported nothing). The token check is renamed `input_tokens_zero` because an
  unreported count is written as 0. Rows logged before this change have no
  `cost_usd` and are skipped. (#370)

- **The reflex's day-scoped state now rolls over on the first access of any
  kind, not on the first MCP tool call.** Only `increment()` compared the
  stored date with today, so yesterday's injected-rule cache went on
  suppressing until someone used an MCP tool, and that call then reset the
  file — dropping every entry the hooks had written since midnight and
  handing the day's live sessions a rule they had already been given. The
  rollover moved into the loader every reader and writer shares, so a new
  day starts empty and nothing written today can be wiped by a later call.
  Yesterday's per-session emission counts no longer count against today's
  `maxEmissionsPerSession` either. (#374)

- **`doctor` now counts both things waiting in `shared/_inbox/`, and counts
  them honestly.** Plain staged pages — the evidence gate's demotions and
  multi-source stagings — were excluded from the backlog line on the grounds
  that they follow their own path, and that path has no consumer: they are
  not served to recall, not listed by `mnemo rewrites`, and not counted
  anywhere, while an unscoped `mnemo extract --force` deletes them. On the
  real vault that was 194 pages no surface showed. They get their own
  advisory line now, split by why each page staged, with the oldest age and
  the `--force` risk named. The rewrite line is unchanged in shape but no
  longer reaches outside `_inbox/<type>/`, where every writer stages: it had
  been counting archive copies an older `mnemo rewrites` left under
  `_inbox/proposals/` and `_inbox/rejected-<run>/` — 34 of 36 on that same
  vault — so `doctor` claimed a backlog `mnemo rewrites` showed nothing of.
  When proposals are staged that no longer have a live rule to merge into,
  the line says so. (#375)

- **Accepting several rewrites in a loop no longer drops all but the first.**
  `mnemo rewrites --accept KEY` derived its run id from a whole-second
  timestamp, and a single accept finishes well inside a second, so every call
  in a shell loop asked for the same archive directory. The first one landed;
  the rest hit the guard that protects an existing run's undo archive and
  aborted with `run ... already applied; undo it first` — 2 of 7 applied in
  the run that found this, with a tail that still read like a normal batch.
  The run id is now minted against the archive on disk and takes a `-2`, `-3`
  suffix when the second is already taken, so back-to-back accepts each get
  their own archive and their own `--undo` id. The re-apply guard is unchanged
  and still refuses a plan whose run has already landed. (#376)

- **A child the account's limit stopped is no longer shown as abandoned next
  to a hint that deletes its worktree.** `mnemo sessions` filed every blocked
  child whose process had died under **ABANDONED**, followed by `remove:
  claude rm <id>` — which removes the tree. On 2026-09-19 that was offered for
  six children the five-hour window had stopped mid-turn (#380-#385), five of
  whose trees held uncommitted work, and none of which was dead: all six
  finished with a PR once they were woken. Those now sit in their own
  **STALLED** bucket saying when the window reopens (`five-hour spent — free
  since 02:40`), with `resume: mnemo resume` instead of the `rm` hint. A child
  blocked on a question, a permission prompt, a login or a billing decision is
  untouched — it stays under ABANDONED, because what it needs is an answer,
  not a nudge. `mnemo sessions --json` carries the same reading as `stall`.
  (#393)

- **A page type the model invents can no longer create a `shared/<type>/`
  directory.** The type an extraction call returns is checked against the
  four real ones and falls back to the kind that call was asked for, which
  then meets that kind's gate; one live page had landed in
  `shared/measurement-before-design/`. (#417)

- **An archived non-feedback page stays archived.** Reclassify's archive step
  dismissed the extraction-state entry under `feedback/<slug>` whatever the
  page's type, so archiving a `reference` page left its real
  `reference/<slug>` entry live and the next `mnemo extract` wrote the page
  back. The dismissal is now keyed by the page's own type directory, as the
  merge step already was since #409. (#419)

- **Temp, pytest and probe directories no longer become agents in your
  vault.** Every `claude` session runs mnemo's global hooks wherever it starts,
  so a live test's `tmp_path`, a background job's probe in
  `$CLAUDE_JOB_DIR/tmp` or a scratchpad under `/tmp/claude-<uid>/` each filed
  a `bots/` directory, a log and a briefing — about 90 of 115 agents on the
  maintainer's machine. A session whose cwd is under the system temp dir
  (`tempfile.gettempdir()`, and `/tmp` on POSIX), a `pytest-of-*` base temp or
  a job's scratch dir now runs no mnemo hook at all — no log, briefing or
  extraction, and no reflex or enrichment either — unless the vault lives
  there too, as a project vault in `/tmp/mnemo-demo` does. Separately, the
  mirror skips a Claude Code `memory/` directory with no file in it (54 of 63
  on the maintainer's machine), which is what made the file-less agents.
  Existing directories are left alone; delete them by hand. (#420)

- **A subscription call is no longer reported as paid.** mnemo told a
  subscription call from an API-key call by the `apiKeySource` field of the
  `claude` CLI's output, and Claude Code 2.1.280 stopped sending it, so every
  call on a Pro/Max plan counted as paid: `mnemo extract` printed the CLI's
  notional price as a charge and `last-auto-run.json` said
  `"all_calls_subscription": false`. When the field is missing, mnemo now asks
  `claude auth status` once per process (`authMethod: "claude.ai"` is the
  subscription). An `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or Bedrock/Vertex
  switch in the environment still counts as API billing, because
  `claude auth status` says `claude.ai` even when `claude --print` uses the
  key. On a subscription the cost line now shows `≈$X API-price equivalent`
  next to "no charge". When billing cannot be told it shows
  `≈$X at API prices (billing unknown)`, never a plain charge. (#441)

- **`mnemo land` sees TypeScript and Rust definitions, not only Python
  ones.** A contract piece's `exposes` is checked by finding a definition
  of its name in the piece's files, and that lookup knew only `def`, `class`
  and a top-level assignment. A TS or Rust piece therefore always read as
  missing: mnemo-desktop's round 18 refused to land two of five green PRs
  over `export function mergePr(…)` and `export function dispatchIssues(…)`,
  both present. The check now also recognises `export function`/`const`/
  `class`/`interface`/`type` and Rust's `pub fn`/`struct`/`trait`, and still
  never counts an import or a call site. (#446)

- **On Windows, mnemo's background workers no longer flash a console window
  for every `git`, `gh` or `claude -p` they run.** The workers that session
  start and session end leave behind were started with `DETACHED_PROCESS`, so
  they had no console, and Windows opened a new visible one — stealing focus —
  for each console program they launched. They now start with
  `CREATE_NO_WINDOW`: a console that is never shown, which their children
  inherit. `CREATE_NEW_PROCESS_GROUP` stays, so a Ctrl-C in the session's
  terminal still never reaches them. Confirmed on a real Windows machine.
  (#452)

- **A dispatched child's report card now reaches a parent that another process
  also resumed, and a report that cannot be delivered says why.** A session's
  inbox address was "the newest row", so when a second process resumed the
  parent's id and then exited, every child that finished afterwards took the
  parent for gone and posted nothing, though it was still open: on 2026-09-22
  three of one parent's four children ended that way. The newest *live* address
  now wins. Every report that is not delivered, whether the parent is gone, the
  socket refused the write, the reporter did not start or it crashed, writes a
  `.mnemo/child-reports.jsonl` row with `delivered: false` and a `reason`, plus
  a `child_report.undelivered` entry in `.errors.log`. That entry does not count
  toward the hook breaker. A started reporter logs a `spawned` row first, so a
  reporter that dies before writing is visible too. (#454)

- **A dispatched child's report that never came is now named, not noticed by
  accident.** `mnemo doctor` lists every `mnemo child-report` of the last 7
  days that started and wrote nothing in 10 minutes, with the child, its
  parent and the reporter's pid. On 2026-09-23 two such reporters left only
  their `spawned` row, and the maintainer found one only because the child was
  missing from their queue. A reporter stopped by SIGTERM, SIGHUP or SIGINT
  now writes a `child-report killed by SIG…` row: the signal used to end it
  before any handler ran, and turned into an ordinary card when it arrived
  during `gh`. Every row a reporter writes carries its pid as `reporter`, so
  doctor pairs it with the hook's `spawned` row whichever lands first. (#460)

- **A tool's edit to a rule page no longer reads as your edit, so page updates stop piling up in `_inbox/` as `.proposed.md` files.** Extraction keeps a page you edited and stages its update beside it. It tells your edits apart by the page hash it recorded at its last write. Three tools changed tracked pages without moving that hash: the doctor self-fixer's `sources:` rewrite, `tools/measure_demotions.py --stamp --apply`, and the `slug:` stamp (its reconcile was overwritten when extraction saved its own state). On the maintainer's vault that left 1,192 pages looking user-edited, and their correct updates went into siblings. Now:
  - project pages record their source vault-relative, so the fixer has nothing to change;
  - every tool that edits a tracked page records the new hash, but only when the page was untouched before the edit;
  - `mnemo extract` re-baselines a page only when undoing those known edits reproduces the recorded hash exactly. Any other drift is left alone, because it may be yours.
  - An update that was diverted into a sibling is applied to its page. This covers project pages, and staged pages with no live page. The sibling then leaves the inbox.
  - A sibling is not re-written on every run while its source stays unchanged.
  - `mnemo doctor` shows how many pages the next extract would re-baseline. (#470)

- **The first session no longer announces staged backfill pages as learned.**
  The project phase reported a backfill page it staged in
  `shared/_inbox/project/` as written, and the learned ledger took written for
  live: on a fresh install over 44 prior sessions the first `[mnemo learned]`
  block listed 32 staged pages, each with a `mnemo disable-rule` line for a
  rule that was not live. Only pages that land in `shared/` are recorded now.
  (#471)

- **A dispatched child's report card no longer dies with the hook that
  started it.** Claude Code ends a `SessionEnd` hook still running at its
  1.5 s bound with a tree kill that follows parent pids from the hook and
  signals every descendant, whatever its session. `start_new_session` was no
  protection: while the hook was alive, `mnemo child-report` was its child,
  and three reporters on 2026-09-23/24 were SIGTERMed about a second after
  they started, so their parents never heard the child had finished. Every
  worker the hooks start in the background — the reporter, the briefing,
  extraction, unblock consumption and the `pr-follow` watcher — is now
  started from a short-lived intermediate that exits at once, so init adopts
  it before the hook can be killed. POSIX only; Windows spawns are
  unchanged. (#475)

- **`mnemo regen-graph-edges` and reclassify's keep and merge no longer make
  their own edits read as yours.** They changed tracked pages without moving
  `written_hash`, or moved it on a key built from the frontmatter slug. The
  ledger keys a page by its file stem, and the two differed for every keep on
  a real vault. So extraction stopped updating those pages and diverted each
  update into a `.proposed.md`. All three writers now go through the
  extraction lock and advance the hash on the page's own key, and only when
  the page was the extractor's bytes before the edit. The next `mnemo extract`
  re-baselines the drift they already left, but only where undoing the
  section rewrite, the keep, the merge append or a partial `sources:` swap
  reproduces the recorded hash byte for byte. On the maintainer's vault that
  heals 297 of 325 drifted pages. A page with an edit someone asked for stays
  theirs: an `aliases:` line, a hand merge, a redaction, a path fix. (#492)

### Security

- **Passwords and Google API keys no longer reach rules, briefings or the judges.** Redaction now knows a labelled password (``Password: `…` ``, ``password `…` ``, `senha: …`, `DB_PASSWORD=…`, `"password": "…"`), an unlabelled login pair (`` `qa@acme.io` / `…` ``) and a Google key (`AIza…`, 39 characters), and it now runs on every path that writes a `shared/` page or a briefing, not only on LLM extraction: project pages built from your mirrored auto-memory, session briefings, the evidence quote a page stores, pages staged by `mnemo import`, and autopilot's rule stubs. Those paths redact secrets only — a test-account e-mail is the identifier the rule needs and stays; the password beside it becomes `[redacted]`. Your own auto-memory files are never touched: the redaction happens in the page mnemo derives from them. The evidence gate compares quote and briefing under the same pass, so a quote still verifies whichever side was written before this change. Why: a real vault held production test-account passwords in live `reference` and `project` pages and a customer's password and a full Google key in briefings — text the opt-in rerank, reflex and dedupe judges send to a third party. (#418)
- **`mnemo redact` finds secrets already on disk.** It walks `shared/` and `bots/*/briefings/` and prints file, line and kind — never the value; `--apply` replaces them with `[redacted]` in place (under the extraction lock, keeping extraction's record of each page in step so the next run does not mistake it for your edit), and `--json` is machine-readable. Pages written before a pattern existed are never revisited by any writer, so run it once after upgrading. It is a command rather than a `doctor` row because the walk costs about 3.5 s on a 4,754-file vault, on top of a doctor that is already the slowest thing on the vault screen. (#418)

### Internal

- **`tools/measure_rerank_judges.py` grades a judge's ordering with labels a different judge made.** Ordering a bucket by the relevance numbers in `recall-qrels.json` and grading it with the same numbers scores 1.000 and says nothing, so the tool builds a second set of labels with `claude-haiku-4-5` through `mnemo.core.llm.call` (hooks off, no tools, the provider mnemo already uses) and reports only the crossed rows as results; a self-graded row is printed as `circular` and never starred. Run on 2026-09-19 over 38 logged queries and 1,202 pairs: ordering by `jev-1.13.0` and grading with Haiku's labels lifts nDCG@5 from 0.820 to 0.897, +0.077 [+0.029, +0.124]; an earlier run of the same comparison in the session that opened #401 gave +0.087 [+0.035, +0.152]. The mirror did **not** reproduce: ordering by Haiku and grading with Jev's labels gave +0.026 [−0.027, +0.074], against +0.062 [+0.014, +0.115] before — Haiku's three-level labels move between runs (539 then 550 "should read") and break ties differently. The read label does not move for any ordering (31 / 31 / 32 of 49). Building the labels is 82 calls and about $2.50; the run saves after every call and resumes, which the real run exercised twice. Only `--judge --send` calls anything. (#401)

- **`tools/measure_rerank_filter.py` asks whether the pair-reading judge is a better order or a better filter, and keeps the population it asked about.** The numbers behind #404 came from scripts in a session scratch directory and could not be re-run, which is what this fixes. `--mine` builds the population from session transcripts rather than replaying a ranking — every `list_rules_by_topic` `tool_use` that carried a `query`, paired with its `tool_result` by `tool_use_id`, the slugs it really returned (names resolved through `recall._name_to_slug` for lists logged before 2026-09-08) and the `read_mnemo_rule` calls that followed — and refuses to overwrite the units file every label on disk is keyed to. `--export-blind` / `--import-labels` run a blind 0/1/2 round for a rater that is not on this machine (a unit is never split across chunk files; an import with a gap or a 3 in it writes nothing), `--score` asks the judge in resumable chunks of 40, and the default report prints the filter table, the reranker rows with a paired bootstrap interval, and pooled average precision for "should read", on `--part dev|test|all`. The fused signal and its order come from `rerank.fuse` / `rerank.marks` / `rerank.ranked`, and a test runs the tool and the stage over the same numbers and compares, so the two cannot drift. Run on 2026-09-20 over 85 queried calls and 1,536 labelled pairs, it reproduces the test-split table in the docs; on the dev split where the bars were fitted, the shipped bar keeps 43 of 47 should-read rules and leaves one query holding one empty. It also prints the label noise the table inherits: of 107 pairs this rater answered twice, 84 came back identical. Only `--score --send` leaves the machine. (#404)

- **The test suite no longer starts a real `mnemo procedures --refresh` from
  every SessionStart test.** #397 added a detached spawn that the suite-wide
  guard did not stub, so 93 tests each launched one per run, with its cwd in a
  directory the test was about to delete — and Windows cannot delete a
  directory that is a live process's cwd. That was the worktree test failing on
  13 of the 16 Windows master runs since #397, none before. The guard now stubs
  the hook's one spawn function and is pinned by what it prevents. The inbox
  latency test's one-second flake is fixed too, with a pinned clock. (#408)

- **`tools/measure_generic_rules.py` makes the generic-rule detector reproducible, labels and all.** On 2026-09-19 a calibrated judge was reported to separate generic aphorisms from real rules at AUC 0.936 on a held-out n=60, with a filter that flagged 8 of 60 and lost no project-specific rule; the scripts, the blind labels and the scores lived in a session scratchpad that was deleted, so per `CLAUDE.md` none of that could ship a claim. `--sample` draws live `shared/feedback` + `shared/reference` rules stratified by project (`--per-project`, `--seed`) — four projects hold 76% of the vault, so an unstratified draw would grade the detector on one writer's habits — freezes the name and `rerank.rule_text` of the body a rater will see into `<vault>/.mnemo/generic-sample.json`, and refuses to redraw once any rater file holds a label. `--export-blind` / `--import-labels --rater NAME` run a blind 0/1/2 round (0 generic, 1 specific practice, 2 project-specific) for a rater that is not a browser: the export carries the id, the name and the rule and nothing else, and an import with a gap or a 3 in it writes nothing. `--score` asks the judge in resumable chunks through `rerank.typesafe_client` and `rerank.resolve_key`, under `QUESTION_VERSION`, keeping both wordings — the pre-registered "what would an engineer lose if this rule were deleted", written as a three-level `score` question whose levels mirror the labels, and the cheaper "names a concrete artifact" — as named variants so the comparison can be re-run instead of remembered. The default report is local: AUC generic-vs-rest and project-specific-vs-rest per variant on dev, test and all; a threshold table fixed on dev and read once on test, counting what is flagged, what of it is truly generic, and the project-specific rules wrongly thrown away; label counts; and, once two raters exist, their exact and off-by-two agreement, which bounds every other number in the report. A local baseline runs beside the judge and needs no request: generic rules name nothing, so the count of backticked identifiers and path-like tokens is a detector, and a judge that cannot beat it is not worth its key. Drawn on this machine on 2026-09-20: 87 of 1,782 live rules over 17 projects, 41 dev / 46 test, ~29k tokens (~$0.0012) to score. Only `--score --send` leaves the machine; no label and no score has been produced yet. (#410)

- **`tools/measure_rerank_reads.py` counts what an agent read against what the rerank stage marked.** Per judged list, as (rule, list) pairs: marked, judged but not marked, shown but never judged, and how many of each were read — so marked-then-read, unmarked-then-read and marked-never-read — with a Wilson interval per rate and, as a position-only reference rather than a control, the read rate by rank in queried lists that carried no marks. A read is credited to the latest earlier list in the same session that showed the slug, any list queried or not. The default source is the access log (a judged row from before #416 is counted as unauditable, never guessed at); `--transcripts` reads the marks from the tool results in `~/.claude/projects` instead, which is how the calls logged before this change can be audited — on 2026-09-22 it found the same 13 judged lists the log holds, with the same shown / judged / marked counts on every one. First run (`--transcripts --days 7`, 13 lists in 8 sessions): 10 of 39 marked rules read (26%, [15%, 41%]), 2 of 165 unmarked (1%), 3 of 50 top-three rules in unmarked lists (6%); marks and position are confounded, and one of the 8 sessions is the one that wrote the tool. A test runs a judged list and two reads through `handle_request` and reports on the log they leave, so the writer and the reader cannot drift apart. `measure_rerank_filter.result_marks` is the shared parser for a result's `relevant` flags. Reads files only; nothing leaves the machine. (#416)

- **`tools/measure_rule_lift.py` measures whether an injected on-point rule changes the agent's answer.** Each of the 64 (prompt, rule) pairs a blind rater scored "inject" (#411) is answered twice by the same model with no tools, once with the prompt and its previous assistant turn alone and once with the rule appended the way the `UserPromptSubmit` reflex injects it. A judge that cannot see which arm it is reading then asks whether each answer acts on the rule. First run (1 sample per arm, `claude-sonnet-5`): follow rate 45.6% → 75.4%, lift **+29.8 pp, 95% CI [+12.3, +47.4]** over 57 pairs. That falls on the pre-registered "rules carry" branch. A second sample per arm (`--samples 2`) confirms it: **+31.1 pp, CI [+19.7, +42.6]** over 61 pairs. Per-rule lift is still too thin to retire anything, because 43 of the 52 rules have a single pair. `--dry-run` prints pairs, calls and tokens and makes no model calls. Runs are resumable after every call; results go under `<vault>/.mnemo/rule-lift/`. (#434)

- **`tools/measure_reflex_reach.py` measures how often an on-point rule reaches the prompt, and where the rest is lost.** It covers the 300 prompts #411 sampled, re-ranked against today's vault down to rank 10 with no hindsight. Every pair is labelled blind 0/1/2 by `claude-sonnet-5` through `core.llm`, with batched calls saved after each one and resumable. The shipped gate is replayed session by session through the hook's cap and dedupe. Each prompt's loss is charged to ranking, gate, cap or dedupe. First run: 72 calls at an API-price equivalent of $5.99. 172 of 290 prompts hold an on-point rule in the top 10, and 20% of those hold it only below rank 3. Sonnet and #411's Fable labels agree poorly on what counts as on-point (kappa 0.38), so every row is also read under Fable's labels. Under both, the judge at `injectAt` 0.4 reaches more of these prompts than the shipped gate (57.6% vs 48.8%, and 34.6% vs 21.8%). Whether the judge at the shipped 0.6 beats the shipped gate depends on the rater. `--dry-run` makes no calls; results go under `<vault>/.mnemo/reflex-reach/`. No product change. (#455)

- **`tools/measure_demoted_keeps.py` checks whether the reference gate's system/technique verdict holds on the pages the evidence gate demoted.** These pages wait in `shared/_inbox/` indefinitely, while a directly emitted reference page with the same verdict goes live. The tool reads the population from frontmatter (`demoted_from: feedback` plus `reference_gate: system|technique`) and draws a seeded sample of 40. Two blind raters, `claude-opus-5-5` and `claude-fable-5-1` (neither is the gate's model), each label a page good or junk using the gate's own definitions. The report gives each rater's good rate, the good rate under both (with 95% intervals), kappa, and the verdict against a bar declared before labelling: 85% good under both. It never uses more than 20 calls, it resumes, and `--dry-run` makes no calls. It writes only under `<vault>/.mnemo/demoted-keeps/`. First run: 67 pages. 29 of 40 were good under both (72.5%, [57.2%, 83.9%]), kappa 0.43, so the bar **fails**: do not promote these pages on the gate's verdict alone. (#465)

- **`tools/measure_day_one.py` measures what a new user's empty vault carries on day one, with and without Claude Code history.** It replays one repo's real transcripts through two fresh vaults, with mnemo's vault and config isolated in a temp dir and the judge off. Arm (a) runs the real `backfill --install-run`, then the first extraction, then the next 50 prompts. Arm (b) feeds sessions one at a time through the SessionEnd path (extraction when the debounce passes on the transcript's clock, then the briefing) and records live pages, the SessionStart payload, and the reflex emit and on-point rates (#411 rubric) per session. `--dry-run` bounds the calls and cost first, `--send` meters every call under a 200-call budget and resumes. First run, on 88 clubinho sessions, 127 calls: the install backfill leaves **0 live rules** (all 56 pages it yields stage for review), so the reflex fires on 0 of the next 50 prompts. Without history, the first on-point injection comes at **session 24**, and only 11 of 328 injected rules over 88 sessions are on-point. No product change. (#467)

- **`tools/measure_day_one.py` gets an arm (c): what Claude Code's own auto-memory carries on day one, and arms (a)+(c) together.** A real SessionEnd mirrors `~/.claude/projects/<project>/memory` into the vault. Arm (c) rebuilds that directory as it stood at the install point from every transcript's Write/Edit records (`originalFile`), and reports the files it could only approximate. It runs the real `mirror_all` over that snapshot alone, then the first extraction, the SessionStart payload, and arm (a)'s 50 prompts, with the judge off. `--arm c` has its own 60-call budget, and `--dry-run` prints the bound first. On clubinho, 17 calls: 90 files at install mirror to **81 live pages**, because 80 `project` files go live directly without a model or a review. The reflex then fires on 7 of 50 prompts, and 2 of its 11 pairs are on-point. With the backfill added, arms (a)+(c) fire on 6 of 50, with the same 2 on-point pairs. No product change. (#472)

- **`tools/measure_backfill_routes.py --live` rates backfill pages where the
  extraction put them.** Since #471 a backfill page that clears the normal gates
  is written live, so the tool's staged-only reading could no longer see the
  pages it was meant to rate. The second-corpus check (clearframe, 40 live
  pages) came out at 29/40 = 72.5% good under both raters [57.2, 83.9],
  κ 0.53, below the 85% bar #471 declared. (#477)

- **`tools/measure_day_one.py --judge` measures day one with the Jev judge on.** It re-extracts nothing. It replays the saved arms' prompts through `reflex.judge` as the hook runs it (the real `judge.ask`, shipped settings, gate fallback on failure). `reflex.replay.run` gains an opt-in `judge=` stage for this, run in the hook's order. Arm (b)'s vault is rewound to each session from `learned.jsonl`. On clubinho, over 69 sessions, the judge cuts the noise in what the reflex injects from 77% to 21% (18 of 87 pairs) and doubles the on-point pairs (16 → 32). That is one pair short of the 20% bar declared beforehand. (#479)

- **`tools/measure_noise_concentration.py` measures how few rules cause the reflex's noise.**
  It reads injections and labels that already exist (day one's arms, #411, #455)
  and rates the top 20 noisy rules of each corpus with #465's G/N/S/T/W rubric. On
  a day-one vault with the judge off, 15 generic `feedback` rules cause 83% of the
  noise and make 3 of 11 on-point injections. In the mature vault, noise is spread
  across system-knowledge pages, and only 10–18% of it comes from generic rules.
  No existing signal (gate stamp, friction ledger, #410 sample, emission count)
  picks out the generic rules without also hitting the on-point ones. (#480)

- **`tools/measure_judge_noise_kinds.py` asks whether generic rules still make
  day one's noise with the Jev judge on.** It reads #479's judge-on replay
  (no Jev request), types every injected rule with #480's two raters, and
  runs the counterfactual twice: the G/N rules' pairs struck out, and the
  prompts replayed with those rules retired, the judge answered from #479's
  cached scores. On clubinho arm (b) 17 of 18 noise pairs are generic
  `feedback` rules, but so are 20 of 32 on-point pairs; striking them clears
  the 20% bar (4.2%), yet the replay moves 468 never-scored pairs into the
  pools against a headroom of 4 noise pairs, so the bar is not decided. (#484)

- **`tools/measure_project_gate.py` runs the shipped reference gate over the backfill project pages #471 and #477 already had rated.** Project pages go live with no gate, and they were the weak route on both corpora. With the gate in front of them: clubinho 26/30 = 86.7% [70.3, 94.7] good under both raters, over the 85% bar, with no good page held; clearframe 20/24 = 83.3% [64.1, 93.3], under it, holding all 4 narratives and 2 good pages. Both results are one page from the bar. No project wording was tried, because clubinho left one catchable junk page to tune on, so the routing stays as it is. (#485)

- **`tools/measure_day_one_gate.py` asks whether the reference gate would cut day one's noise, and it would not.** It runs the real `reference_gate.judge_pages` over the auto-memory mirror's 80 live project pages (arm (c) of #472), then replays the same 50 prompts without the pages it calls generic or narrative, judge off and on, reusing #479's Jev answers where a prompt's pool is unchanged. On clubinho the gate holds 7 of 80 (N 7, S 64, T 9): project status pages are system knowledge by its own definition. Noise moves 82% → 80% judge off and stays 29% judge on, and the one held page that carried an on-point injection is lost. Eight of the nine noise pairs come from pages the gate keeps, so day one's noise is a relevance miss, not a category the gate sorts. No extraction change. (#486)

- **`tools/label_recall_pairs.py` keeps the one relevance check that does not come from a model.** Both sets of labels the ranking is graded with are a model's; the 60 pairs labelled blind on 2026-09-19 that validated the first judge lived in a session scratchpad and were deleted with it. The tool draws 60 pairs stratified by the first judge's score (12 per fifth, seeded), serves them one at a time on `127.0.0.1` with the second judge's question and 0/1/2 scale — the page shows the task and the rule, never a score, a stratum or a slug — and writes `<vault>/.mnemo/recall-labels-human.json` through a rename after every answer. Run with no flag it grades both judges against the labels so far: AUC for "should read" and "any relevance", the first judge's two confident-and-wrong counts, and the second judge's confusion. Each pair freezes the text the rater saw and the score it was drawn on, so re-judging cannot move the report, and `--sample` refuses a file that already holds a label. Nothing leaves the machine.

- **`tools/measure_demotions.py` asks the reference judge about the pages
  the evidence gate demoted.** #429 lets the judge's own generic and narrative
  pages expire from `shared/_inbox/` and left demotions out, because nobody
  had measured them. The tool freezes every staged `demoted_from: feedback`
  page into `<vault>/.mnemo/demotion-judge/sample.json`, as the judge would
  read it: `reference_gate.view` of the name and body, with no Sources
  section. It asks the stage's own prompt and parser, ten pages a call,
  resuming from `scores.json`, and reports verdict counts, what would be held,
  a per-project split, and the pages the judge would keep. Run on 2026-09-22
  over 130 demotions with `claude-sonnet-5`: G 69, N 3, S 41, T 17. That is
  72 (55%) held and 58 (45%) kept. None were 14+ days old yet. 13 calls on a Max plan,
  so no charge: $0.95 is the CLI's API-price equivalent (#441). The verdict is not the truth: on the audit's held-out half the same
  judge staged 30 of 31 junk pages and 3 of 41 good ones.

- **`tools/measure_jev_dedupe.py` measures whether a calibrated judge sees the synonym duplicates Jaccard cannot.** #187 showed a real duplicate scoring 0.136 against a noise p90 of 0.131, so no threshold on token overlap separates them. The tool asks TypeSafe's `jev-1.13.0` one three-level `score` question per pair of rules in a topic bucket and prints each pair's score beside its Jaccard ratio and its rank by Jaccard. On `mnemo`/`measurement` (26 rules, 325 pairs, 2026-09-19) the gate catches 0 pairs and the judge puts a four-rule "measure before designing" cluster at the top, at Jaccard 0.13–0.20. It reports no precision or recall, since a vault has no labelled duplicates: the output is a queue for a curator. Nothing leaves the machine without `--send`, which posts rule bodies to a third party and needs `TYPESAFE_API_KEY`; nothing in `src/` calls it. (#187)

- **`tools/measure_recall_judged.py` grades the ranking against judged relevance instead of against what happened to be read.** `mnemo recall` calls a rule "expected" when the agent read it after a list call; measured on 2026-09-19 the agent picks from slugs alone, 56% of reads come from the top 3 of the list it was shown, and the label is silent about rules that should have been read and were never seen. The tool asks TypeSafe's `jev-1.13.0` one yes/no question per (query, rule) pair, saves the answers to `<vault>/.mnemo/recall-qrels.json`, and from then on evaluates any ordering locally (nDCG@5, precision@5, should-read rules in the top 5, beside the read-based count). The judge was checked first: AUC 0.822 for read against not-read inside a list, and 0.913 / 0.943 against 60 pairs labelled blind. On 38 logged queries (1,202 pairs) the query rerank lifts nDCG@5 from 0.574 to 0.770, the gate is flat from 0 to 3, and 22 of 46 should-read rules sit outside the top 5, 20 of them scored and outranked, not lost to a vocabulary gap. Only `--judge --send` leaves the machine; it needs `TYPESAFE_API_KEY`, and nothing in `src/` calls the tool.

## [1.6.0] — 2026-09-15

### Added

- **A repo can carry its rules, and a second vault can take them: `mnemo
  publish` and `mnemo import`.** A vault was one person's; `export` was
  one-way and lossy by design. `publish` writes the rules attributed to the
  repo you are in to `<repo>/.mnemo-shared/<type>/<slug>.md` in a portable
  shape — the evidence quote kept verbatim, every vault path and `enforce`
  block dropped, provenance as an opaque per-vault id (created on first use
  at `.mnemo/vault-id`, never a hostname or a git identity), `user` pages
  excluded by default — and prunes only the files it wrote itself, so two
  contributors publishing into one tree never delete each other's rules. A
  manifest under `.mnemo/share/` lets `mnemo status` and `mnemo doctor` say
  when the tree is behind the vault. `import [PATH]` stages every rule into
  `shared/_inbox/<type>/` — never `shared/` — with `origin: imported`,
  `confidence: verified-elsewhere` where it was published verified, and the
  local project stamped in `projects:`; a ledger skips what was already
  staged and what came from this vault; a slug that is live becomes a
  `.proposed.md` rewrite for `mnemo rewrites`, and one that exists under
  another type is refused by path. `mnemo export` renders an imported rule's
  quote as another contributor's, not the reader's. Built as a three-piece
  contract (`docs/superpowers/contracts/2026-09-13-share-rules.md`) and
  landed with `mnemo land --merge`. (#245)

- **Every subcommand the parser offers now has a handler, by test.** The two
  commands above parsed on master and printed `unknown command`: neither
  piece's `files:` listed `cli/commands/__init__.py`, whose import list is
  what makes `@command` run, and every test imported the module directly.
  `test_every_subcommand_has_a_handler` walks the built parser's choices
  against the registry. (#245)

- **`mnemo reverify` gives the evidence gate the input it was missing.**
  The label-only half of `mnemo replay`'s split — sixty `confidence:
  verified` pages that `mnemo reclassify` labelled against raw transcript
  turns — cites briefings written before `## Corrections` existed, so
  today's gate fails every one of them mechanically, real corrections
  included. `mnemo reverify` re-briefs each of those sessions once through
  the ordinary briefing path (the prompt as it stands today, into a scratch
  root, one Haiku call per session) and looks the page's quote up in the
  regenerated Corrections; a tighter span of the same turn counts, and the
  page then keeps the briefing's span. Dry run by default, printed per page
  — *verified now*, *still fails*, *quote too short*, *no transcript*, *no
  briefing cited* — and saved to `.mnemo/reverify-plan.json`; a rerun reuses
  the scratch briefings (`--fresh` to re-brief). `--apply` executes
  exactly what the dry run showed, without a second LLM pass: a verified
  page gets the bare briefing path as its `evidence.source` and the
  regenerated briefing installed, so `page_verifies` passes from then on; a
  page that was re-briefed and still fails is demoted to `reference` with
  `demoted_from: feedback`, the marker reclassify and the extractor share.
  A page whose transcript is gone is reported and never touched. Same
  archive and manifest as reclassify; `--undo ID` restores every file, the
  swapped briefings included. Two latent bugs in reclassify's frontmatter
  surgery fixed on the way: a second keep duplicated the `evidence:` block,
  and a demote left the page's own `confidence: verified` line behind the
  new `confidence: inferred`, which the parser then preferred. Dry run on
  the maintainer's vault: 60 pages, 28 sessions re-briefed (22 pages have
  no transcript left), 2 verified now, 32 still fail, 3 too short. (#257)

- **`mnemo dispatch --model <id>` picks what a child runs on.** Every dispatched child used to take whatever `~/.claude/settings.json` resolved to, so the child that designs a decomposition and the child that sweeps a mechanical fix across 44 call sites cost the same — measured across 21 children in one day, all on the same top-tier model, none of it chosen. The flag applies to every child of the invocation and takes what `claude --model` takes (an alias like `haiku`, or a full id); mnemo keeps no list of valid models, so an unknown one fails in the child's own startup where the error names the real vocabulary. `--dry-run` prints what each child would get before anything is spent. (#268)
- **A contract piece may carry `model:`, which wins over the flag.** The contract is written and reviewed knowing what each piece is, so a blanket typed at the command line does not silently re-price the pieces someone already thought about. Optional: every contract written before this parses unchanged. (#268)
- **`mnemo session <id>` names the model a child is on, and `mnemo sessions` ends with the models in play.** Read from the session's own `respawnFlags` — where Claude Code records it even when nothing was passed, because it resolves the machine default into them — not from the sibling `model` key, which is `null` on every real session. The queue says it once in a footer rather than once per row: real rows already run 87–135 columns, and the same id repeated down the table would buy nothing. (#268)

- **`mnemo sessions` shows what each finished child spent before its first edit, and sums it.** Each PRONTAS row ends with `32u/+107k`: tool uses before the child first changed its working tree, and how much context that added over the first turn's baseline. A line under the table totals the finished children, and `mnemo session <id>` prints the baseline, how many reflex rules the opening prompt carried and which edit ended the count. `--json` carries it per session under `exploration`. "Changed the tree" is not "called `Edit`": on the 50 dispatch transcripts on disk, 15 children first edited through `Bash` and 5 others first called `Write` on a file outside the repo, so counting tool names got 15 of 50 wrong. (#269)
- **`tools/measure_exploration.py` splits that number by reflex injection.** First result, over those 50 children: a median 22 tool uses and +45k tokens before the first edit with no rule injected (n=38), 27.5 and +41k with one (n=6), 25.5 and +46k with two (n=6); Spearman +0.11 for rules against uses, n=50. Nothing here says the injected rules replaced exploration. It is a correlation over what happened, not a test, and every child also gets the SessionStart briefing whatever reflex does. `--list` prints the edit chosen for every transcript so the Bash classifier can be checked by eye. (#269)

- **CI is a second source of corrections, and a red-then-green run now teaches
  one.** The vault had one correction channel — the user typing one — and
  `mnemo replay` says what that yields here: eighteen gate-verified
  correction-backed rules out of 1830 pages, none of which ever carried into a
  later session's prompt. Meanwhile CI corrected the model four times in the
  same class inside a fortnight (`'charmap' codec can't decode byte 0x90`,
  `[WinError 2] The system cannot find the file specified`, `assert 312 == 310`,
  a cp1252 `SKILL.md` drift), each with an exact quote, and each became a rule
  only because the maintainer wrote one by hand. When a branch's CI goes red
  and a later push turns it green, `mnemo.core.ci_corrections` now extracts the
  failing test, the assertion text as the quote, and the files the fix touched.
  A red run with no green successor is not a pair and teaches nothing; a
  failure still present in the green log is dropped, so a flaky test mints
  nothing. The `gh` path sits behind `autopilot.network.enabled` like every
  other GitHub call and refuses before anything leaves the machine, while the
  same extraction runs against a local `pytest` red→green pair with no network
  at all. (#272)
- **A log quote is its own evidence class: `confidence: verified-ci`.** A quote
  a runner printed is not the user's words, so it gets a distinct value, the
  way `verified-elsewhere` marks an imported rule. Every surface that speaks in
  the user's voice compares `== "verified"`, so the "learned since your last
  session" banner, `mnemo learn` and `mnemo status` all exclude it unchanged,
  and publishing a CI rule downgrades it to `inferred` rather than letting it
  hop to another vault as something a person said. CI pages are staged as
  `reference`, not `feedback`: the evidence gate demotes an unverifiable
  `feedback` page *and nulls its evidence*, which would strip exactly the quote
  this feature exists to keep. The gate for human corrections is untouched and
  is never asked of a CI page — its quote is checked by the run that printed
  it. (#272)
- **`mnemo replay` gains an origin axis: user or CI.** The counts that matter —
  correction-backed rules in the vault, and carried correction-backed prompts,
  injections and distinct rules — are now split by which channel did the
  correcting, at every level, with the two halves guaranteed to sum to the
  whole. The vault line keeps saying "cite a correction you typed" while the
  user is the only source, and only widens once a red run has actually taught
  something. (#272)

- **`mnemo stale` finds live rules that cite a file the repo no longer has.** A rule written against `src/parser.py` keeps pointing there long after the file became `src/mnemo/cli/parser.py`. Run it inside a project: it checks every path cited by the live rules attributed to that repo against `HEAD`, and when a path is gone but its basename is unique it says where it moved to. Read-only, with `--json` for scripting; `mnemo doctor` grows a row that counts the same thing. On this vault it reports 26 of 1728 live rules across six repos (1.5%). (#274)
- **It checks cited paths and deliberately not cited symbols, and the report says so.** #274 also proposed checking backticked identifiers. Measured first: of 153 identifiers absent from `HEAD` across six repos, **none had ever been defined in that repo's history** — every one was third-party vocabulary (`login_customer_id`, `period_end`, `GetMessage()`), a name the rule was proposing ("create a helper, e.g. `getFoo()`"), or one it said had been deleted. Nothing in the token separates those from a real rename, so identifiers are counted as skipped rather than flagged, and `mnemo stale --why` explains the refusal. There is no `--apply`: with the finding this rare and this specific, the useful output is a pointer to re-read the rule, not a frontmatter stamp. (#274)

- **`mnemo doctor` names what the Claude Code daemon is holding in memory.** One row splits the daemon's idle pre-warmed spare from the background children it keeps resident, with MB, age and state for each — `✓ claude daemon: 1 idle spare (146 MB, oldest 5m); 7 children resident (2.19 GB: 2 blocked, 2 done, 3 working)` — and lists the finished children still resident with `claude stop <id>` as the way to let one go. It warns when there is more than one idle spare, or a spare the running daemon does not own. Read-only; silent on Windows and wherever no daemon roster exists. (#280)
- **Dispatch does not warm a pool of spares, so mnemo retires none.** #280 read `ps | grep bg-spare` during a freeze as 12 orphaned spares. Measured against `daemon.log`, `daemon/roster.json` and `ps`: the daemon keeps **one** idle spare and replaces it the moment a `--bg` claims it (86 of 86 claims), and a claimed spare keeps its `bg-spare` argv for life — so that grep counts running children as spares. The 1.19 GB was five working children plus one spare; the "1d19h orphans" were a `done` child from the day before, which the daemon itself retired under low memory. Killing the spare only makes the daemon spawn another, and no CLI flag caps the pool; the assumption is recorded as `daemon-spare-pool` in `mnemo.core.claude_cli` and checked by the live test. (#280)

- **`mnemo sessions --json` names the session that dispatched each child.** Every row carries `parent_session`: the full id of the Claude Code session that ran `mnemo dispatch`, read from the `CLAUDE_CODE_SESSION_ID` its Bash tool exports, or `null` for a dispatch run from a plain terminal. Nothing Claude Code writes links a `--bg` child to its parent, so a consumer summing children's tokens onto the session that spawned them had only a guess. The link is kept in `<vault>/.mnemo/dispatch-parents.jsonl`, not in the child's worktree as first proposed: on the machine this was measured on, 36 of 43 dispatch children, and 34 of 37 finished ones, had already lost their worktree while their job, and its token count, were still on disk. Only the parent is recorded. The issue or piece still comes from `cwd`, and the start time from the job's own `createdAt`. (#288)

- **`mnemo sessions` now shows what fills a session's context, per tool.**
  `sessions --json` adds `context_breakdown: {tool: tokens}`, the tokens each
  tool's results put in the context. A row gets a `Bash 46%` suffix only when
  one tool (MCP tools counted per server) fills at least 40% of the context.
  Otherwise the row stays as it was. On the 24 jobs on disk that bar flagged
  one. The numbers are not `/context`'s. `/context` counts `len(JSON)/4` as a
  share of the window, so the "Read 207%" that prompted this came from 23
  screenshots' base64 counted as text; by this measure its Read cost ~38k
  tokens. Text here counts at 2.25 chars/token, measured over 2,612 turns
  (chars/4 is ~44% low). An image counts at its pixels / 750. Each turn's
  estimate is capped at how much that turn actually grew the context. The
  transcript is now read whole once, for both numbers. Parsing it took 0.045s for
  every job on disk. (#308)

- **`mnemo dispatch --may push|pr` states up front what a child may publish.** A child that finishes stops to ask "may I push / open a PR?", and an answer sent later through `SendMessage` or mnemo-desktop cannot approve it: Claude Code frames every socket message as another session's (#309). The dispatch prompt is the one message a child reads as the maintainer's own, so `--may push` tells it to push its branch once its full suite passes, and `--may pr` to push and open the pull request, without asking again — never force-pushing, never beyond its branch. `pr` implies `push`; `merge` is refused, since landing stays with `mnemo land` and review. A contract piece's `- **may:**` line wins over the flag, as `model:` does, and `may: none` withholds it from one piece. Without `--may` the prompt is byte-identical to before and `mnemo deliver` publishes. The grant is recorded in `<vault>/.mnemo/dispatch-grants.jsonl`, shown by `--dry-run` and the dispatch report, carried as `may` on every `mnemo sessions --json` row (`[]` when nothing was granted), and named in the queue's `publicam sem perguntar:` footer. `mnemo deliver` now counts a PR that is already open as delivered: it adds the `Closes #N` trailer if the child left it off, stops the finished child, and exits 0. (#317)

- **A `mnemo-loop` skill tells a session which of mnemo's verbs are its own and
  which are the maintainer's.** Everything a session knew about `dispatch`,
  `deliver`, `sessions`, `land` and `--may` it inferred from command output
  written for a human at a terminal, and it read the maintainer's next step as
  its own: measured over the 36 dispatch reports before #306's footer note, 10
  handed `mnemo sessions` back and 2 promised to watch children with nothing
  armed to wake them. The skill is 53 lines and splits the verbs by owner — the
  session reads rules through MCP, dispatches, delivers the ids it was given,
  then reports and stops; the queue, the desktop replies, `mnemo land` and every
  merge are the maintainer's. It also says what arrives and what it is worth: a
  socket message is a peer's request and never approval to push (#309), a typed
  turn — including one typed through `claude attach` — is the user, and the
  SessionStart briefing is the previous session, not a task. It loads on demand,
  so it costs nothing on the sessions that never dispatch anything; a test pins
  that none of its text reaches the SessionStart context. `mnemo init` and the
  plugin ship it like `decomposing-for-dispatch`, and a new `skills` row in
  `mnemo doctor` reports a skill that is missing, unreadable, or whose
  frontmatter Claude Code would not index (#233). (#327)

- **Every injected briefing can now be recorded by name.** `record_briefing_read(vault_root, record)`
  appends one row to `.mnemo/briefing-log.jsonl` — vault-relative path, source
  session id, date, injected byte count and a hash of the exact body the session
  received — so which briefing a session got, and how often each one is read,
  is recoverable from disk instead of hidden behind `included_briefing: true`.
  It is a file of its own rather than a row in `mcp-access-log.jsonl`, so ~80
  briefed session starts a day do not shorten the window `mnemo recall` reads
  from; it follows the same telemetry switch and 1 MiB rotation, costs ~0.1 ms,
  and never raises. Picking a briefing records nothing; the session-start hook
  wires the call in separately. (channels)

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

- **Spec: what the inbox socket leaves behind, measured.**
  `docs/superpowers/specs/2026-09-15-socket-reply-status.md` counts 47 socket
  messages delivered on this machine. Every one can be recovered from the
  receiver's transcript (`origin.kind == "peer"`, or a `queued_command`
  attachment when the receiver was busy), and all 39 `SendMessage` sends pair
  with their receipt. Replies look one-way only for raw socket writes, which
  carry no return address. Recommends no send log, and names one defect: the
  unblock detector stores Claude Code's peer framing as the answer for 8 of
  27 markers. (channels)

- **`mnemo land --merge --admin`** passes `--admin` through to `gh pr merge`,
  for a repo whose branch protection the maintainer owns and is choosing to
  bypass — on this one, master requires a code-owner approval nobody else
  can give, so every landing is one. Never implied: without the flag a
  protection that refuses the merge still stops the landing with `gh`'s
  reason, exactly as before. Found on the first real landing (#245's three
  pieces), where the rehearsal passed and the merge step could not.

- **`mnemo recall` now says whether a missed rule was buried or absent.**
  Every "miss" the harness has ever reported was a rule that *was* returned,
  at rank 6–65, never one missing from the list — but the report only
  printed `misses (45):` and a bare id, which reads as "not found" and led
  one design (#154) to build reach the vault did not need. The report now
  carries `buried` (returned, rank > 5) and `absent` (rank None) as separate
  lists, prints `outside top-5 : N = buried B (rank 6–max) + absent A` under
  the headline, and shows `rank 34/41` next to each listed miss. `misses`
  keeps its rank-over-ten meaning, since the autopilot digest and miss
  collector read it by name. (#158)

- **The `claude` CLI behaviours dispatch depends on are now stated in one
  place, tested against the installed binary, and loud when they break.**
  `mnemo dispatch`, `mnemo sessions` and `mnemo deliver` rely on things Claude
  Code never documented as a contract — the shape `claude --bg` prints, the
  files under `~/.claude/jobs/`, the daemon roster, what `--resume` does to a
  live session — and every one of them had already changed underneath us
  once (#211) while the suite stayed green on fixtures mirroring the last
  observed output. `mnemo.core.claude_cli` now lists each assumption with the
  `claude --version` it was last verified against; `pytest -m live_claude`
  (opt-in, deselected by default) spawns one real child and checks every
  claim against the installed binary, then stops and removes it. A spawn
  whose output has no id, or whose jobs entry is missing or names another
  cwd, now reports *which* assumption broke and against which version,
  instead of a blank column. The tree is kept in that case — `claude` exited
  0, so the child is running in it — and the report prints the warning under
  the row. (#235)

- **`mnemo land <contract.md>` — the last metre of a contract.** `deliver`
  ends with each piece pushed and its PR open, and the part a contract exists
  for was still done by hand: piece A `consumes` a signature piece B `exposes`,
  A was written against a signature that did not exist yet, and the merge is
  where it becomes real. Until now that meant merging the PRs in dependency
  order, running the suite after each, and finding out at the end whether A's
  assumption about B held. (#236)

  `mnemo land <contract.md>` is read-only: every piece in **landing order**
  (a stable topological sort by `consumes`, owners before consumers, ties in
  contract order), its PR and state, the ref that carries it, and whether each
  `exposes` is actually defined in the piece's `files` on that ref — `✓`
  present, `✗` missing, `?` for a signature that names no identifier (a CLI
  shape). Presence is a **name** check, not a string check: the contract
  parser refuses to compare a consumed signature with an exposed one by
  literal equality because the two are hand-written and differ cosmetically,
  and a name is the granularity that survives a renamed argument and still
  catches a function that was never written. A merged piece whose branch is
  gone is checked on `master`, where its work is. A cycle — which the parser
  admits — is refused by name, since it has no landing order.

  `--merge` finishes it, in two phases, and the irreversible one runs only
  after the reversible one passed in full. First a **rehearsal** in a
  throwaway worktree under the system temp dir: each open piece is merged in
  order, its `exposes` are checked in the merged tree, every `consumes` is
  checked against the *owner's* files in the merged tree (the owner landed
  earlier, so this is the first moment the consumed signature either exists
  or does not), and the suite runs. The first conflict, missing name or red
  suite stops it with the piece and the step named, and nothing anywhere has
  changed. Only then are the PRs merged with `gh pr merge`, in the same
  order, stopping at the first refusal; a rerun skips whatever `gh` reports
  as merged. `--suite CMD` overrides the suite (default `python -m pytest -q`,
  with the rehearsal tree's `src/` prepended to `PYTHONPATH` when it has one,
  because an editable install would otherwise test the checkout it came from).
  `--method` selects squash, merge or rebase.

  A new verb rather than a mode of `deliver`, because `deliver`'s invariant is
  that naming an id is the approval and there is no flag that approves N
  children; a landing is inherently every piece of the contract, and the
  per-piece approval already happened when each was delivered. Not a
  scheduler: dispatch stays a flat fan-out, and this runs after it landed.
  Verified against the two contracts this repo has actually dispatched.

### Fixed

- **The unblock detector reads the answer out of the transcript instead of
  trying to catch `tempo` mid-flip.** A background session that is answered
  is back at `blocked` within about ten seconds, and every trigger the sweep
  rides — `mnemo sessions` typed by hand, `SessionEnd` of some *other*
  session — fires on an event unrelated to that window; measured on the real
  transcripts, 107 of 126 answers were out of reach of their own session's
  end by arithmetic, and a 3s poll loop caught 3 of 3 while `SessionEnd`
  caught none (#203). No cadence fixes that, and a poll loop is a daemon. So
  `detector.sweep` now keeps a byte bookmark per session in
  `session-queue.json`, reads the session's transcript forward from it, and
  records every human turn it finds — a `SendMessage` from your own session
  included — as one unblock with `answered_at`, an `answer` excerpt and the
  question it answered. A first sighting baselines at the end of the file, so
  upgrading does not turn old history into markers; the opening prompt is
  never an edge; and the `tempo` comparison remains only for a transcript
  that cannot be read. Late is now just late: the consumer re-reads the
  transcript anyway. (#176)

- **A correction is a reaction, and the evidence gate now says so in both
  directions.** Measured on the maintainer's vault before touching anything
  (`docs/specs/2026-09-13-extraction-corrections-measurement.md`): 100 of
  the 100 feedback pages demoted since the `## Corrections` section existed
  had no supporting correction in any source briefing — the gate was right
  every time, the extractor types *Decisions made* as feedback. Of the 64
  Corrections items the briefings hold, 23 quote only the session's first
  user turn, 21 of them dispatched children whose sole user turn is mnemo's
  own dispatch template; three of the vault's nineteen gate-verified rules
  cite "Do NOT merge or push without asking." or "Choosing is part of your
  job…" as the user's words. And the four truest corrections of September
  were emitted as `type: reference` with a quote that verifies at the
  feedback bar, invisible to a gate that only demoted. Three changes, none
  a threshold: `corrections.verify` ignores the opening turn when it is a
  dispatch brief mnemo itself wrote (a human's first message still counts —
  the README's one-line loop is a first-turn correction — and a quote the
  user repeats later is kept either way); `evidence.verify_page` retypes a `reference`
  page whose quote verifies to verified `feedback`, the mirror of the
  demotion it already did; the briefing prompt states what a correction is
  not (the opening message, approvals, questions, feature requests, bug
  reports) and that the `→` half is an imperative rule, not what was done.
  `mnemo replay` on the same 2100 prompts, both gates applied to today's
  pages: carried 117 → 117, hindsight 24 → 23, not-yet-learned 85 → 85,
  correction-backed rules 78 → 75 and carried-correction-backed 3 → 2 — the
  one lost was an approval prompt matching an approval quote, which fell
  under the relative gap when nine evidence-bearing pages moved. The
  measurement also grades `mnemo reclassify`'s 61 keep verdicts by hand:
  3–4 are corrections, the rest feature requests, bug reports and approvals,
  so 53 of the 78 "correction-backed" rules carry a label no current gate
  would issue. Not changed: thresholds, the 1320 demoted pages, the 60
  reclassify-era labels, the replay's definition. (#244)

- **Dispatched PRs no longer collide in `CHANGELOG.md`.** Five children
  dispatched in parallel (#233–#237) touched disjoint code and still
  conflicted with each other in one file, because each added its entry at
  the top of `## [Unreleased]` — the first merge invalidated the other four,
  and the rest were rebased, re-tested and re-run through CI one at a time.
  A change now documents itself in `changelog.d/<id>.<section>.md`, one file
  per change that nothing else touches; `python3 tools/assemble_changelog.py`
  folds the fragments into `## [Unreleased]` under their headings at release
  and removes them, a release with no fragments leaves the file byte for
  byte, and the release workflow refuses to publish while any fragment is
  still pending. The dispatch prompt tells a child where to write when the
  repo keeps a `changelog.d/`, and says nothing where it does not. (#246)

- **A live `shared/<type>/` page with no state entry is now user-owned, never
  overwritten.** Pages the extraction ledger has never written are exactly
  the ones a person put there — written by hand, `mv`-promoted out of
  `_inbox/` (the documented review path), or imported by `mnemo import`. The
  single-source auto-promote branch wrote over them unconditionally, so a
  `verified` page someone reviewed was replaced by an `inferred` re-emission
  with nothing but an `auto_promoted` line in the extract summary to show for
  it; the prompt makes the collision routine, since it advertises every slug
  on disk as one to reuse. Such a page now takes the door an edited sacred
  page already takes: the re-emission lands as
  `shared/_inbox/<type>/<slug>.proposed.md`, counted under `conflicts:`, and
  the live page is left byte for byte, with no entry adopted into the ledger.
  A proposal that differs from the staged one only in its run stamps is not
  rewritten, so an unreviewed proposal does not churn on every run. Not
  changed: whether a same-type `inferred` re-emission may replace a
  `verified` page whose entry it matches — the evidence gate retypes every
  feedback page that fails to verify, so that overwrite has no path through
  the pipeline. (#248)

- **Every text file mnemo reads or writes is UTF-8 on every platform.** 44 call
  sites in the package used the platform default encoding, which is cp1252 on
  Windows: a `settings.json` holding a non-ASCII path, a session cache entry
  with an accented directory name, or a vault page in Portuguese decoded as
  mojibake or failed with `'charmap' codec can't decode`, silently inside a
  hook. Three Windows-only CI failures in one day (#233, #236, #243) were
  this class, each fixed at the one site CI hit; now every `read_text`,
  `write_text`, `open` and `fdopen` in `src/`, `tools/` and `tests/` names
  its encoding, the settings backup is a byte copy, the slash-command and
  `.gitignore` probes tolerate files saved in another encoding, and a
  repo-wide test fails on any new bare call. (#255)

- **`mnemo replay` splits "cite a correction you typed" by provenance, and
  quotes only the half the evidence gate can stand behind.** The
  `confidence: verified` label has been written by three different bars
  (#244), and every surface that counts it counted them alike. The replay
  now re-asks today's gate of each verified page — the quote must sit in
  the `## Corrections` of a briefing the rule was built from, the same
  predicate `verify_page` uses — and prints *gate-verified* and *label only*
  side by side, in the text and in every `replay-report.json` figure
  (`vault`, `prompts`, `injections`, `rules`, `rate`, `top_carried`).
  Measured on the maintainer's vault, 2107 prompts: 78 correction-backed
  rules are 18 gate-verified and 60 label only; the 3 carried prompts
  "citing your own words" are all label only, so the honest number is **0**.
  The 60 are not demoted: every one cites a briefing that has no
  `## Corrections` section at all (written before 2026-09), so today's gate
  fails them mechanically, the 3–4 real corrections among them included —
  re-verifying would drop exactly the rules a human would keep. Deciding
  them needs a human grade or a re-briefing of those sessions, not the gate.
  (#257)

- **A rule that names a file is now shown when a session opens or edits that
  file — the path-scoped enrichment had never fired once.** Claude Code hands
  `PreToolUse` an absolute `file_path` and `activates_on.path_globs` are
  written relative to the repo, so even `**/` globs could not match: across
  3,660 real `Edit`/`Write` calls on disk, 0 did, while 655 live rules carried
  globs. The path is now taken relative to its own git root (a worktree's, in
  a worktree), `Read` triggers it as well as the writing tools, and each rule
  is shown once per session rather than once per day for the whole vault, so
  parallel dispatched children each get the note. Only globs that name a file
  fire (`prisma/schema.prisma`, `**/screens/HomeScreen.tsx`); area globs
  (`src/app/**`, `**/*.ts`) are ignored, because replayed over the real
  transcripts they would have averaged 6.2 notes a session against 1.6. The
  extraction prompt now asks for file paths only. Standalone installs: run
  `mnemo init --hooks-only` to add `Read` to the hook matcher (`mnemo doctor`
  says when it is missing); the plugin picks it up on update. (#271)

- **`mnemo sessions` now shows the dispatch children it was hiding.** The
  queue scoped by exact directory, so a maintainer running it in their repo
  never saw the sessions dispatched from it — every child lives in a
  `<repo>-wt-<issue>` worktree beside the repo, which never matched. On a real
  machine that printed an empty queue while four sessions waited, one blocked
  for five hours. A repo and its dispatch worktrees are now one queue, and an
  empty scoped listing names how many sessions are elsewhere instead of
  reading as "nothing is running". (#281)

- **The live CLI contract no longer claims every child records a model.**
  `bg-model-flag` was measured before the lean child profile existed, and
  stated that `respawnFlags` carries a resolved `--model` even when the
  dispatcher passed none. A lean child (now the default) passes its own
  `--settings`, so Claude Code has no user-level default left to resolve and
  writes no `--model` at all — measured both arms on 2.1.270, one flag apart.
  Two shipped features that each passed alone therefore failed together, in a
  suite CI does not run. The assumption now states both arms, the default-path
  spawn test pins what a lean child must carry, and the resolution half is
  asserted on a full-profile child, the only arm that can still exhibit it.
  `Session.model` being `None` for a lean child is the honest answer, not a
  failed read. (#282)

- **`mnemo deliver` and `mnemo land` measure against the repo's default
  branch, not a hardcoded `master`.** In a repo whose default branch is
  `main`, `deliver --review` reported every committed piece as "no commits
  ahead of master" and `deliver <id>` refused it; `land` rehearsed from a
  `master` that did not exist. The base is now read from
  `refs/remotes/origin/HEAD`, then `gh repo view` when the remote was added by
  hand, and falls back to `master` only when neither answers. A base branch
  that exists nowhere is reported by name instead of reading as nothing to
  deliver. (#287)

- `mnemo briefing` and every other `claude -p` call no longer fail with `Can't access working directory` when the hook runs from a worktree the dispatcher already removed: the subprocess falls back to the home directory.

- **`mnemo sessions --json` now says what a session is doing, not what Claude
  Code guessed it was doing.** Each row gains `activity` (the last tool call
  from the transcript: `tool`, `target`, `since`, `at`, `repeated`) and
  `status_line` — the same line the table prints: `needs` for a waiting
  session, the tool call for a working one, the result for a finished one.
  `detail` is Claude Code's own summary and is now only the fallback. On
  2026-09-15 it read "awaiting task specification; message truncated" for
  twenty minutes over a child that was editing files and committing, and
  mnemo-desktop, which reads `--json`, showed exactly that. The table already
  preferred the tool call; both surfaces now share one rule. Raw fields are
  unchanged. (#293)

- **Unblock markers from dispatch worktrees are learned from instead of
  failing forever, and a worktree's transcripts count toward its repo.**
  Discovery named each Claude Code project directory by decoding its name,
  which cannot be decoded: `mnemo-wt-200` came back as `mnemo/wt/200`, a path
  that never existed, so every dispatch child's transcripts were filed under
  an agent like `200`. It now reads the `cwd` Claude Code records inside the
  transcript, and a dispatch tree that has since been removed
  (`<repo>-wt-<n>`, `<repo>-wt-c-<slug>`) is filed under its repo rather than
  an orphan `<repo>-wt-<n>` namespace. On the real vault, `learn` resolves 40
  of 40 pending unblock markers (0 before); `mnemo backfill` and `mnemo learn`
  in the `mnemo` repo see 121 transcripts where they saw 49. A hand-made
  worktree whose directory is gone still keeps its own name. (#301)

- **An imported rule, once promoted, now reaches the reflex.** An imported page
  has no `sources:` and names its project only in `projects:`, but the reflex
  index read the project from sources alone, so the rule indexed with no project
  and was never offered for injection — while the MCP tools and the SessionStart
  topic list, which read the page directly, still showed it. The index now uses
  the `projects:` fallback, as `rule_activation` already did. The extractor's
  "existing rules" hint had the same gap in the other direction: it read an
  imported rule as project-less and advertised it to every project's
  extraction; it is now scoped to the importing project. No page in an existing
  vault changes attribution. (#302)

- **`mnemo doctor` now warns when an installed hook is older than the one this
  version ships, and `mnemo init --hooks-only` fixes it without touching
  anything else.** `mnemo init` writes each hook's matcher once. When #271
  added `Read` to the `PreToolUse` matcher, existing installs kept the old one,
  and `mnemo status` still counted the hook healthy. Over a week on a real
  machine that halved what path enrichment delivered (23 notes against 40).
  The new `hook_matcher` row compares the global and project `settings.json`
  with the running version. `--hooks-only` rewrites mnemo's hook entries after
  a backup and leaves the statusLine, MCP server, commands and skills alone. It
  refuses where no mnemo hook is installed, so it cannot start a second install
  next to the plugin. (#303)
- **Re-running `mnemo init` no longer resets `mnemo.config.json`.** It wrote
  `{"vaultRoot": ...}` over the whole file, so a user who followed #271's
  advice to re-run it lost every non-default setting without warning, such as
  `extraction.subprocessTimeout` or `doctor.skipStatuslineDrift`. `init` now
  sets `vaultRoot` and keeps the rest. A file that isn't a JSON object is copied
  to `mnemo.config.json.bak.<timestamp>` before it is replaced. (#303)

- **`mnemo land` finds a dataclass field, a class constant or an enum member
  that a contract exposes.** The check reduced `ConsumeReport.retired` to
  `retired` and then only accepted an assignment at column zero, so an
  attribute, which is always indented, never counted. On 2026-09-15 that
  refused the `channels` contract's `unblocks-retire` piece on a correct tree,
  and it had to be merged around the gate. A dotted name whose owner is a
  class in the piece's files is now looked up among that class's own members:
  fields, constants, methods, nested classes and `self.<name>` set in its
  methods. A local variable of the same name does not count, and neither does
  a method of the same name on a different class, which used to pass. A
  qualifier that is not a class there, like `briefing_select.pick`, is looked
  up as before. (#305)

- **`mnemo dispatch` no longer hands a session the queue as its own next
  step.** Run through a session's Bash tool, the `queue:  mnemo sessions`
  footer came back to the maintainer as the model's promise — "Acompanho com
  `mnemo sessions`… te aviso quando terminarem" — with nothing armed that
  would ever wake it. When `CLAUDE_CODE_SESSION_ID` is set, dispatch now ends
  with a note to that session: the children are detached, nothing notifies
  it, report the ids and stop unless the maintainer asked it to watch. A plain
  terminal gets the footer unchanged, and `/mnemo:dispatch`'s description no
  longer says "to watch them". `tools/measure_dispatch_reports.py` counts
  dispatch reports that name the queue with no watcher behind them, split by
  whether the note was shown: 2 of 36 before it, both in sessions outside the
  mnemo repo. (#306)

- **`mnemo sessions` token column now shows how full each session's context is.**
  It used to copy `tokens` from Claude Code's `state.json`, which is not the
  context size: on all 18 jobs measured it read 3-65x low (`fee17ecd`: 14k
  where `/context` said 92.6k). The column is now read from the transcript's
  last real model turn (`input + cache_creation + cache_read`), the number
  `/context` prints, skipping synthetic API-error turns. `sessions --json` adds
  it as `context_tokens`; the raw `tokens` field is still there, unchanged.
  (#307)

- **One noisy command no longer pauses every hook for an hour.** The circuit
  breaker now counts strikes, one per distinct `(where, kind)` per minute,
  instead of raw `.errors.log` rows. On 2026-09-15 a single `mnemo sessions
  --consume-unblocks` pass wrote 27 same-second `unblocks.consume` rows,
  opened the breaker, and the next session ends wrote no briefing. A hook
  that keeps failing still trips it, because every minute it fails is a new
  strike. The unblock consumer also writes one row per pass now, with the
  count and every session id, instead of one row per marker. `mnemo doctor`'s
  closed-breaker row shows what it has counted (`27 errors in the last hour,
  most from unblocks.consume; 1 of 10 strikes`), so you can see the breaker
  filling up before it opens. (#314)

- **mnemo's own `claude` helpers no longer fire mnemo's hooks.** Briefing,
  extraction and `learn` run `claude --print` under your full settings — which
  is how they reach your subscription — and those settings carry mnemo's
  SessionStart and SessionEnd, so every helper was a session that scheduled
  another briefing, extraction and unblock sweep. One machine reached 42
  concurrent sweeps and 34 helpers behind 7 real sessions, at load average 118.
  Helpers now run with `MNEMO_HOOKS_OFF=1` and every hook returns immediately
  when it sees it. (#329)
- **`mnemo sessions --consume-unblocks` takes a lock and stops at five markers
  a pass.** A sweep is spawned by every session end, so passes overlapped and
  each one re-ran the same briefings and extractions over the whole backlog;
  now the second pass exits quietly and says so, and a 40-marker backlog drains
  five at a time instead of holding one process for minutes. (#329)
- **`mnemo doctor` counts the helper processes.** One row for live unblock
  sweeps and `claude --print` helpers, which warns at the shape above — and
  tells you when `MNEMO_HOOKS_OFF` is set in your own shell, where it would
  silence mnemo entirely. (#329)

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

- **A dispatch child stopped after its worktree was removed no longer files
  its briefing under `bots/<repo>-wt-N/`.** (#247) #225 made every naming
  path canonical, and the `session_end` hook's log line did use the name
  session_start had cached. Its briefing and proposer helpers, though, still
  re-resolved the agent from the hook's `cwd` on their own — a decision from
  when the cache held the naive name. The dispatcher's normal sequence is
  merge the PR, `git worktree remove --force` the tree, then `claude stop`
  the child, so by the time SessionEnd fires the cwd is a path with nothing
  on disk; the resolver finds no `.git` above it and returns the basename.
  On 2026-09-13 that wrote `bots/mnemo-wt-236/briefings/` 55 seconds after
  the tree was gone, while the "session ended" line beside it went under
  `bots/mnemo/`. Both helpers now take the name `main()` resolved (session
  cache first, canonical resolution only on a miss), and `cwd` is used
  solely to find the transcript, which outlives the tree. Regression tests
  drive the hook from a removed `_make_worktree` and assert the spawned
  briefing names the main repo.

- **The first-run backfill's default is one value everywhere, and a finished
  sweep now says so.** `backfill.autoOnFirstSession` has been `false` in
  `config.DEFAULTS` since 1.1.0, and the README and `docs/configuration.md`
  said so — but `docs/getting-started.md` still described the sweep as
  automatic, and the `session_start` hook's own fallback for a missing key was
  `True`, the opposite of the default it was meant to stand in for. The
  fallback now reads `DEFAULTS` (a test pins the two together) and the
  getting-started page describes the opt-in. Separately, a sweep that ran
  staged its whole output in `shared/_inbox/`, from which nothing injects, so
  on a fresh vault it was indistinguishable from a sweep that did nothing;
  only `mnemo doctor` listed it. Session start now carries one line while
  backfilled rules are staged **and the vault has no live rule at all** —
  stateless (no once-shown marker, after #229), repeating while that holds
  and clearing itself once the user moves or deletes the pages or anything
  goes live. It counts; it never promotes. (#234)

### Added

- **`mnemo replay` — the first number about *your* vault that has a
  baseline.** `mnemo status` reports how often reflex fired; nothing said
  whether what it fired was worth anything. `replay` reads every Claude Code
  transcript on disk, replays each prompt you typed through the hook's own
  decision (`core.reflex.decide`, now shared with the hook rather than copied),
  and sorts every rule that would have fired by *when the vault learned it*:
  from an earlier session (the only bucket the vault can take credit for),
  from the same session (hindsight — Claude had it anyway), or not yet learned
  at the time (a naive replay's over-count, shown and excluded). Inside the
  first bucket it counts the rules that cite a correction you typed. Counts
  always; a rate with a 95% interval only past 30 prompts. Session cap and the
  day-level injected cache are simulated from the transcript clock; export
  suppression is not, and the output says so. No claim about tokens, time or
  productivity — the issue ruled those out and so does the report. Composed
  from `recall-sessions`' transcript discovery and the hook's decision; why
  `mnemo recall` could not be folded in is in the module docstring. (#237)

- **The dispatch loop is reachable from inside a session.** The
  `decomposing-for-dispatch` skill existed in the repo since #216 and reached
  no install: the plugin loads `skills/` by convention, but `mnemo init` wrote
  commands and never skills, so a direct install could not load it at all. The
  skill is now package data (`mnemo/skills/`), `mnemo init` writes it to
  `~/.claude/skills/` (or `<cwd>/.claude/skills/` with `--project`) and
  `mnemo uninstall` removes it, `tools/sync_plugin_manifest.py` mirrors it to
  the plugin's `skills/` the way it already did `commands/`, and CI fails when
  the two copies drift. `/mnemo:dispatch` joins the slash menu on both install
  paths, passing its arguments through (`/mnemo:dispatch 197 198`,
  `/mnemo:dispatch --contract plan.md --dry-run`); `sessions` and `deliver`
  stay CLI-only, because the queue is designed never to land in a session's
  context. The README and getting-started now name the loop once, end to end:
  `dispatch` → `sessions` → `deliver`. (#233)

- **`mnemo init`'s slash commands showed up with no description and were
  visible to the model.** The ownership tag was written *above* the YAML
  frontmatter, and Claude Code only reads frontmatter that starts on line 1 —
  so every direct-install command lost its `description`, `allowed-tools` and
  `disable-model-invocation`, and nine entries the plugin hides from the model
  were listed to it on every session. The tag now sits right under the
  closing `---`, and both descriptions and argument hints are quoted, since
  `learn`'s "now: briefing" was a nested mapping to a strict YAML parser
  rather than text. Re-running `mnemo init` rewrites the files. (#233)

### Changed

- **A dispatched child no longer inherits your whole Claude Code profile.**
  `mnemo dispatch` used to start every child on the same configuration you use
  interactively — every plugin, every MCP server, every unrelated skill and
  hook — none of which is what the child was dispatched for, and all of which
  it pays for on its first turn and carries to its last. Children now start
  with the repo's own `.claude/` settings plus mnemo's hooks and MCP server,
  and nothing else: measured over three background children per arm, ~58,000
  first-turn input tokens becomes ~42,000, a saving of ~16,000 tokens (~27%)
  per child. The lean arm also varies by three tokens between runs where the
  full profile moves with whatever you installed that day, so children are
  reproducible as well as cheaper. Pass `--full-profile` (or set
  `MNEMO_DISPATCH_FULL_PROFILE=1`) for a child that genuinely needs one of
  your plugins. (#270)

- **`mnemo sessions` hides finished children whose worktree is gone.** Claude
  Code keeps a background job's record until `claude rm`, long after the
  dispatcher removed its tree on merge: on 2026-09-15 that was 48 records and
  14 PRONTAS rows for work merged days earlier. A `done`/`stopped` session
  whose `cwd` no longer exists now leaves the text queue and `--json` (so the
  desktop cockpit too), with a footer counting what was hidden; `--stale`
  lists them again and `--json` rows carry `is_stale`. A blocked session is
  never hidden. `mnemo doctor` names every such job with a pasteable
  `claude rm` line; transcripts survive `claude rm`, only the job record goes.
  (#292)

- **`mnemo deliver <id>` stops the child once its PR is open.** Nothing in
  mnemo ended a finished child: the daemon retires one only after 8 h idle,
  each held 300–400 MB until then, and — because only a *stopped* child fires
  `SessionEnd` (#247) — the children that finished cleanly were exactly the
  ones that never wrote a briefing (0 of 12 `done` jobs on disk had one;
  `claude stop` on a `done` child was measured to run its SessionEnd hook).
  After printing the PR URL, `deliver` runs `claude stop` on every session in
  that worktree whose state is `done`; the row still reads `done` and the
  worktree is left in place for `SessionEnd` to resolve. A child still working
  or blocked is left running, with one line saying so. (#311)

- **The SessionStart briefing now goes through a selection step, and it still
  picks the newest on purpose.** `briefing_select.pick(vault, project, query=)`
  ranks the last 10 briefings with reflex's BM25 scorer and gates, and keeps
  the newest unless a query clearly names an older one. The hook has no query
  to give it: it runs before the first prompt is written, and the one task
  signal it does have, the checked-out branch, picked the best briefing 5
  times in 20 on mnemo's real briefings, the same as newest-wins
  (`tools/measure_briefing_query.py`). The hook also records which briefing it
  injected, through `record_briefing_read`. (channels/briefing-query)

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

- **The README tells both halves.** It told the memory story only — the
  dispatch loop that shipped in 1.4, 1.5 and this week (`dispatch` →
  `sessions` → `deliver` → `land`, `replay` to measure the vault) was one
  paragraph under "Commands", and a reader learned that mnemo is a memory
  plugin, not that a session fans out into children that inherit the vault,
  that the queue puts the blocked ones first, or that answering a blocked
  child feeds the vault back. The README now says so in one sentence and shows
  the whole loop once, with the queue, `mnemo land` and `mnemo replay` output
  copied from the terminal (the replay figures, with their interval and their
  "not measured" line, are the new number it quotes). "How it compares" gains
  the `claude --bg` row. It is not longer: the autopilot section and the vault
  tree moved to getting-started, which also gains a "The dispatch loop"
  section — issue form and contract form, delivering, landing, and what a
  blocked child's answer becomes — at the depth the queue already had. (#243)

### Internal

- **Declined to add a `check:` rule field; documented why in `docs/issue-273-findings.md`.** The field already exists as `enforce:` — with a matcher, a PreToolUse block, `mnemo list-enforced`, two `mnemo doctor` checks, and extraction-prompt guidance that defaults to omitting it. The measured gap is adoption, not schema: 1 of 1842 live rules carries a declarative check. Two findings that would have bitten an implementation are recorded there — a grep-shaped check for the rule #273 cites as its prototype reports 22 false positives on a clean tree where the AST scanner reports 0, and `mnemo publish` drops `enforce:` on purpose, because a rule that can block a tool call is not something another vault gets to install. (#273)

- **An unblock marker's `answer` no longer starts with Claude Code's peer
  framing.** A raw write to a session's inbox socket arrives without the
  `<cross-session-message>` wrapper, so the header line ("Another Claude
  session sent a message:") and the permission paragraph after it were stored
  as the answer: 14 of 47 markers on a real vault. Both are now stripped by
  their text, where Claude Code places them. Nothing reads the field today —
  `unblocks.consume` re-briefs from the whole transcript — so no rule was ever
  learned from it; this keeps the recorded data honest. (#304)

- **Declined to tell dispatch children that a marked inbox reply carries the
  maintainer's authority; documented why in
  `docs/superpowers/specs/2026-09-15-inbox-reply-authority.md`.** Any process
  that can write to a child's inbox socket could send the marker. On this
  machine, 6 of the 8 unwrapped socket writes came from a Claude session's Bash
  script, and exporting the marker in `sessions --json` would give it to every
  session. On the child that prompted the issue, 3 of the 6 "maintainer
  replies" were haiku's output from the desktop's English rewrite. Measured
  instead: a reply typed through `claude attach` lands as
  `origin.kind == "human"`, the same as the opening prompt. That is the path
  mnemo-desktop should use for approvals. The dispatch prompt is unchanged. (#309)

- **Measured why `.mnemo-shared` never ran; recorded in `docs/superpowers/specs/2026-09-15-shared-layer-status.md`.** `publish` → commit → clone → `import` → promote → re-publish → `rewrites` all work on first contact against a copy of the real vault. It never ran because it has existed for ~39 hours in no released version (the share pieces merged after the `v1.5.0` tag, PyPI's latest), with zero non-dry-run invocations in any transcript and nothing that invites a publisher before the first publish. One real defect surfaced: a promoted imported rule is invisible to the reflex — `core/reflex/index.py` calls `projects_for_rule` without `frontmatter=`, so the `projects:` fallback imported pages rely on never fires, while the MCP tools and topic list see the rule. The spec recommends fixing that line before the release that first ships `publish`/`import`, then keeping the layer and documenting the flow in `docs/getting-started.md`; removal is rejected because mnemo-desktop's marketplace pane runs both commands. (channels)

## [1.5.0] — 2026-09-13

### Added

- **`mnemo sessions --json` now answers the question instead of handing you the
  raw fields to re-derive it from.** Each row carries `is_waiting`,
  `is_blocked`, `is_done` and `is_abandoned` alongside `state`/`tempo`/`live`.
  `Session` already computed all four correctly, but as `@property`, and
  `asdict()` cannot see a property — so every script re-implemented the rule
  and a watcher polling the queue during the #215–#218 dispatch stayed silent
  through three children going from working to blocked to done. `is_waiting` is
  the one that cannot be rebuilt by eye: it is an interaction between `tempo`
  and liveness that exists because of #196. Additive — existing consumers of
  the raw fields are unaffected. (#222)

- **The queue now says what each session is *doing*, not just whether it is
  alive.** `mnemo sessions` answered two questions — is the process alive
  (`live`), does a human need to answer (`tempo`) — and a child sitting at
  `active` for twenty minutes read identically whether it was making progress,
  stuck on one thing, or going in circles. Those three call for three different
  responses, so the working bucket now shows the last tool, its target, a
  `(+N)` count of tool uses since, and `↻` when the same tool and target come
  back:

  ```
  TRABALHANDO (3)
    s00  #158 recall harness      Bash Commit measurement… (+26)        2k
    s07  #197 dispatch a piece    Bash Commit (+20)                    18k
    s08  #200 windows ci          Grep linkScanOffset (+7) ↻            9k
  ```

  A rising count with a changing target is progress; a frozen count is stalled;
  a rising count on an unchanged target is a loop, which reads exactly like
  progress without the mark.

- **`mnemo session <short_id>`** — the same data one level down, for when a
  queue line looks wrong and the question becomes *wrong how*. Lists that
  session's recent actions oldest-first with timestamps (default 15,
  `--limit` to change), marking any tool and target that comes back inside the
  window. A unique prefix of the short id is enough; an ambiguous one refuses
  rather than showing the wrong session. No `--follow`: you look, you decide,
  and `claude attach` is still how you get inside.

  Read from `linkScanPath`, which `state.json` has always provided and which
  mnemo parsed and then never opened — `detector` forwarded it into an unblock
  marker and the consumer ignored it in favour of re-resolving. The pointer
  into a live session was write-only; this reads it.

  Transcripts are append-only, so a byte offset is a complete bookmark and each
  `--watch` tick reads only new bytes. Measured on nine real dispatch children
  (9.3 MB): a cold tick costs 7.8 ms and a warm one 0.011 ms. Offsets live in
  memory and die with the process — an offset only has value inside a live
  watch, and between invocations what you want is current state, not yesterday's
  delta. `--json` is unchanged; activity is a render concern, not a `Session`
  field.

- **`mnemo dispatch --contract <path>` — one background child per *piece of a
  feature*, instead of one per GitHub issue.** Nobody files four issues to
  build one feature, so the unit of parallel work is now a **contract**: a
  reviewed markdown file naming each piece, the files it may change, the
  signatures it `exposes`, and the signatures it `consumes` from other pieces.
  Pieces are addressed as `<repo>-wt-c-<slug>` on `feat/<feature>/<slug>`; the
  issue form is unchanged.

  A piece's `consumes` names something that does not exist yet — the piece
  delivering it is being written at the same moment. The child writes against
  the signature and the merge resolves it, which is why `exposes` must be a
  literal signature rather than a description, and why dispatch is a flat
  fan-out rather than a scheduler.

  `verdict: sequential` is a first-class outcome: it is the decomposition
  reporting that the work does not divide, and it is refused rather than
  dispatched. A contract naming an unknown piece, a duplicate slug, an
  unaddressable slug, a piece with no file boundary, or a piece consuming from
  itself is refused **before any worktree is created**.

- **Skill `decomposing-for-dispatch`** — writes that contract file from the
  current conversation, whatever its origin (built-in plan mode persists
  nothing, so the skill reads the session rather than a plan file). It applies
  one test per pair of pieces — *can A be written and tested without reading
  the interior of B?* — and records boundaries, never approaches. The
  maintainer reviews the file and runs the dispatch; no model spawns work.

### Fixed

- **Two mnemo installs at two versions ran every hook twice, and nothing on the
  machine said so.** A plugin install at 1.3.3 sat alongside the direct install
  at 1.4.1; Claude Code fired both sets of hooks, so the stale copy kept writing
  the orphan `bots/<repo>-wt-*/` namespaces that #225 had just fixed — three of
  them created in the ten minutes after the fix merged. The duplication was
  already detectable (`install.migration`), but only from inside the *plugin's*
  own SessionStart, gated on `CLAUDE_PLUGIN_ROOT`, and fired at most once ever:
  on this machine that single notice was spent on 2026-09-10, three days before
  the damage. Nothing compared the two versions.

  `mnemo doctor` now reports a second install on every run, naming both
  versions, the hooks the other copy registers, and how to remove it. Detection
  keys on the plugin *registry* (`installed_plugins.json` + `enabledPlugins`),
  not on the cache directory: `claude plugin uninstall` leaves the whole
  versioned tree — `hooks/hooks.json` included — on disk, so a cache-dir scan
  reports plugins that no longer run. Enablement is read from the project's
  `.claude/settings.json` and `settings.local.json` as well as the global file,
  since the registry's `project` and `local` scopes are switched on there.
  (#229)

- **`mnemo status` reported a phantom plugin and hid the install actually doing
  the work.** Same root cause, found while fixing the above: `_installed_plugin_root`
  globbed the cache, so a leftover 1.3.3 tree made status print
  `Hooks (plugin): 4/4` on a machine with no plugin installed — and because that
  branch *replaces* the settings.json scope lines, the four real hooks went
  unreported. It now intersects the glob with the registry. (#229)

- **`mnemo migrate-worktree-briefings` repaired one kind of vault breakage by
  creating another.** Moving a briefing out of `bots/<proj>-wt-*/` left every
  reference to its old path behind, and the command knew only about `bots/` —
  it moved files and updated nothing that pointed at them. Running it on the
  real vault turned six `doctor` warnings on, silently, and the repair was left
  to whoever thought to run `doctor` afterwards.

  The move is the easy half; the citations are the half that makes the move
  safe. Four populations cite a briefing path, and the issue found three of
  them. Measured on the live vault, one migrated briefing was cited **28 times
  across 14 rule pages**:

  - **Rule markdown, in three spellings** — the frontmatter `sources:` list,
    the `evidence.source` quote (`source: 'briefing: bots/...'`, which the
    issue did not name), and `[[wikilink]]` bodies, whose form omits the `.md`.
  - **`shared/_inbox/` drafts.** `doctor` cannot see these, so nothing would
    ever have reported them — **8 of the 14 affected pages**, which is why the
    warning count (6) understates the damage.
  - **`.mnemo/extraction-state.json`.** Not the inert historical record it
    looks like: `inbox/dedup.py` compares `source_files` to a new page's
    sources for *exact set equality* to catch slug drift, and
    `_union_with_prior_sources` merges them by string identity. Repointing the
    frontmatter while leaving the state stale makes the two disagree and
    defeats that guard, so both move together — `source_hash` with them.
  - **Both derived indexes.** `rule_activation.projects_for_rule` reads a
    rule's *project* out of the `bots/<name>/` segment of its sources. A stale
    `-wt-` source therefore does not merely dangle: it files the rule under
    project `mnemo-wt-187`, where no lookup for `mnemo` will ever find it —
    the same class of invisibility as #225. Rebuilding is part of the repair,
    not a tidy-up.

  `written_hash` is advanced for the pages rewritten, because a bulk rewriter
  owns the bytes it produces — leaving it stale makes a page read as
  hand-edited and stages a `.proposed.md` instead of updating in place, the
  mistake `migrations/slugs.py` already records having made once.

  `--dry-run` now reports the blast radius before anything moves — which pages
  will be rewritten, how many state entries repointed, and that the indexes
  will be rebuilt — so the decision is informed rather than discovered by
  `doctor` afterwards. Verified against a real vault slice: the pre-fix
  behaviour reproduces exactly the six warnings the issue reported, and the
  fixed path leaves zero. (#228)

- **A PR opened by `mnemo deliver` did not close its issue.** `open_pr` builds
  the body with `gh pr create --fill`, so the description is the child's commit
  message — which is right, and unchanged: the child wrote the work and this
  process knows strictly less about the diff than it does. But a commit subject
  in the conventional style, `fix(sessions): ... (#222)`, names the issue as a
  *reference*, and GitHub acts only on a closing keyword. Merging PR #223 left
  #222 open, and it was closed by hand — the manual step `deliver` exists to
  remove. The issue number is the one fact this process owns and the body does
  not carry, recovered from the worktree path by `issue_for_cwd`, so it is now
  appended to the filled body as a `Closes #<n>` trailer. Conditional on the
  target being an issue: a `c-<slug>` contract piece has no issue to close. A
  body that already closes the issue is left alone, and a failed append leaves
  the PR exactly as `--fill` made it rather than failing a delivery that
  already landed. Asking the child to write the trailer was rejected — it makes
  the dispatcher trust a child to report what the dispatcher already knows,
  the fragility #217 named, and #223 is the demonstration. (#224)
- **Dispatched children read rules from an empty vault.** `mnemo.core.agent`
  has two resolvers — `resolve_agent` returns the directory's own basename,
  `resolve_canonical_agent` follows a worktree's `.git` file back to the main
  repo — and the MCP server used the naive one. It backs `list_rules_by_topic`
  and `read_mnemo_rule`, the two tools the injected prompt tells every session
  to call *before writing code*, so inside a worktree they scoped to a project
  that has never had a rule written to it: measured from `mnemo-wt-225`,
  **21 topics visible instead of 78**. Injection was already canonical, which
  is why this stayed invisible — children *did* get a briefing, and only the
  rule lookup came back thin.

  The same naive resolution ran on four more paths that write or report under
  a project name, and all four are now canonical: `session_start` cached the
  naive name and `session_end` wrote the day's log under it (this is what
  created one orphan `bots/<repo>-wt-*/` namespace per dispatched worktree —
  15 of them, holding logs and five real briefings, no rules); `mirror` chose
  the vault memory dir to sync into; and `statusline` / `mnemo status`
  reported per-project rule counts that contradicted what `pre_tool_use`
  actually enforces. The cached `repo_root` still points at the tree being
  worked in — only the *name* was ever wrong.

  Existing orphan briefings are not moved by this change:
  `mnemo migrate-worktree-briefings --repos <path> --dry-run` already exists
  for that and is unaffected. (#225)

- **A `stopped` session was filed under "working" forever.** `is_done` tested
  `state == "done"` alone, but Claude Code also writes `stopped` for a process
  that ended without finishing its turn — two of nine real sessions on
  2026-09-13. They rendered under TRABALHANDO with the literal word `stopped`
  as their activity, and a consumer polling for completion never saw them end.
  `is_done` now covers both terminal phases. Surfaced while documenting the
  `state` enumeration for #222: the docstring claimed `state` carried only
  `working, done`, which is what made the omission invisible on review. (#222)

- **Dispatch rollback left the branch behind.** Removing a failed child's
  worktree deleted its directory but not its branch, so retrying the same
  target died on `fatal: a branch named '...' already exists` — a *different*
  failure from the one rolled back, and one no amount of retrying clears. This
  affected the issue path as much as the new contract one. The existing test
  asserted only that the directory was gone, which is why it survived; the
  assertion with teeth is that the retry succeeds.

- **`mnemo sessions` labelled a contract piece as a nonexistent issue.** A
  slug-named child rendered as `#c-parser`, where `#` is the GitHub-issue
  sigil. Pieces now show bare.

## [1.4.1] — 2026-09-12

### Added

- **`mnemo dispatch <issue>...` — one background child per issue, one git
  worktree each.** A real three-issue parallel dispatch produced three merged
  PRs and a set of constraints that each cost a failed attempt; they are now
  encoded rather than rediscovered. `--bg` takes the prompt **positionally**
  and never `-p/--print` (the CLI rejects the pair, and `--print` would never
  start the interactive session `claude attach` needs, leaving the job
  unattachable); nothing is wrapped in `timeout`, which is not on the macOS
  PATH; the pipe-safe reader is `mnemo sessions --json`, not `claude agents`,
  which requires a TTY.

  One worktree per child is mandatory, not advisory — sharing a tree between
  parallel sessions has already cost three git accidents in one turn. So a
  dispatch **refuses rather than reuses** an existing tree, which may hold
  another session's uncommitted work, and removes anything it created if a
  later step fails: a stray worktree blocks every later attempt at the same
  issue, which is worse than never having started. A bad issue number is
  refused before any git state exists, and one failing issue never strands
  the others.

  The child is handed **the issue body, a worktree and scope limits — never a
  preferred solution**. `build_prompt` takes no "approach" parameter by
  design: the #187 child was given one, refused it, and was right (the
  instructed change would have reopened #177 at vault scale), so a prompt that
  prescribes can override a correct refusal. A blocked child is likewise left
  for a human; the dispatcher can reach one but deliberately does not answer.

### Changed

- **The queue labels a dispatched child by its issue.** Claude Code names a
  session by inferring a title from the transcript (`nameSource="auto"`),
  which reads well and drops the identifier being tracked — #193's child came
  back as "recall harness hit_slugs migration". The number is now recovered
  from `cwd`, which the dispatcher itself chose (`<repo>-wt-<issue>`) and
  Claude Code already records, so **no new state file is written or
  reconciled**. A path the dispatcher did not name is left labelled as before.

### Fixed

- **The session queue can now tell a blocked session from a dead one.** `tempo`
  is not a fact about the present — it records the last thing a process wrote
  before it stopped writing. A session that blocked and then died left
  `tempo=blocked` frozen on disk, and the queue replayed it forever as a live
  request for attention: one real entry sat in `TE ESPERANDO` for 575h (24
  days), another stated in its own `needs` text that its login had expired.

  `state.json` carries no pid, but `~/.claude/daemon/roster.json` keys its
  `workers` map by the same short id as the job directories and carries a real
  one — so liveness is now probed against that pid rather than inferred from
  age (signal 0 on POSIX; see the Windows note below). There is deliberately
  **no age threshold**: a session waiting on a
  human for 24h is exactly what the queue exists to surface, and only a dead
  *process* re-buckets an entry. Liveness is tri-state, and "unknown" (no
  readable roster) always counts as waiting — a false "dead" would hide a real
  request, which is the worse failure.

  Sessions whose process is gone move to a new `ABANDONADAS` bucket with a
  `claude rm <id>` hint. They are re-bucketed, never hidden: a queue that
  silently drops entries cannot be trusted to be complete. Waiting sessions now
  sort **newest first** (the stalest blocked entry is the likeliest corpse, so
  oldest-first pinned zombies to the top by construction) and the `attach` hint
  follows that order. The statusline badge counts the same `is_waiting` rule the
  list buckets on, so the number can no longer disagree with the screen; its
  global cross-repo scope is deliberate and is unchanged.

- **Answering a blocked session is now learned from.** The detector recorded
  every `blocked → active` edge with `extracted: false`, and `pending_unblocks()`
  existed to serve them — but nothing called it and no code path ever set the
  flag, so the markers accumulated unread. `mnemo sessions --consume-unblocks`
  is the reader, and `session_end` runs it detached (gated on `briefings.enabled`,
  since consuming a marker *is* a briefing plus an extraction).

  What the marker turned out to be worth is **reach, not emphasis**:
  `session_end` briefs at `min_mutations=1`, so a session whose only product was
  the maintainer answering a question mutates no files and is skipped outright.
  Across 206 real transcripts, 76 have zero mutations but only 15 carry
  correction-shaped text — lowering the threshold would buy 61 wasted LLM calls
  to find those 15. The unblock edge selects them for the price of a flag.

- **Windows: the liveness probe was sending a real Ctrl-C to the console
  instead of asking about the pid.** `os.kill(pid, 0)` is not a liveness check
  there: signal 0 is `CTRL_C_EVENT`, so CPython takes the console-control
  branch and calls `GenerateConsoleCtrlEvent`, which per Win32 "cannot be
  limited to a specific process group" — the pid is ignored and **every process
  sharing the console receives a Ctrl-C**. The call then reports success, so the
  probe also always answered "alive".

  The queue probes every rostered session and the statusline runs on every
  render, so on Windows this fired a Ctrl-C at the user's own Claude Code
  session repeatedly — and because the answer was always "alive", the
  abandoned-session handling above could never trigger there at all. Windows
  now uses `OpenProcess` + `GetExitCodeProcess`, with access-denied read as
  alive (the same reading as POSIX `EPERM`); POSIX keeps signal 0. The
  `kernel32` signatures are declared rather than left to ctypes' defaults,
  which would truncate a 64-bit `HANDLE` to `c_int` and leak or mis-close it.

- **The Windows CI job can fail again.** It carried `continue-on-error: true`,
  so it reported `failure` while the run conclusion and the PR status rollup
  both reported `success` — invisible at every place a merge decision is made,
  and how a Windows-breaking change reached `master`. The suppression is gone
  and locked out by a test, the job has a timeout (10 minutes against a 62–108s
  measured runtime) so a hang reads as a fast failure rather than holding a
  runner for hours, and the name no longer says `(experimental)`. Note `master`
  configures no required status checks, so this reports honestly rather than
  blocking a merge.

- **A rule that reappears under a different page type is now recognised as the
  same rule.** `chain-navigation-no-odometry` lived in the vault twice — once as
  `feedback`, once as `reference`, six hours apart from the same project —
  saying the same thing in different words. All three dedupe layers were blind
  to it: each filters candidates by `f"{page.type}/"`, so a slug reappearing
  under another type is invisible to every one of them, and each also gates on
  body similarity first. Lowering that threshold was not available. Measured on
  the real vault, the duplicate pairs score 0.136–0.271 Jaccard while unrelated
  pairs reach p90 = 0.131 and max 0.303: the true pair sits *below* the noise,
  because the two texts use different vocabulary for the same idea and token
  overlap cannot see synonymy.

  The slug is the cheaper and stronger signal — 1852 pages, 1849 distinct
  slugs — so a new first layer keys on slug identity alone and never consults
  the body. It is safe as well as cheap: the 2026-09-02 reclassify moved 1320
  pages to a new type and not one left a live twin behind, so the same slug
  under two types is never a legitimate steady state.

  A collision **stages a `.proposed.md`** for review rather than redirecting.
  Redirecting would reach the auto-promote branch, which overwrites the sacred
  file outright — and in every real pair the existing page is the `verified`
  one and the arrival is `inferred`, so a redirect would let the weaker page
  destroy the stronger. The proposal lands beside the page it would merge into
  and is picked up by the existing `mnemo rewrites` flow.

  The layer deliberately declines two shapes. It never fires on a page carrying
  `demoted_from: feedback`: the evidence gate demotes with
  `replace(page, type="reference")` and keeps the slug, so every demoted page is
  structurally a cross-type collision, and 1386 of the 1852 live pages are in
  that state — acting on them would walk gate-demoted rules back toward the
  tier the reflex injects from (#177). It also leaves the promoted-vs-staged
  copies of one type alone, which are one state key on the normal update path.
  On the real vault it fires twice, on the one true cross-type pair, with zero
  false positives across the other 1849 slugs. (#187)

## [1.4.0] — 2026-09-12

### Added

- **`mnemo sessions` — a blocked-first queue of Claude Code background
  sessions,** so several parallel sessions cost one stream of attention.
  Sessions waiting on a human come first, oldest first, under TE ESPERANDO;
  the rest follow. `--json` for scripts, `--watch` to leave running, `--all`
  for every repo rather than the current one. The statusline gains
  `N esperando`. Read-only over `~/.claude/jobs/`; nothing is exposed to
  model context, and `mnemo doctor` reports the session count and any state
  it could not read.

- **`mnemo rewrites` accepts the `_inbox` backlog, and accepting now sticks.**
  The extractor stages a rewrite of a hand-edited rule as a `.proposed.md`
  sibling, and promotion was a manual `mv` that never advanced
  `written_hash` — so every later run compared the live file against a stale
  hash, concluded "user edited", and re-proposed the same rewrite over the
  unread draft. All 35 staged rewrites on the real vault had drifted this
  way, some since May. Meanwhile `shared/_inbox/` is excluded from every
  consumer surface, so recall served the un-updated rule: one said
  `MARKETPLACE_ENABLED = false` when it had been `true` since 24/08, another
  reported a Stripe webhook gap closed on 2026-08-11 as still open.

  The new command classifies each rewrite by what it does to the live body —
  insert-only (11 of 35), mixed (18), or a full rewrite of a superseded rule
  (6) — merges the insert-only set with `--apply-safe`, and reconciles
  `written_hash` so an accepted rule stops re-proposing. Frontmatter is
  merged per key rather than taken wholesale: `sources[]` is normalized and
  unioned (proposal-wins dropped a source in 3 of 11 cases), `description`
  comes from the proposal (live ones were factually stale), `stability` stays
  with the live rule (a proposal flipping it to `evolving` would silently
  hide the rule from recall), and a staged page's `needs-review` marker is
  never copied onto a reviewed rule. Every apply archives pristine originals
  and the consumed proposal to `shared/_archive/rewrites-<run_id>/` with
  `mnemo rewrites --undo <run_id>`, holds a vault lock so two runs cannot
  clobber each other's ledger writes, and flushes the manifest per rewrite so
  a crash mid-batch leaves the completed work recoverable. The vault is not a
  git repository, so this is the only recovery path there is.

### Fixed

- **A slug migration would have deleted every rollback path `mnemo rewrites`
  creates.** `relocate_proposed` excluded only `shared/_inbox/` from its walk,
  so the proposals archived under `shared/_archive/rewrites-<run>/` — the
  copies `--undo` restores from — read as stray drafts sitting beside the rule
  they shadow. `mnemo doctor` reported 34 of them minutes after the first
  drain. The check is dry-run, but `mnemo extract` runs the real migration:
  one run would have moved all 34 back into `_inbox`, re-staging rewrites that
  had already been accepted and reconciled, and taking the only recovery with
  it — the vault is not a git repository. The walk now skips `_archive`, the
  way every consumer surface already does.

### Changed

- **CI and the release build now run the CLI through a pipe.** Every check
  that existed ran the binary on a terminal, so `mnemo doctor` being dead
  on Windows survived four releases: Claude Code pipes a slash command's
  stdout, and that is what selects cp1252 and crashed on the first `→`.
  The release build pipes `status` and `doctor` on all four platforms
  before anything is published, and the Windows CI job now runs a command
  end to end rather than unit tests alone. Both fail on an encoding crash.

## [1.3.4] — 2026-09-10

### Fixed

- **`mnemo doctor` crashed on Windows before running a single check.**
  Claude Code captures a slash command's stdout through a pipe, so Python
  picks the ANSI codepage (cp1252) rather than the console's, and the
  first `→` in a preflight remediation raised `UnicodeEncodeError`.
  `PYTHONUTF8` and `PYTHONIOENCODING` cannot fix it: the PyInstaller
  bootloader starts CPython with environment config disabled, so the
  shipped binary ignores both. The CLI now reconfigures stdout and stderr
  as UTF-8 at startup, which also clears the mojibake in `status` output.
- **Extraction failed with "claude CLI not found" on Windows.** The CLI
  installs as `claude.cmd` there, and `subprocess.run` without a shell
  does not apply `PATHEXT`, so the bare `"claude"` argv raised
  `FileNotFoundError` on a machine where `claude` ran fine in the
  terminal — auto-brain had been failing on every run. The executable is
  resolved with `shutil.which`, which does apply `PATHEXT`, falling back
  to the bare name so a genuinely missing install still reports the same
  error.
- **`mnemo status` reported 0/4 hooks on a healthy plugin install.**
  `CLAUDE_PLUGIN_ROOT` is only set when Claude Code invokes mnemo, so
  running `mnemo status` in a terminal fell through to the settings.json
  scopes, which a plugin install legitimately leaves empty. Status now
  falls back to the newest mnemo plugin in `~/.claude/plugins/cache`.

### Fixed

- **The plugin's MCP server never connected.** Three spawn constraints
  collided in `.mcp.json`. Claude Code spawns stdio servers without a
  shell, so the shebang-less `bin/mnemo.cmd` is ENOEXEC on POSIX and a
  `.cmd` cannot be spawned on Windows at all. `bash` is not on PATH on a
  default Git-for-Windows install (only `Git\cmd` is), so `command: bash`
  died with `CONNECTION_CLOSED` there. And `${CLAUDE_PLUGIN_ROOT:-.}` is
  not recognised by the plugin loader, which resolved it to `.` — the
  project cwd — so `claude mcp list` showed `bash ./bin/launch mcp-server …
  CONNECTION_CLOSED` on macOS and Windows alike (the #118 fix was only
  ever verified from a source checkout, where the project-scope entry
  masked it). The server is now spawned through `git`, which is on PATH
  wherever a plugin was cloned: a `!` alias runs through git's own `sh`
  (on Windows, the one bundled with Git, which brings `bash` along) and
  reads `$CLAUDE_PLUGIN_ROOT` at runtime, converting it with `cygpath`
  where that exists. Verified from a marketplace install on macOS in both
  scopes. (#121)
- **`bin/launch` and `bin/mnemo.cmd` are pinned to LF.** With
  `core.autocrlf=true`, the Git for Windows default, both checked out with
  CRLF; `bash` then chokes on the stray `\r`, and because `bin/launch`
  fails open the hooks looked fine while doing nothing.
- **`claude plugin install` no longer needs an SSH key.** The marketplace
  entry used the `github` source, which clones over `git@github.com` and
  failed on a fresh Windows machine with no `known_hosts` entry. The repo
  is public, so the source is now an explicit HTTPS URL. (#169)
- **The marketplace manifest passes `claude plugin validate`.** It was
  missing the required `owner` and used the unrecognised
  `github:owner/repo` string source; `author` is the documented object in
  both manifests. (#168)

## [1.3.3] — 2026-09-09

### Added

- **`mnemo doctor` reports the `_inbox` proposal backlog.** The extractor
  stages rewrites of hand-edited rules as `.proposed.md` files for review,
  and promotion is a manual `mv` — but nothing ever said how many were
  waiting. After #156 relocated the strays the pile was 33 files nobody had
  seen. A new `staged_proposals` check counts them and names the oldest
  (#159, step 1). Advisory only; plain staged pages are not counted.

### Fixed

- **LLM-supplied `source_files` are normalized on the way in.** The
  consolidation prompt renders each memory file as `<<<FILE: {path}>>>` with
  the scanner's absolute path and the model echoes it back, so 36 rules
  carried `/Users/.../bots/...` under `sources:` beside the vault-relative
  spelling of the same file. `_parse_pages_from_response` now routes the
  list through `source_paths.normalize_sources` before the hash, the
  backfill-origin match and the page are built (#161). #152's render-time
  fix stops being load-bearing, `source_hash` no longer depends on the
  spelling, and an absolute echo of a reconstructed source is recognised as
  `origin_backfill` instead of being treated as live.
- **The recall harness now replays the `query` real callers pass.** Since
  #105 every `list_rules_by_topic` call from a live session carries a `query`
  that gates the BM25F rerank — the access log shows 0 query-less calls since
  2026-08-31 — but `mnemo recall` bootstrapped cases from `topic` alone and
  `run_case` never sent a query, so `primacy@5` measured the
  `source_count`+popularity path nobody takes any more (#158).
  `bootstrap_cases` records `args.query` when the logged list-call had one
  (the case id gains a `?q` suffix; a queried and an unqueried observation of
  the same triple are distinct cases), `run_case` passes it through, and the
  report splits `primacy@5`/MRR into `queried` and `unqueried` so the legacy
  cases stay visible as a baseline rather than the target. Queries are only
  ever observed, never synthesised.

## [1.3.2] — 2026-09-08

### Fixed

- **Staged `.proposed.md` rewrites no longer shadow the rules they propose to
  replace.** When the extractor re-consolidates a page a human may have edited,
  it stages the new version as a sibling for review rather than overwriting the
  live one. `extract/inbox/paths._sibling_path` routes that sibling into
  `shared/_inbox/<type>/` — "so the sacred dir stays free of plugin artifacts",
  as its docstring puts it — but `extract/promote.py` used `target.with_name`
  and wrote it beside the rule instead. Because a sibling copies the live
  page's frontmatter verbatim (same `slug`, same `name`), every walker keyed on
  slug indexed it under the real rule's identity, and `x.proposed.md` sorting
  after `x.md` meant the draft won: on the vault this was found in, **30
  project rules were served as their unreviewed draft while the approved page
  was unreachable**. Three parts: `promote.py` now calls `_sibling_path`;
  `core.filters` hides `*.proposed.md` / `*.update-proposed.md` from
  `iter_shared_pages` and `is_consumer_visible`, so every walker agrees rather
  than each growing its own check (`reclassify.py` and `existing_rules.py` had
  one already); and `core.migrations.proposed` relocates siblings a pre-fix run
  left behind, run from `mnemo extract` and reported by `mnemo doctor`. Strays
  are moved, never deleted — they are unreviewed proposals — and a stray whose
  destination is already occupied is left in place rather than clobbering a
  proposal nobody has read. Closes #155.

- **`sources:` entries holding an absolute path no longer render dead
  wikilinks.** A wikilink resolves against the vault root, so
  `[[/Users/me/mnemo/bots/proj/briefings/sessions/abc]]` resolved to nothing
  and the rule→briefing edge was silently absent from the graph — 346 dead
  links across 335 rules on a real vault. `_wikilink_target` stripped `.md`
  and copied the rest through verbatim; it now routes through
  `core.extract.source_paths.vault_relative_source`, the helper that already
  documents itself as the write-side chokepoint for exactly this. Paths with
  no anchor inside the vault are still left untouched. Running
  `mnemo regen-graph-edges` repairs existing rules. Closes #152.

- **The vault is legible in Obsidian.** Three separate causes made the graph
  view unreadable. (1) `HOME.md`'s generated dashboard had no caps: it listed
  every rule by trust tier and then every rule again per topic tag — 5,848
  wikilinks over 1,731 pages on a real vault. Since HOME then linked to nearly
  every note, the graph drew it as one hub with an edge to everything, hiding
  the per-agent and per-topic structure underneath. Sections are now capped
  (20 high-trust, 10 per topic, each reporting how many were dropped) and the
  `source_count == 1` tier — 98% of a mature vault, and the tier with the
  least evidence behind it — is summarized as a count instead of listed. The
  block drops from ~6,600 lines to a few hundred. (2) `shared/_archive/`
  (reclassify originals) and `bots/*/logs/` are skipped by every mnemo walker
  (`core.filters.iter_shared_pages`) but were fully visible to Obsidian, which
  rendered ~1,800 of them as an orphan ring around the content; scaffold now
  writes `.obsidian/app.json` with matching `userIgnoreFilters`. (3) Scaffold
  shipped the graph *theme* (`graph-dark-gold.css`) but never `graph.json`, so
  every new vault opened on Obsidian's defaults — orphans shown, no
  exclusions, and one undifferentiated color for `reference`, `project`,
  `feedback` and `bots`. It now ships a configured `graph.json` with color
  groups per page type. Both `.obsidian` files are written only when absent,
  so a user's own tuning survives (scaffold runs on every SessionStart).
  Closes #148, #149, #150.

- **The reflex index tokenizes only the rule body into the `body` field, not
  the whole page.** `reflex/index.py` passed the page text with its YAML
  frontmatter into the BM25F `body` field, so every rule's `name`,
  `description` and `evidence.quote` were counted twice (once in their own
  weighted field, once again as body), and source paths, session ids,
  timestamps and keys like `extraction_run` / `auto-promoted` were
  searchable as if they were rule text. On the real vault (1,637 docs) the
  average body length falls from 250 to 176 tokens and the vocabulary from
  25.9k to 23.7k terms; replaying 1,828 logged prompts, the old top-1 scored
  partly on frontmatter-only tokens 114 times (project names and session
  UUIDs from `sources:` paths), one rule scored 48 on a prompt because its
  own evidence quote was in the body, and the same prompt now scores 7.5.
  Scores deflate about 8% at the median, so the per-project calibrator will
  re-settle the emit rate over its next runs. #137

## [1.3.1] — 2026-09-03

The follow-up to the distribution release. The exported rules file is now
compact by default — the lead sentence and your quote per rule, under half
the tokens on real projects, with `--full` for the whole bodies — and every
reader of the rotating reflex and MCP access logs now sees the rotated file,
so windowed numbers stop silently shrinking at 1 MB. The auto-promoter's
maintainer note no longer leaks into previews and exports, and the digest
and `mnemo status` agree on the emit rate.

### Changed

- **`mnemo export` writes each rule as its lead sentence and your quote;
  `--full` writes the whole body.** The exported block rides on every prompt
  the host sends, and with whole bodies it ran to 6,700 tokens on a 26-rule
  project and 6,600 on an 18-rule one — past the 4,000-token warning for the
  median real project, which made the warning noise. The compact block is
  2,900 and 1,900 tokens on the same projects (−57% / −71%): the heading,
  the paragraph before `**Why:**` / `**How to apply:**`, and the
  `> you said:` line, with one note at the top pointing the tool at the
  `read_mnemo_rule` MCP tool for the rest. `mnemo init --host cursor|codex`
  writes the same compact block. The manifest records which format was
  written so `mnemo status` compares like with like (a manifest from an
  earlier version reads as full), and in `--full` mode the size warning
  suggests dropping `--full` before `--limit`. The 4,000-token warning
  itself is unchanged: on the compact block it fires around 35 rules, where
  it means something again. #129

### Fixed

- **Every reader that computes a windowed metric off a rotating log now sees
  the rotated file, not just the live one.** `core/numbers.py`
  (`mnemo status`) already read `reflex-log.jsonl.1` alongside the live log,
  but the weekly digest, the reflex calibrator, the dead-rule sweep's two
  liveness checks, and the MCP access-log summary read only the live file —
  so `mnemo status` and the digest agreed on the emit rate (#128) only until
  the log rotated at 1MB, and the dead-rule sweep's 180-day window was
  silently truncated to whatever the live file still held, making a rule
  whose last mention rotated into `.jsonl.1` look dead. These readers now
  share one `core/log_utils.iter_rotated_rows` helper that yields rows from
  the rotated file then the live one. (`mnemo doctor`'s session-cap check
  still deliberately reads only the live file, capped at its last 5000
  lines — a lightweight recency check, not a windowed metric.) #136

- **The last three readers of the MCP access log see the rotated file too.**
  The popularity tiebreak in `list_rules_by_topic` (a 30-day window of
  `read_mnemo_rule` calls), the telemetry doctor's anomaly scan (which needs
  five `llm.call` samples before it flags anything), and the recall
  harness's list→read pairing plus its phase-3 entry count all read only
  the live `mcp-access-log.jsonl`, so a rotation at 1MB silently shrank the
  popularity window, reset the telemetry sample, and dropped any list→read
  pair that straddled the boundary. All three now go through
  `iter_rotated_rows`, which also means the popularity reader no longer
  raises on an undecodable byte in a torn line. #140

- **The auto-promoter's advisory sits below the rule and stays out of
  previews, exports and indexes.** A rule promoted without its `enforce:`
  block used to open with a two-line maintainer note ("mnemo auto-promoter
  stripped an `enforce:` block…"), so the reflex preview — the first 300
  characters of the body — and `mnemo export` handed the host that note
  instead of the rule. The note now follows the rule text on disk, drops its
  reference to a docs path that does not exist in user checkouts, and no
  longer opens the reflex preview, lands in the exported rules file, or feeds
  BM25 and similarity scoring — including on pages written by earlier
  versions. `read_mnemo_rule` still returns the page as written. #134

- **Reflex log readers now treat `exported` as liveness, and the digest
  agrees with `mnemo status` on the emit rate.** The dead-rule sweep counted
  only `emitted` as proof of life, so a rule delivered exclusively through
  the exported rules file — working exactly as designed — accrued no
  `emitted` mentions and was proposed dead after the sweep window; it now
  also counts `exported` mentions. Separately, the weekly digest computed
  `reflex_emit_rate` over every logged row while `core/numbers.py` (which
  backs `mnemo status`) excludes `all_exported` rows from both sides of the
  ratio, so the two surfaces could report different numbers for the same
  log; the digest now excludes them too, via the same `is_reflex_opportunity`
  check both readers now share. (#128)

## [1.3.0] — 2026-09-03

The distribution release. Rules leave the vault: `mnemo export` writes them
to a file the host loads on its own, and `mnemo init --host cursor|codex`
registers the MCP server and that file in Cursor and Codex. The README opens
with a recorded GIF of the five-minute loop — the real `claude` CLI in a
throwaway repo, correction to receipt, no staging — and recording it found
the bug that would have made it impossible for every new user: a vault with
a handful of rules could never clear the reflex floor, so the first rule
anyone learned never fired. The floor now scales with the vault.

### Added

- **`mnemo export`.** Writes the current repo's learned rules — `feedback`
  and `user` pages attributed to it, plus universal ones — to a file the
  host loads on its own: `.claude/rules/mnemo.md` (default), a managed block
  in `CLAUDE.md` (`--target claude-md`), `.cursor/rules/mnemo.mdc`
  (`--host cursor`) or a managed block in `AGENTS.md` (`--host codex`).
  Each rule carries the user's own quote. `--dry-run`, `--limit`,
  `--all-types`, `--remove`. A per-project manifest under the vault's
  `.mnemo/export/` lets `mnemo status` report staleness, and the reflex
  suppresses winners that the exported file already carries (`mnemo why`
  marks them `exported`; those prompts are excluded from the emit rate and
  from calibration); a note on stderr flags user-profile pages, which can
  carry names or emails.
- **Cursor and Codex.** `mnemo init --host cursor|codex` registers the MCP
  server in that tool's config (`~/.cursor/mcp.json` or `<repo>/.cursor/mcp.json`;
  `codex mcp add`, with a TOML snippet when the binary is missing) and writes
  the rules file through `mnemo export`. `mnemo uninstall --host …` removes the
  registration. `mnemo status` lists wired hosts, `mnemo doctor` verifies the
  registered command exists. Hooks, transcripts and auto-memory stay Claude
  Code-only (#127 keeps that half).
- **Demo GIF.** `docs/assets/loop.gif` shows the five-minute loop — correct
  Claude, `mnemo learn`, the next prompt already knows, `mnemo why` shows the
  receipt — recorded from `tools/demo/loop.tape` against the real `claude`
  CLI in a throwaway repo (`tools/demo/setup.sh`).

### Fixed

- **Reflex cold start.** A vault with a handful of rules could never clear
  `absoluteFloor` — BM25 idf tops out near 0.3 per term at one rule, 1.3 at
  five — so the first rule learned never fired on the next prompt. The floor
  now scales with the vault's idf ceiling below
  `reflex.thresholds.floorReferenceDocs` (default 30; vaults at or above it
  are unchanged), and `mnemo why` shows both the configured and the
  effective floor.

## [1.2.0] — 2026-09-02

The follow-ups release. The corrections-layer reviews left seven findings;
this closes all of them. Rule pages now carry `slug:`, so the indexes, the
MCP tools, `disable-rule` and `mnemo learn` finally agree on one identifier
(the migration runs itself on the next session start). A tripped circuit
breaker says so instead of going silent. Briefings get a retention policy
that never deletes the evidence behind a live rule. `mnemo reclassify` only
keeps a rule for a quote that actually says something. And two things the
reviews did not expect: the plugin's MCP server could never spawn on
macOS/Linux (shell-less spawn of a shebang-less launcher), fixed here, and
the test suite used to write into the developer's real vault, now isolated.

### Changed

- **`mnemo reclassify` keeps a rule only for a quote that says something.**
  A `keep` now needs a user quote with at least five content words (after
  Portuguese and English stopwords) and a `link` sentence from the grader
  stating what in the quote establishes the rule; bare approvals
  ("implementa os fixes", "vamos testar a opcao A?") demote. The same
  specificity bar applies to the extraction-time evidence check. `link` is
  written into the kept page's `evidence:` block and shown in the plan
  printout. Calibrated on the live vault's saved plan: of 61 keeps, 54
  survive, and the 7 demoted are all approvals or questions
  (`tools/calibrate_keep_bar.py` replays any saved plan). (#119)
- **Rule pages carry `slug:`.** Pages were written under the normalized LLM
  slug but only carried `name:` (the display name), so the reflex and
  activation indexes, `disable-rule`, `read_mnemo_rule`, `list_rules_by_topic`
  and `mnemo why` keyed by display name while the learned ledger and
  `mnemo learn` used the slug. New pages get `slug:` right after `name:`;
  existing pages are stamped once at the next session start (or
  `mnemo extract`) and both indexes are rebuilt in the same step. Project
  pages keep their composite `<agent>__<slug>` stem, which is what the ledger
  already used. `disable-rule` still accepts a display name. Old rows in the
  activation activity log keep their display-name keys. `mnemo doctor`
  reports pages still missing `slug:`. Live vault: 1659 pages, all stamped
  with their existing file stem, no identifier changes. (#114)

### Added

- **The circuit breaker says so when it trips.** SessionStart now emits one
  line — `[mnemo] paused: circuit breaker open (N errors in the last hour,
  most from <where>) … run `mnemo fix` to reset` — instead of going silent;
  `mnemo status` and `mnemo doctor` show the count, the top error source,
  and the remedy. The other hooks stay quiet on purpose. (#115)
- **Briefings have a retention policy.** `briefings.retentionDays` (180; 0
  disables) and `briefings.keepPerAgent` (20 newest, always kept). Any
  briefing a live rule cites in `sources:` is never deleted. Runs from
  SessionStart at most once a week, or on demand with
  `mnemo briefing --prune [--dry-run]`; `mnemo status` shows
  `Briefings: N across M agents (X MB) — K prunable`. On the live vault
  today: 299 briefings, 293 protected, 0 prunable. (#116)

### Fixed

- **The plugin's MCP server could not spawn on macOS/Linux.** `.mcp.json`
  pointed at `bin/mnemo.cmd`, a shebang-less polyglot; Claude Code spawns
  stdio MCP servers without a shell, so the spawn failed with ENOEXEC (hooks
  were unaffected — they run through a shell). The entry now spawns
  `bash bin/launch`. Opened as a project, the same file resolves to the
  editable install instead of the literal `${CLAUDE_PLUGIN_ROOT}` string that
  shadowed the working user-scope server. Native Windows plugin installs are
  unverified either way — see #121. (#118)
- `mnemo doctor`, `list-enforced`, `disable-rule` and the EOS proposer no
  longer walk `shared/_archive/**` (reclassify originals); the stripped-enforce
  advisory listed ~1.5k archived copies after `mnemo reclassify --apply`.
  `list-enforced` and the advisory also stop descending into `_inbox`, which
  the hook never enforces. (#120)

### Internal

- Every test runs under a temporary HOME with `MNEMO_CONFIG_PATH` redirected,
  and a guard fails any test that changes the real vault's `.errors.log`,
  `shared/` or `~/.claude/projects`. Hook tests used to write into the
  developer's vault and trip its circuit breaker for an hour. (#117)

## [1.1.0] — 2026-09-02

The corrections release. 1.0 proved mnemo could measure itself; the post-1.0
audit then showed what it was measuring: a vault where 98% of rules had a
single source, ~40% were textbook advice the model already knew, and the
top-ranked rule was the extractor's own prompt echoed back. The root cause
was that the extractor never saw the user's words. This release makes
mnemo the corrections layer it claimed to be: briefings quote you verbatim
and verify the quote against the transcript, feedback rules need that quote
to be promoted, re-learned rules reinforce the existing page instead of
minting a duplicate, nothing leaves the machine or spends an LLM call
without opt-in, every learned rule is announced with a one-line veto, and
`mnemo learn` closes the loop inside a single session. The README now says
only what `mnemo status` can print.


### Changed

- **The first extraction of a vault is never debounced.** A fresh vault has no
  extracted pages, and only an extraction produces the pages the count gate
  counts, so `extraction.auto.minNewMemories` held new installs back forever.
  A missing last-run marker now runs immediately — that first pass is what
  shows a new user mnemo works at all.
- **Session briefings count as new material** toward the automatic-extraction
  count gate, alongside memory files. A session whose only product is a
  correction mutates no files but does write a briefing, which is exactly what
  consolidation reads. The time gate is untouched: new material does not buy a
  pass through `extraction.auto.minIntervalMinutes`.
- `generate_session_briefing` gained a `min_mutations` keyword (default `1`,
  unchanged behaviour) so a caller can brief a session that touched no files.
  `mnemo learn` passes `0`.
- **Feedback rules now require evidence.** The session briefing carries a
  `## Corrections` section quoting the user verbatim; each quote is checked
  mechanically against the transcript and fabricated ones are dropped. A
  feedback page reaches `shared/feedback/` only when it cites one of those
  quotes as `evidence:` from one of its own source briefings
  (`confidence: verified`). Everything else is staged as an inferred
  `reference` page in `shared/_inbox/reference/` with `demoted_from: feedback`
  — including feedback-typed pages from Claude Code's own auto-memory, which
  carry no user quote.
- **Extraction reinforces existing rules instead of minting duplicates.** The
  consolidation prompt lists the vault's existing slugs, and a similarity pass
  (weighted Jaccard ≥ 0.32 on stemmed name/description/body AND name-stem
  overlap ≥ 0.27, calibrated on a 1,436-page vault: 38 redirects, 2 judged
  false) redirects a page onto an existing slug when it states the same rule,
  so `source_count` accrues and universal promotion can fire. Never redirects
  onto a dismissed slug.
- The reflex scores the evidence quote as its own BM25F field
  (`reflex.bm25f.fieldWeights.evidence`, default 2.5).
- **The autopilot no longer touches the network without opt-in.**
  `autopilot.network.enabled` now defaults to `false`: self-fix cures still
  apply in place and every run is logged to `.mnemo/autopilot-runs.log`, but
  nothing calls `gh`. If you relied on digest issues, self-fix PRs or outcome
  polling, restore them with
  `{"autopilot": {"network": {"enabled": true}}}`.
- **The first-run backfill is opt-in.** `backfill.autoOnFirstSession` defaults
  to `false`, so a fresh install no longer sweeps your old transcripts through
  the LLM unasked. The first session in a repo with harvestable transcripts
  prints a one-line invitation instead — how many sessions, what it would
  cost, and that `mnemo backfill --dry-run` prices it exactly — shown once per
  repo. Restore the old behaviour with
  `{"backfill": {"autoOnFirstSession": true}}`.

- **Self-fix works on Windows.** The perimeter guard compared backslash
  paths against `shared/`-style prefixes, so every autopilot self-fix PR on
  Windows aborted with a perimeter violation. Paths are now compared in POSIX
  form.

### Added

- **`mnemo learn` / `/mnemo:learn` — teach the vault from this session, now.**
  Correct Claude in your own words, run it, and the rule is live on your next
  prompt. It runs the two stages the `SessionEnd` hook runs, but synchronously
  and in the foreground: a session briefing (with `min_mutations=0`, because
  the session that earns a `mnemo learn` is one where you only *said*
  something) carrying the verified `## Corrections`, then extraction **scoped
  to that briefing alone** — other projects' dirty pages wait for the normal
  end-of-session run rather than being swept into LLM calls you didn't ask
  for. It prints what it read, the briefing and its correction count, and one
  `learned:` line per rule with your own sentence quoted back as the evidence.
  It never opens a PR and never touches the network beyond the LLM calls
  extraction already makes. If another extraction holds the lock it stops and
  says so — that run will pick up this briefing anyway. `--session <id>` learns
  from an earlier session, `--dry-run` names the transcript it would read.
  See [Five minutes](docs/getting-started.md#five-minutes).
- `mnemo reclassify` — grades the existing feedback vault under the same rules
  (keep / demote / merge / archive) with a saved plan, `--apply` (no LLM
  calls), `--limit`, and a byte-exact `--undo`.
- Prompt-echo guard: pages that repeat the extractor's own instructions are
  staged as reference pages instead of being promoted. E-mails, API tokens and
  32-character hex ids are redacted from rule name, description and body
  before writing (RFC 2606 example domains and `git@` remotes are left alone).
- **Learned rules are announced at session start, each with its veto.** A
  `[mnemo learned since your last session]` block lists what extraction
  promoted since this project last looked — up to 5 bullets, each ending in
  `veto: mnemo disable-rule <slug>`, with the source sentence shown for
  `verified` rules. A rule written silently is a rule nobody can correct;
  this is the disclosure half of letting extraction write on its own. Backed
  by `.mnemo/learned.jsonl` and a per-project marker in
  `.mnemo/announced.json`, so nothing is announced twice.
- `mnemo status` grew a **Recently learned** section: the last 10 rules
  relevant to the current project, announced or not — the overflow the
  session-start block points at.
- `mnemo disable-rule` is now a public command rather than an internal one,
  since the session-start announcement hands it to users by name.
- **`mnemo status` prints a `Numbers (last 14 days)` section** — the reflex
  emit rate (from `.mnemo/reflex-log.jsonl`) and `primacy@5` (from the last
  `mnemo recall` run, `.mnemo/recall-report.json`), in the same shape the
  README quotes them:

  ```
  Numbers (last 14 days):
    reflex: injected on 90 of 1041 prompts (8.7%)
    recall: primacy@5 41.7% over 72 cases (mnemo recall, 2026-09-01)
  ```

  Either line — or the whole section — is omitted when its source file is
  missing or holds no row inside the window: a number the tool cannot measure
  is a number the README may not claim, so "no data" is never rendered as
  "0%". New `mnemo.core.numbers` module backs both readers and is fail-safe by
  construction (a missing, truncated, or hand-edited file yields `None`, never
  an exception).

### Changed

- **Plugin slash commands are down to five: `status`, `why`, `doctor`,
  `learn`, `help`.** `/mnemo:open`, `/mnemo:fix`, `/mnemo:statusline` and
  `/mnemo:migrate` are removed — each was a thin wrapper around a CLI command
  that's just as easy to type: `mnemo open`, `mnemo fix`,
  `mnemo statusline --install`, `mnemo migrate-plugin`. The five that remain
  are the ones worth a slash: read state, or teach the vault, without leaving
  the conversation. `mnemo help` (and `/mnemo:help`) still lists every
  command, including the ones that lost their slash.
- **README rewritten** around the corrections layer, an honest comparison to
  plain Claude Code memory and to Obsidian-backed note vaults, the dated
  `Numbers (last 14 days)` figures above in place of prior claims, and the
  opt-in defaults shipped in WS-B (network, backfill) stated as opt-in rather
  than implied always-on. The **"zero network calls" claim is gone** — mnemo
  calls the `claude` CLI for extraction and briefings by design; what's opt-in
  is the autopilot's own network use (`gh`), not LLM calls.

## [1.0.0] — 2026-09-01

The 1.0 gate was never code quality — the plugin distribution, four-platform
binaries, ~2000 tests and the autopilot have been in place for months. What
blocked it was not having an honest number for "does this work?". This release
is the one where that number exists: a 14-day measured window (2026-08-04 →
08-18) plus 14 more days on the fixes below, with every figure produced by
commands that ship in the box (`mnemo recall`, `mnemo recall-sessions`,
`mnemo why`, `mnemo doctor`). The reflex injects on ~7–8% of prompts with the
default thresholds; ranking inside a topic bucket was the weak point and is the
headline change here.

### Added

- **Query-aware ranking in `list_rules_by_topic`.** The tool takes an optional
  `query` — the agent's task text — and reranks the topic bucket with the
  reflex's BM25F scorer: rules that score ≥ 1.0 against the query rise by
  score, everything else keeps its `source_count` order as a floor. Without
  `query` the output is byte-identical to before. The SessionStart injection
  now tells agents to pass their task, so the feature is live end-to-end
  without any configuration.

  Why: in buckets past ~20 rules almost everything ties at `source_count=1`
  (46 of 48 in the largest), so the tiebreak degenerated into alphabetical
  noise and the relevant rule sank to rank ~40. Measured on the real code path
  over big buckets (530 evaluations, 84 cases): primacy@5 16% → 31%,
  primacy@3 10% → 23%.

- **`mnemo recall-sessions`.** A second recall harness, built from the sessions
  each rule was extracted from rather than from the access log. `mnemo recall`
  tops out at the number of `read_mnemo_rule` calls in the log; this one has a
  case for every session that produced a rule, so a ranking change can be told
  apart from noise. It is a delta detector — its absolute numbers are not
  comparable to `mnemo recall`, and the command says so on every run.


- **Cold-start backfill.** A new vault used to inject nothing for weeks,
  because it had nothing to say. mnemo now reconstructs memory from the
  session transcripts Claude Code has been keeping all along
  (`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`), which the existing
  extraction pipeline turns into rules.

  ```bash
  mnemo backfill                  # this repo
  mnemo backfill --all            # every project on the machine
  mnemo backfill --dry-run        # count, projects and a rough token estimate
  ```

  Also `--project NAME`, `--limit N`, `--yes/-y`, and `--retry-failed`. The
  command prints what it's about to read and asks before spending. Failures are
  recorded per transcript and stepped over; an interrupted sweep resumes where
  it stopped; a transcript that fails three times is retired until
  `--retry-failed`. Exit codes: `0` done, `1` some sessions failed, `2` aborted,
  `130` interrupted.

- **Backfilled material always stages for review.** Pages produced from old
  transcripts are stamped `origin: backfill`, and every rule derived from one
  is written to `shared/_inbox/<type>/` — never auto-promoted into `shared/`,
  whatever its source count, and the stamp survives across extraction runs.
  These are the model's reconstruction of sessions nobody watched; they get a
  human before they get to influence anything. `mnemo doctor` lists what's
  staged, and reports a first-run sweep that failed where nobody could see it.

- Config: `backfill.enabled`, `backfill.installCap`,
  `backfill.minFileMutations`, `backfill.autoOnFirstSession`. See
  [docs/configuration.md](docs/configuration.md).

### Fixed

- **Per-project reflex calibration never reached the hook.** The autopilot
  has written `.mnemo/reflex-config.<project>.json` since 0.17 — and nothing
  on the prompt path ever read it. Every prompt ran on the 1.5/2.0 defaults,
  including projects the calibrator had tuned. The hook now merges the project
  file over the global config per key (file > global > defaults; a missing or
  corrupt file changes nothing). The calibrator also used to hand back the
  *defaults* whenever a project's emit rate was inside the 3–12% band, which
  with live wiring would have undone a working calibration the moment it
  worked; it now keeps the current thresholds when in band. Emission receipts
  record the thresholds that admitted them, so `mnemo why` shows the real
  arithmetic per project.

- **Concurrent SessionStarts could crash on the index files.** Both the reflex
  index and the rule-activation index staged writes through a fixed
  `<file>.tmp` sibling, so two sessions starting at once raced: one process's
  rename consumed the other's temp file and the loser raised
  `FileNotFoundError`. Both writers now go through `core/atomic.py` — a unique
  temp file per call, plus retries — the same pattern the session cache
  already used.

- **Autopilot self-fix PRs never carried a diff.** Three stacked defects:
  the branch was cut with `git checkout -b` in the live checkout (stealing
  `HEAD` from whoever was working there), nothing ever committed, and the
  repository was resolved from the process cwd rather than from the vault.
  PRs are now built in a throwaway `git worktree`, committed, and anchored on
  the repository that holds the edited files. Telemetry findings, which change
  no files, are filed as issues instead.

- **The dead-rule sweep archived every rule, at any age.** Autopilot's sweep
  archives rules with no usage signal in the last 180 days, behind an age guard
  meant to protect new rules. The guard read a `created_at:` frontmatter field
  that no writer in mnemo has ever emitted — pages carry `extracted_at:` /
  `promoted_at:` / `extraction_run:` instead — so it parsed to "unknown age"
  for every page ever written, and it was coded to archive on unknown
  (`if created is not None and created > cutoff`). It failed open where it had
  to fail closed. A rule created minutes ago was swept as dead.

  The damage compounded with the promotion bug above: rules that couldn't
  inject couldn't earn a usage signal, so they looked dead and were swept. The
  categories that inject least were wiped fastest. On the maintainer's vault
  that read 942 archived against 113 live, `feedback` fell 15 → 2 in a single
  session, and 7 hand-promoted rules were archived within hours of promotion.

  Age is now taken from fields pages actually carry — `created_at`,
  `promoted_at`, `extracted_at`, `extraction_run`, then filesystem mtime — and
  a page whose age cannot be established is **never** archived. Nothing was
  deleted: archived rules are in `shared/_archive/` and can be moved back. `mnemo doctor` and the
  docs both tell you to review a staged page and move it into
  `shared/<type>/`. Promotion is a plain `mv` — there is no promote command,
  and nothing rewrites the frontmatter — so the page kept the `needs-review`
  tag it was written with. The visibility filter read that tag as "still a
  draft" and hid the page from injection, the MCP tools, the reflex index and
  the HOME dashboard. Every keeper anyone ever promoted by hand was silently
  doing nothing; on the maintainer's own vault that was 7 rules.

  **Location is now the only authority on draft-ness**: under
  `shared/_inbox/` it is a draft, under `shared/<type>/` it is live. The tag
  is left as a harmless marker (`topic_tags` still strips it, so it never
  shows up as a topic). `stability: evolving` is unchanged and still hides a
  page wherever it lives.

  ⚠ **Behaviour change for existing vaults.** Rules you promoted months ago
  and assumed were live will now actually become live, on the first session
  after upgrading. If some of them shouldn't be, move them back under
  `shared/_inbox/<type>/` or delete them — see
  [docs/troubleshooting.md](docs/troubleshooting.md).

### Removed

- **`extraction.preferAPI`**, a documented config key that nothing has ever
  read. Every LLM call goes through the `claude` CLI and uses whatever auth
  that CLI already has; there was no code path the flag could switch. It is
  gone from the defaults and from the config reference. A config file that
  still carries the key keeps merging harmlessly — no migration needed.

### ⚠ Upgrade note — this spends LLM calls on your next session

**Read this before upgrading.** The "have we done the first-run sweep?" marker
defaults to *not done* for every vault that existed before this release. So
your next session is treated as a first session: mnemo spawns a background
sweep of **the repo that session is in**, harvesting up to `backfill.installCap`
(**20**) of its most recent transcripts.

What that costs: **up to 20 calls to the `claude` CLI**, one per session,
using `extraction.model` (Haiku by default). On a Pro/Max subscription that
draws on your subscription with no per-token charge; on API-key auth it is
billed. It happens once per vault — not once per repo, not once per session —
and only for the repo you happen to be in. Sessions that touched no files are
skipped without a call.

It runs detached, so it will not slow your session down, and its output goes
nowhere — `mnemo doctor` is where you find out how it went.

To upgrade without it ever running, put this in
`~/mnemo/mnemo.config.json` **first**:

```json
{ "backfill": { "autoOnFirstSession": false } }
```

`mnemo backfill` still works by hand after that. To disable backfill entirely,
including the command:

```json
{ "backfill": { "enabled": false } }
```

If it already ran and you'd rather it hadn't: the pages it wrote are in
`bots/<repo>/memory/` alongside the `origin: backfill` stamp, and anything
extracted from them is sitting in `shared/_inbox/` — not in `shared/` — so
deleting them is a local, reversible cleanup.

## [0.17.2] — 2026-08-01

The release that actually ships binaries. 0.17.0 and 0.17.1 were both blocked
before publishing them; this is the first tag whose GitHub Release carries the
four platform builds the plugin install needs.

### Fixed

- **Intel macOS built on a runner that never started.** The `macos-13` job sat
  queued indefinitely on two consecutive releases — that runner image is on
  its way out. Switched to `macos-15-intel`. PyInstaller cannot
  cross-compile, so an Intel build needs an Intel runner, and dropping the
  target would leave Intel Mac users with a silent no-op: the launcher fails
  open when its asset is missing.

### Added

- `workflow_dispatch` on the release workflow, so the binary matrix can be
  exercised without burning a version number. All three publishing jobs are
  guarded on the ref being a tag, so a manual run builds and stops.

## [0.17.1] — 2026-08-01

Fixes the 0.17.0 release itself. That tag published to PyPI and npm but
shipped **no binaries**, so the plugin install it was cut for did not work.

### Fixed

- **The Windows binary job failed on a missing `shasum`.** Neither `shasum`
  nor `sha256sum` is guaranteed in the Git Bash that GitHub runs
  `shell: bash` under on Windows. Checksums are now generated with Python,
  which every one of these jobs already sets up, in a format byte-identical
  to `shasum -a 256` so `bin/launch` parses it unchanged.
- **The release published before it built.** `publish-pypi` ran first and
  unconditionally, so when the Windows job failed, PyPI and npm were already
  advertising a version whose release had no binaries — a version number,
  once taken, cannot be reused. Publishing is now gated behind the binary
  build: the fallible step runs first, the irreversible step second. Tests
  pin the ordering so it cannot quietly regress.

## [0.17.0] — 2026-08-01

This is the release that makes the plugin install work: the launcher fetches
its binary from the GitHub Release matching the plugin's version, and 0.16.0
predates the binary build job.

### Added

- **Install with no terminal.** mnemo is now a Claude Code plugin:

  ```
  /plugin marketplace add xyrlan/mnemo
  /plugin install mnemo@mnemo-marketplace
  ```

  No Python, no Node, no PATH setup. It ships as a self-contained binary that
  the plugin fetches for your platform on first use, verified against a
  published SHA-256. npm and PyPI keep working for dotfile setups and CI.
- Standalone binaries for macOS (arm64/x64), Linux x64, and Windows x64,
  built and attached to each GitHub Release.
- `mnemo statusline --install` / `--remove`. Plugins cannot declare a status
  line, so the heartbeat becomes an explicit opt-in rather than a reason to
  open a terminal. Still additive — any status line it replaces is preserved.
- `/mnemo:migrate` (`mnemo migrate-plugin`), for users who ran `mnemo init`
  before installing the plugin. Both sets of hooks otherwise stay live and
  every session gets doubled capture, injection, and enforcement.
- `mnemo hook <event>`, the binary-invocable equivalent of
  `python -m mnemo.hooks.<event>`.

### Fixed

- **The vault was never scaffolded under a plugin install.** No `mnemo init`
  runs, and the hooks only created the directories they touched — so there was
  no `HOME.md`, no config file, and no `shared/` for extracted rules to land
  in. `SessionStart` now scaffolds when `HOME.md` is absent.
- **`mnemo status` reported "settings.json missing" to plugin users** — i.e.
  "not installed" — because hook health was read only from `settings.json`,
  where a plugin legitimately has nothing. It now reports the plugin's hooks.
- **`npx @xyrlan/mnemo install` bailed when `python3` was absent even with
  `uv` installed.** It checked for Python *before* choosing an installer, so
  the one tool that provisions its own CPython — and would therefore have
  worked — was never reached.
- Hook-ownership detection no longer matches a bare `"mnemo"` substring, which
  counted any unrelated command sitting under a path containing "mnemo".

### Changed

- Docs rewritten around the plugin install, and corrected against the code:
  `getting-started.md` documented 2 hooks where 4 ship, `configuration.md`
  described the v0.1 key set, and both used a `/mnemo <cmd>` slash syntax that
  never existed. Config keys in the reference tables are now fully qualified
  so any row can be copied straight into `mnemo.config.json`. New
  `docs/obsidian.md`; the v0.1 backlog moved to `docs/archive/`.
- The vault templates shipped into every new vault said background features
  were "off by default". They have been on since 0.15.0.
- A test suite now checks the docs against the code: every referenced command
  and config key must exist, and internal links must resolve.

## [0.16.0] — 2026-08-01

Three months of fixes that were merged to `master` but never released: the
0.15.0 tag was the last one cut, so PRs #86–#92 never reached PyPI or npm.
This release ships them and closes the gaps that let the drift happen.

### Fixed

- **Windows: hooks and statusLine were wired with backslash paths.** Claude Code
  dispatches hook commands through bash/Git Bash, which eats `\`, so the
  installed commands could fail to run. `mnemo init` now emits POSIX-style
  paths for the hook and statusLine commands (#88).
- **Autopilot reflex calibration was measured against the wrong denominator.**
  `emit_rate` divided by every prompt seen, including ones skipped before
  scoring ever happened (`index_missing`, `below_min_tokens`). That deflated
  the observed rate 2–4× and meant genuinely chatty projects never got tuned
  down. It now divides by *eligible* prompts, and the min-sample guard counts
  eligible prompts too (#89).
- **Autopilot's pytest gate spawned a bare `pytest`,** which resolved against
  whatever happened to be on PATH rather than the interpreter running mnemo.
  It now spawns via `sys.executable` (#90).
- **Autopilot self-fix healed source-path hygiene** — machine-absolute paths in
  rule provenance are relativized, and sources whose briefing moved are
  relocated — and the universal-promotion signal that fired on nearly every
  rule was dropped as noise (#91).
- **Universal promotion was blocked and project rules went unindexed** in the
  extraction and activation paths (#86, #87).

### Changed

- `mnemo --version`, the landing card, and the MCP server's advertised version
  now resolve through a single helper (`mnemo._version.resolve_version`). All
  three previously inlined the same `importlib.metadata` lookup, and the MCP
  server didn't do the lookup at all — it reported a hardcoded `0.8.0`. The
  fallback is now the baked-in `__version__` rather than the literal
  `"unknown"`, which matters for builds with no distribution metadata to read.

### Release tooling

These are the reasons 0.16.0 was three months late; each is now enforced.

- `tools/sync_npm_version.py` also rewrites `PIN_SPEC` in
  `npm/lib/bootstrap.js`. It was a hand-edit in the release commit, and
  forgetting it ships an npm wrapper that installs the *previous* minor.
- `tools/sync_plugin_manifest.py` also bumps `.claude-plugin/marketplace.json`,
  which nothing synced and which had drifted twelve minors behind (0.4.0).
- CI now runs the npm test suite and fails when the generated
  `.claude-plugin/` manifests are stale. Previously `npm test` ran only during
  publish — *after* the PyPI job had already succeeded — so an npm-side
  regression would leave the two registries on different versions.

## [0.15.0] — 2026-05-04

### Changed (default behaviour)

- **Autopilot is ON by default.** Fresh vaults activate the autopilot loop
  (Tier 0 digest, Tier 1 self-fix, Tier 2 BM25 / reflex tuners, Tier 3
  end-of-session proposer) without `mnemo autopilot on`. Vaults that
  previously ran `mnemo autopilot off` keep that explicit choice — the
  on-disk state file always wins over the new no-file default.
  Disable with `mnemo autopilot off` (#80).

### Added

- `mnemo help --all` flag surfacing 7 advanced/maintenance commands
  (`telemetry`, `recall`, `migrate-worktree-briefings`, `dedup-rules`,
  `disable-rule`, `list-enforced`, `regen-graph-edges`) that are now
  hidden from the default help listing (#83).
- `mnemo init` ends with a richer summary card: vault path, verify
  command, and live autopilot state — so users learn how to toggle the
  new default (#82).
- Bare `mnemo` (no subcommand) now prints a 5-line orientation card
  instead of the full argparse dump. `mnemo help` and `mnemo --help`
  still show the full listing (#82).

### Fixed

- **Python 3.14 argparse regression:** `help=argparse.SUPPRESS` on a
  subparser stopped hiding the entry (rendered the literal
  `==SUPPRESS==` string instead). Switched the four internal
  subparsers (`briefing`, `mcp-server`, `statusline`,
  `statusline-compose`) to omit `help=` entirely, which hides them on
  every supported Python version (#82).
- **macOS `pip --user` install hint:** the npm bootstrap told users to
  add `~/.local/bin` to PATH, but on macOS pip-user lives in
  `~/Library/Python/<X.Y>/bin`. Now the hint shells out to
  `python3 -m site --user-base` for the authoritative path on every
  platform (#81).
- Seven test regressions on master, surfaced after the default flip
  and the Tier 3 git-signal subprocess work (#84).

## [0.11.0] — 2026-04-23

### Breaking

- **Rule schema:** `enforce.deny_command` now requires a paired `deny_pattern` regex.
  Bare `deny_command: "git push"` is rejected at index load. Run `mnemo doctor`
  to surface affected rules; fix by adding a `deny_pattern` that narrows the match,
  or remove the enforce block entirely.
- **Rule-activation index schema:** bumped from v3 to v4 to force a transparent
  rebuild that populates the new `path` field on every entry. No user action required.

### Added

- `mnemo list-enforced` — audit every rule with an `enforce:` block (path + tool +
  trigger + reason). One-shot way to see what can hard-block your tool calls.
- `mnemo disable-rule <slug>` — flip `runtime: false` on a rule's frontmatter
  without touching its body. Suggested by the PreToolUse block message.
- PreToolUse deny envelope now includes the offending rule's path and a
  disable-hint, so users can fix the block without grepping the vault.

### Changed

- Auto-promoted pages have their `enforce:` block stripped (flagged with
  `promoted_without_enforce: true` in frontmatter + review note in the body).
  Manual promotion and hand-authored rules are unaffected. `mnemo doctor`
  surfaces stripped rules so you can review and re-add manually if safe.
- Extractor prompt tightened: the LLM now emits `enforce:` only when the
  source briefing contains explicit blocking intent ("never allow",
  "always refuse", "the hook should block"). A command in backticks is
  no longer sufficient justification.

### Migration

Existing rules with bare `deny_command` will fail to load after upgrade. After
pulling:

1. Run `mnemo fix` — rebuilds the rule-activation index at v4 (adds `path` field).
2. Run `mnemo doctor` — the `rules` check lists every rule rejected by the new
   validator, by absolute path.
3. Fix each offender one of three ways:
   - Add a `deny_pattern` regex that narrows the block (preferred).
   - Remove the `enforce:` block — the rule stays advisory.
   - Run `mnemo disable-rule <slug>` — sets `runtime: false` without edits.

### Skipped versions

v0.9.x and v0.10.x shipped in master but were never reflected in
`pyproject.toml` (both remained at 0.8.0). This release jumps 0.8 → 0.11
to align the package version with the already-shipped feature set. The
v0.10 and v0.9 changelog sections below document the shipped content;
neither version was ever tagged on PyPI.

## [0.10.0]

### Added

- **Session handoff injection.** SessionStart now appends the most recent briefing's body (under `[last-briefing …]`) to the `mnemo://v1` envelope when `briefings.injectLastOnSessionStart` is true (default). Claude wakes up with the previous session's handoff context already in scope.
- **Worktree-aware canonical agent.** New `agent.resolve_canonical_agent` follows `.git` worktree pointers to the main repo, so all worktrees of a repo share a single briefing pool. Briefing writer (SessionEnd) now uses canonical naming.
- **`mnemo migrate-worktree-briefings`** — one-shot CLI to relocate orphan worktree briefings written before the canonical-agent change. Uses a name-prefix heuristic; always `--dry-run` first.
- **`mnemo doctor`** now flags orphan worktree briefing dirs and suggests the migration command. (Silent for early upgraders who haven't written a canonical briefing yet.)
- **Cost telemetry.** `llm.call()` invocations and SessionStart injections both write structured entries to `mcp-access-log.jsonl`. `mnemo telemetry` now reports per-purpose token totals + estimated USD via a hard-coded pricing table.

### Changed

- `_build_injection_payload` accepts `inject_briefing: bool` parameter (default `False` for backwards compat with direct callers; SessionStart hook passes `True` by default via config).
- `access_log_summary.summarize` returns two new top-level keys: `llm_cost`, `injection_stats`. Existing keys unchanged.

## [0.9.0]

### Changed

- `rule-activation-index` schema bumped from v2 to v3. The v0.8.x
  `file_stem` field was added without a version bump, so existing
  v2 indexes silently fall back to slow glob scanning. v3 forces
  a transparent rebuild on first load (already-load-bearing
  auto-rebuild path: `load_validated_json` returns `None` on
  schema mismatch; SessionStart and extract hooks call
  `build_index` whenever `load_index` returns `None`). First run
  after upgrade takes a few seconds longer; nothing else visible.
  ([refactor roadmap PR E](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

### Removed

- `mnemo.core.mcp.counter` v0.8 backwards-compat shim. Importers must use
  `mnemo.core.mcp.session_state` directly. The shim was scheduled for
  v0.9 removal in the v0.8 CHANGELOG. ([refactor roadmap PR D](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

### Internal

- `mnemo.core.rule_activation` monolith (849 LOC) split into a package:
  `parsing.py`, `globs.py`, `matching.py`, `index.py`, `activity_log.py`,
  plus a back-compat shim at `__init__.py`. The pre-v0.9 import surface
  is preserved. `parse_enforce_block` + `parse_activates_on_block` +
  `_describe_*_error` collapsed into a single `parse_block(kind, fm)`
  walker (the two thin wrappers stay for back-compat; the two describe
  helpers are deleted). `_is_universal` promoted to public `is_universal`
  (single in-tree consumer at `reflex/index.py` updated atomically; no
  deprecation window). `build_index` orchestrator decomposed via a new
  `_build_rule_entry` helper (138L → <30L).
  ([refactor roadmap PR G](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))
- `mnemo.core.extract.inbox` monolith (717 LOC) split into an 8-module
  package: `io.py`, `paths.py`, `types.py`, `state_io.py`, `rendering.py`,
  `dedup.py`, `apply.py`, `branches/{auto_promoted,inbox_flow,upgrade}.py`,
  plus a back-compat shim at `__init__.py`. The pre-v0.9 import surface is
  preserved. Five duplication clusters consolidated:

    - D1: `vault_root / "shared" / type / f"{slug}.md"` inlined at 5 sites
      → all routed through `paths._inbox_path` / `paths._promoted_path`.
    - D2: `.proposed.md` sibling construction at 3 sites → `paths._sibling_path`.
    - D3: `"sha256:" + hashlib.sha256(...)` one-liners at 3 sites → new
      public `content_hash(source)` in `inbox/io.py` (polymorphic over
      `Path` / `str` / `bytes`).
    - D4: 5 duplicate fresh-write `StateEntry` blocks → new
      `StateEntry.mark_written(*, run_id, new_hash, source_files,
      source_hash, status=None)` method in `extract/scanner.py`.
    - D5: `SCHEMA_VERSION = 2` duplicated across `inbox.py:25` and
      `scanner.py:39` (ExtractionState dataclass default) → scanner uses
      a `field(default_factory=...)` that defers to
      `inbox/state_io.py::SCHEMA_VERSION` (single source of truth;
      function-level import side-steps the circular dependency).

  New public helpers `atomic_write` and `content_hash` replace the
  underscore aliases `_atomic_write` / `_file_hash`, which remain as
  back-compat re-exports with `DeprecationWarning` (removal scheduled
  for v0.10). `apply_pages` internal dispatch converted to a
  table-driven `(status_predicate, handler)` map (OCP fix). The
  96-line `_apply_inbox` body split into three handlers
  (`_handle_no_entry`, `_handle_dismissed`, `_handle_promoted`,
  `_handle_inbox_status`); the 77-line `_apply_auto_promoted` split
  into smaller per-status helpers. `extract/promote.py` migrated to
  the new `atomic_write` / `content_hash` names. `extract/promote.py`
  mutation sites at lines 80-86 and 95-99 intentionally NOT migrated
  to `mark_written` — they have a divergent shape (no `last_sync`
  update), and forcing a unified API there would change v0.8
  behavior for direct-promotion entries. Deferred:
  `rule_activation.index._atomic_write_bytes` consolidation into a
  shared `io_utils.py` module (follow-up nit-PR).
  ([refactor roadmap PR I](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))
- `mnemo.core.extract.prompts` monolith (529 LOC) split into a `prompts/`
  package with a `templates/` sub-package. Three near-identical
  `build_{feedback,user,reference}_prompt` builders unified into a single
  `build_consolidation_prompt(kind, files, *, vault_root=None)` dispatching
  on a kind→(label, cluster_clause, few_shot) table; thin wrappers preserve
  existing call-sites. `build_briefing_prompt` signature changed — now
  accepts a pre-flattened `transcript: str` rather than `events: list[dict]`
  (SRP fix: event-parsing moved to a new `mnemo.core.transcript` module).
  In-tree callers updated. Pre-v0.9 import surface preserved via the
  package's `__init__.py` shim, including the three underscore-private
  `_FEW_SHOT_*` constants that PR F1's schema regression test accesses.
  ([refactor roadmap PR F2](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))
- `mnemo.cli` monolith (1294 LOC) split into a `cli/` package:
  `parser.py` (argparse + COMMANDS registry + @command decorator),
  `runtime.py` (main + _resolve_vault + _run_open), `commands/*.py`
  (one module per command: init, status, doctor, extract, briefing,
  recall, telemetry, statusline, misc), `commands/doctor_checks/*.py`
  (one module per concern: activation, fidelity, rules, reflex, misc),
  `_helpers/` (absorbs the PR-A `cli_helpers.py`). `cmd_doctor` converted
  to an OCP-compliant `(name, check_fn)` registry — adding a new check
  is now a new row, not an edit to `cmd_doctor`. Pre-v0.9 import
  surface preserved via the package's `__init__.py` shim (re-exports
  `main`, `COMMANDS`, `_resolve_vault` — the three names pinned by the
  public API surface test plus the single symbol the 10 in-repo
  monkeypatches target). ([refactor roadmap PR H](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

## v0.8.0 — 2026-04-19 — Prompt Reflex

### Added

- **UserPromptSubmit Reflex**: new hook that injects 0-2 rule body previews
  inline via BM25F retrieval when a triple-gate confidence test passes.
  Scope respects v0.7 semantics (local + universal per project).
- **`aliases:` frontmatter field**: optional synonym bridge for bilingual
  or domain-synonym matching. Extraction LLM emits it across all three
  system prompts.
- **`reflex` config block**: full tuning surface for thresholds, BM25F
  parameters, field weights, and kill switches (`reflex.enabled`).
- **`mnemo doctor` reflex checks**: `reflex-index-stale`,
  `reflex-session-cap-hit`, `reflex-bilingual-gap`.
- **Statusline**: new `N⚡` segment aggregating today's reflex emissions.

### Changed

- `mcp-call-counter.json` extended in place with `injected_cache` and
  `session_emissions` top-level keys. File path preserved for
  backwards-compatibility with v0.7 statusline + server readers.
- `counter.py` Python module renamed to `session_state.py` with a thin
  compat shim. The shim will be removed in v0.9.
- `PreToolUse` enrichment now honours `enrichment.maxEmissionsPerSession`
  (default 15) and filters against the shared `injected_cache` to avoid
  cross-hook duplicate injections.

### Defaults

- `reflex.enabled = true` by default in v0.8.0 stable. Kill switch: set
  `"reflex": {"enabled": false}` in `mnemo.config.json`.
- `reflex` config block scoped to the knobs that are actually wired:
  `enabled`, `maxEmissionsPerSession`, `thresholds`, and `bm25f`. Additional
  knobs (maxHits, previewChars, dedupeTtlMinutes, log.maxBytes,
  debug.logRawPrompt) are deferred until v0.9 when they'll be wired.

## v0.7.0 — 2026-04-18

### Breaking

- `scope="project"` on MCP retrieval (`list_rules_by_topic`,
  `read_mnemo_rule`, `get_mnemo_topics`) now returns **local + universal**
  rules. Pass `scope="local-only"` to preserve v0.6.2 "strict local" behaviour.
- Rule-activation index schema bumped to v2. Existing v1 indexes load as
  `None` and are regenerated automatically on the next SessionStart.
- Index top-level keys `enforce_by_project` and `enrich_by_project` are
  removed. Consumers read the unified `rules` table plus the derived
  `by_project` / `universal` lookup tables, or use the new iterators
  `iter_enforce_rules_for_project` / `iter_enrich_rules_for_project`.

### Note on MCP fallback

If MCP is invoked *before* the first SessionStart of v0.7.0 has rebuilt the
index, retrieval falls back to a glob+parse walk of `shared/{feedback,user,reference}/`.
In that fallback path, **universality is not evaluated** — every rule is
treated as local (equivalent to `scope="local-only"`). Running a SessionStart
(or `mnemo extract`) after upgrade is all that's needed to enable the full
v0.7 semantics.

### Added

- Automatic **universal promotion** at `distinct_projects >= 2`
  (configurable via `scoping.universalThreshold`).
- Structured `mnemo://v1` SessionStart injection envelope with per-scope
  topic lines and a `injection.maxTopicsPerScope` cap (default 15).
- `mnemo doctor` reports universal promotion health.
- `shared/project/` pages now carry `runtime: false` to document their
  human-surface-only role.

### Changed

- MCP retrieval now reads the unified index for O(1) lookups; glob+parse is
  kept as a fallback for missing/stale indexes.
- SessionStart rebuilds the index whenever `injection.enabled` is true
  (in addition to the existing enforcement/enrichment triggers).

## v0.6.0 — 2026-04-16 — Loop enabled by default

**Changed**
- Defaults flipped from `false` → `true` for `extraction.auto.enabled`,
  `briefings.enabled`, `injection.enabled`, and `enrichment.enabled`.
  `mnemo init` now produces a working product from session one, instead
  of an inert scaffold awaiting manual JSON configuration.
- `enforcement.enabled` was already `true` since v0.5; unchanged.

**Backward compatibility**
- `_deep_merge` in `core/config.py` preserves explicit `enabled: false`
  values in existing user configs. Users who had opted out of specific
  features continue to see opt-out behavior with no action required.

**Rationale**
- The opt-in pattern shipped since v0.3 ("ship dark, dogfood, then flip")
  imposed JSON-editing friction without safety benefit during solo
  dogfood, and contradicted the project tagline ("the Obsidian that
  populates itself"). Flipping defaults aligns the zero-config experience
  with the product promise.

**Migration**
- Users who wanted the features on: no action needed — defaults now match
  your existing explicit config.
- Users who wanted the features off: add `"enabled": false` blocks to
  `~/mnemo/mnemo.config.json`. See README "Runtime flags".

**Tests**: 779 passing, 2 skipped (opt-in E2E only).

## v0.5.0 — 2026-04-15 — MCP injection (the loop closes)

**Added**
- **MCP stdio server** (`src/mnemo/core/mcp/`): a long-running JSON-RPC 2.0
  process exposing three read-only tools to Claude Code:
  - `list_rules_by_topic(topic)` — returns slugs sorted by source_count desc
    so multi-agent synthesized rules surface first
  - `read_mnemo_rule(slug)` — returns the full body + frontmatter for a slug
  - `get_mnemo_topics()` — returns the union of all topic tags in the vault
  Hand-rolled stdlib-only (no `mcp` SDK dependency, consistent with mnemo's
  `dependencies = []` policy). Both tools apply the v0.4 shared filter from
  `core/filters.py` so machine view and the HOME dashboard stay in lockstep.
  Project pages are excluded by design: they have no topic tags by
  construction and their sources are already in Claude's auto-memory.
- **SessionStart MCP topic injection**: when `injection.enabled=true`, the
  SessionStart hook emits a `hookSpecificOutput.additionalContext` JSON
  envelope listing the vault's topic tags plus a one-line instruction
  telling Claude to call `list_rules_by_topic` + `read_mnemo_rule` BEFORE
  writing code when the task matches a known topic. ~120 tokens per session
  regardless of vault size.
- **`mnemo init` registers the MCP server in `~/.claude.json`** under
  `mcpServers.mnemo`. `mnemo uninstall` removes it. Fully idempotent.
- **New config flag `injection.enabled`** (default `false`, opt-in per the
  v0.3 conservative pattern). Flip to `true` in `~/mnemo/mnemo.config.json`
  to activate after dogfood validates the injection mechanism in your vault.
- **Hidden CLI subcommand `mnemo mcp-server`**: stdio entry point referenced
  from `~/.claude.json`. Not surfaced in `mnemo --help`.

- **Status line integration**: `mnemo init` now wires an additive
  `statusLine` composer into `~/.claude/settings.json`. Output looks like
  `mnemo mcp · 9 topics · 7↓ today` — topic count from your vault plus
  the number of times Claude has consulted mnemo via MCP today (counter
  resets daily, atomic write, lives in `<vault>/.mnemo/mcp-call-counter.json`).
  If you already had a custom statusLine, mnemo **does not overwrite it** —
  the composer wraps your original command and concatenates outputs with
  ` · `. Your original is preserved in `<vault>/.mnemo/statusline-original.json`
  and restored by `mnemo uninstall`. `mnemo doctor` warns if you edit
  settings.json manually and drift away from the composer.

**Internal**
- Injection mechanism de-risked on 2026-04-15 via a throwaway prototype that
  proved `hookSpecificOutput.additionalContext` injects into interactive
  Claude sessions, not just `claude --print` one-shot mode. The prototype is
  removed in this release.

**Tagline status**: "Capture → Present → Inject" is now complete. v0.3
shipped capture, v0.3.1 shipped dense input (briefings), v0.4 shipped
auto-presentation (HOME dashboard + tags), v0.5 ships auto-injection.

## v0.4.0 — 2026-04-14 — HOME dashboard + dimensional tags

**Added**
- **HOME.md dashboard**: `run_extraction` now regenerates a managed block inside
  `HOME.md` at vault root at the end of every run. The block groups consumer-visible
  `shared/` pages by trust tier (cross-agent synthesized first, auto-promoted
  direct reformats second) AND by topic tag. Wikilinks are path-qualified
  (`[[shared/<type>/<slug>]]`). The rest of `HOME.md` is user-owned — mnemo only
  touches content between `<!-- mnemo:dashboard:begin -->` and `<!-- mnemo:dashboard:end -->`.
- **Dimensional tags**: the extraction JSON schema gains a `tags: [topic1, topic2]`
  field. Each prompt builder now receives `vault_root` and injects a per-page-type
  "Existing vault tags" hint into the prompt so the LLM reuses the established
  vocabulary instead of inventing synonyms. Tags persist into frontmatter as a
  unified list alongside the existing system marker (`auto-promoted` /
  `needs-review`).
- **Shared filter module** (`core/filters.py`): single source of truth for
  "consumer-visible" pages — three-condition predicate (path, needs-review tag,
  stability). Both the v0.4 HOME dashboard and the planned v0.5 MCP tools will
  call the same function so human and machine views stay in lockstep. Ships with
  `collect_existing_tags(vault_root, page_type)` for the controlled-vocabulary
  hint and a minimal frontmatter parser for the exact YAML shape mnemo writes.
- **`mnemo doctor` legacy-wiki warning**: surfaces `wiki/sources/` and
  `wiki/compiled/` as orphaned v0.3 directories with a note that the next
  `mnemo extract` will auto-delete them.

**Changed**
- **`dedupe_by_slug` bug fix**: pre-v0.4 dedupe silently dropped both `stability`
  and newly-added `tags` on the floor when merging cross-chunk slug collisions.
  It now preserves `stability` from the chosen cluster and unions `tags` across
  all merged pages.
- **HOME.md template** rewritten: dashboard block skeleton at the top (after
  frontmatter), "Tier 3 — Curated wiki" section removed, `/mnemo promote` and
  `/mnemo compile` removed from quick commands. The user-editable welcome
  content sits below the managed block.
- **README template** drops references to `wiki/sources/`/`wiki/compiled/`;
  documents `HOME.md` as the landing page with an auto-generated dashboard region.

**Removed**
- **`/mnemo promote` and `/mnemo compile` CLI commands** — the manual wiki
  promotion + compilation flow is gone. The dashboard auto-regenerates as a side
  effect of `mnemo extract`, which is a superset. `cmd_promote`, `cmd_compile`,
  `core/wiki.py`, and `tests/unit/test_wiki.py` are deleted entirely.
- **`wiki/sources/` and `wiki/compiled/` directories**: scaffold no longer
  creates them; the first v0.4 `mnemo extract` on an existing vault auto-deletes
  both (and the empty `wiki/` parent if nothing else lives there). Plugin
  manifest (`.claude-plugin/plugin.json`) loses the `promote` and `compile`
  command entries.

**Invariants preserved**
- Zero new runtime dependencies — stdlib only.
- Dashboard failures never abort extraction (wrapped in try/except around an
  `OSError` boundary).
- `shared/<type>/**` remains sacred — extraction writes there, nothing else does.
- Dry-run extraction never touches `HOME.md` or deletes legacy directories.

**See:** `docs/superpowers/plans/` for the full v0.4 plan and
`project_mnemo_v0.4_direction.md` for the "Shared filter specification".

## v0.3.1 — 2026-04-14 — briefings + stability + force-wipe

**Added**
- Per-session briefings module (`core/briefing.py`) generates a structured
  shift-handoff markdown file at every session end, gated on `briefings.enabled`.
- `ExtractedPage.stability: {stable|evolving}` field populated by the LLM and
  persisted into frontmatter; the feedback system prompt teaches the LLM to
  emit `evolving` on hedging language.
- Scanner routes briefings as feedback input so the extraction pipeline mines
  the "Decisions made" and "Dead ends" sections.
- `mnemo extract --force` wipes `shared/_inbox/{feedback,user,reference}/*.md`
  to kill slug-drift duplicates from prior force runs.

**Changed**
- `minNewMemories` default lowered from 5 to 1 — with briefings producing one
  dense file per session, a single new file is enough signal for the background
  auto-spawn.

## v0.2.0 — 2026-04-13 — LLM extraction

**Added**
- `mnemo extract` command: LLM-powered consolidation of mirrored memory files
  into `shared/_inbox/` (cluster types) and `shared/project/` (1:1 promotion).
- Passive hint in `SessionEnd`: when ≥5 new memory files accumulate since the
  last extraction, today's log gets a `🟡 N new memories — run /mnemo extract`
  line (per-day dedup).
- New config section `extraction.*` with sensible defaults for model, chunk
  size, hint threshold, subprocess timeout.
- State file at `~/mnemo/.mnemo/extraction-state.json` tracks source/written
  hashes and per-slug status (`inbox`/`promoted`/`dismissed`/`direct`).

**Changed**
- `shared/` layout mirrors memory types: `shared/feedback/`, `shared/user/`,
  `shared/reference/`, `shared/project/`. The speculative v0.1 taxonomy
  (`people/`, `companies/`, `decisions/`) is deprecated. `shared/people.md`
  from v0.1 is left in place and documented as legacy.
- `core.errors.should_run()` now filters entries with `where` prefixed
  `extract.*` — manual extraction failures never trip the hook circuit
  breaker.

**Invariants preserved**
- Zero new runtime dependencies — stdlib only.
- Cross-platform (Linux / macOS / WSL / best-effort native Windows).
- Hooks never crash the Claude Code session; extraction command may fail
  loudly on stderr.
- `shared/feedback/**`, `shared/user/**`, `shared/reference/**` are sacred —
  the plugin reads them but never writes to them.

**See:** `docs/specs/2026-04-13-mnemo-v0.2-design.md` for the full design.

## [0.1.0] — TBD

### Added
- Hooks-only capture: SessionStart, SessionEnd, UserPromptSubmit, PostToolUse(Write|Edit)
- Three-tier vault: `bots/`, `shared/`, `wiki/`
- Mirror of `~/.claude/projects/*/memory/` to `bots/<agent>/memory/`
- `/mnemo` slash commands: init, status, doctor, open, promote, compile, fix, uninstall, help
- `--yes` non-interactive install for dotfiles
- Cross-platform atomic locks (`os.mkdir`-based)
- Circuit breaker (>10 errors/hour pauses hooks)
- Pure-Python rsync fallback for Windows
