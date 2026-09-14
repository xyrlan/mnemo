# Why 3 of 108 carried rules are correction-backed (#244)

Measured on the maintainer's vault on 2026-09-13/14, before changing anything.
Every number below comes from a script run against `~/mnemo` and the
transcripts under `~/.claude/projects`; nothing was inferred from fixtures.

## The vault's shape

| where | pages | of which |
|---|---|---|
| `shared/feedback/` | 81 | 80 `verified` with a quote, 1 without |
| `shared/reference/` | 1456 | 1320 `demoted_from: feedback` (the 2026-09-02 `reclassify --apply`), 136 native |
| `shared/_inbox/reference/` | 102 | 100 demoted since 2026-09-02, 2 native |

Briefings: 355. Only the 59 written in September carry a `## Corrections`
section at all (34 of 59 non-empty), holding **64 items** in total, 47 of
which pass `quote_is_specific`. That is the entire supply of quotable
corrections the extractor has ever had.

## 1. Why feedback pages fail the gate since 2026-09-02

100 feedback pages emitted since the Corrections section existed were
demoted. For each, the `## Corrections` of its own source briefings were
read back:

| class | pages | meaning |
|---|---|---|
| sources have Corrections, **none related to the rule** | 34 | e.g. `tdd-mandatory-before-code` cites a briefing whose only correction is about deploy backups |
| sources have no Corrections section | 28 | nothing to cite |
| sources have Corrections, none specific enough | 20 | one approval-grade item ("pode ir implementando e voce mesmo faz o merge") |
| no briefing among the sources (memory files only) | 15 | nothing to cite |
| source briefing missing on disk | 3 | |

Failure classes the issue asked about — "quote reworded", "quote absent",
"correction in the transcript but not in the briefing" — do not occur: in
**100 of 100** cases there was no correction supporting the rule in the
first place. These pages are *Decisions made* content the extractor typed
as `feedback`. Demotion is the right verdict every time; the gate is not
the problem. In the same window it verified 21 pages, so the extractor's
feedback emissions run ~85% unbacked despite the prompt telling it to emit
`reference` when no quote supports the rule.

## 2. What the "verified" label actually means today

Of the 80 `verified` feedback pages, re-checked against today's gate:

| provenance | pages | what the quote is |
|---|---|---|
| passes today's gate (quote in a source briefing's Corrections, ≥5 content words) | 19 | see below |
| `mnemo reclassify` keep verdicts, verified against raw transcript turns; `evidence.source` is prose so the gate cannot re-check them | 53 | feature requests, bug reports, approvals |
| verified before #119 added the content-word bar | 7 | "implementa os fixes", "ja exclui as acoes de compra", "pode usar o dryrun bypass" |
| quote present but never found | 1 | |

The 61 reclassify keep verdicts were graded by hand against the definition
the prompt states (the user telling the assistant to stop, change, prefer
or never/always do something about *how to work*): **3–4 of 61** qualify.
The LLM's own `link` sentences say what the rest are: "User explicitly
requested feature", "User reported bug", "User approved". Reclassify's keep
precision as a correction detector is ~5%; as a "the user typed a related
sentence" detector it is ~100%. Its demote verdicts were not re-graded here
(#181, closed).

Of the 19 that pass today's gate, **6 quote only the session's first user
turn**. Four of those six are mnemo's own dispatch template quoted back as
the user's words: `liveness-from-real-pids-not-mtime` cites "Choosing is
part of your job…", `measurement-before-design` cites "Do not design a
ranking mechanism…", `test-guard-reachability-not-grep` cites "Add a
regression test that fails if…". Across all 64 Corrections items, **23
quote only the opening turn**, 21 of them in dispatched children whose
sole user turn is the dispatch prompt ("Do NOT merge or push without
asking.", "Write commits and any PR in English.", "Commit on branch
fix/issue-196."). The `→ rule` half of most items narrates what was then
done ("Not merged, not pushed.", "Committed as a4949e8.").

## 3. What the extractor emits as `reference` that is a correction

108 native (not demoted) reference pages were extracted since 2026-09-01.
Read one by one, **4** are corrections a human would call corrections, all
from one saints-network session: writing style for client copy, plain-text
lists for WhatsApp, translating jargon for non-technical stakeholders, no
presential validation. All four were emitted as `type: reference` **with**
an `evidence.quote` that verifies at exactly the bar feedback pages are held
to (3 of 4; the fourth, "remova a barra no inicio das linha", has 4 content
words). `verify_page` only looked at `feedback` pages, so they stayed
`inferred` and count as unbacked. The other 104 are knowledge; `reference`
is right. Over-typing runs the other way: 100 feedback emissions that
should have been reference, against 4 reference emissions that should have
been feedback.

## 4. Why 78 verified rules carry 3 times

62 of the 81 verified feedback rules never fire in the replay in any bucket.
They are episodic feature decisions — `prayer-wall-badge-active-count`,
`referral-earnings-table-remove-annual-recorrente`, `wallet-balance-sort-
order` — with a request-grade quote attached. No gate change makes those
carry: they are not rules. The three that did carry:

| slug | quote it cites | prompt it fired on |
|---|---|---|
| `move-destructive-actions-to-dropdown` | "pode seguir com o plano de implementacao e pode seguir suas recomendacoes" | "vamos pode seguir com suas recomendados, seguir com o plano e implementacao" |
| `environmental-protection-layers-comment-not-delete` | "nos vamos comentar todo esse grupo de camadas por enquanto…" | |
| `migrate-hand-before-auto-deploy` | "nos iamos implementar esse plano porem eu quero que voce revise o plano…" | |

The first is an approval prompt matching an approval quote through the
BM25 evidence field, with no rule content in common. The KPI's 3 was
already generous.

## What changed

1. **`corrections.verify` ignores the opening turn.** A correction reacts to
   something the assistant did; the first user turn is the task, and in a
   dispatched child it is mnemo's own text. Removes 23 of the 64 existing
   items had it been in place; a quote the user repeats later is kept.
2. **`evidence.verify_page` is symmetric.** A `reference` page whose quote
   verifies at the feedback bar becomes verified `feedback`; `user` and
   `project` pages are untouched. Recovers the 3 real corrections above.
   A re-emitted slug that already lives under `reference` goes through the
   existing cross-type staging, never an overwrite.
3. **The briefing prompt says what a correction is not** — the opening
   message, approvals, questions, feature requests, bug reports, status
   updates — and that the `→` half is an imperative rule, not what was done.

Not changed: thresholds, the 1320 demoted pages, the 60 reclassify-era
labels, the replay's definition of `correction_backed`.

## Replay, before and after, same 2100 prompts

Both gates applied to today's pages on a copy of the vault (3 reference
pages retyped to verified feedback; 6 verified pages whose quote is the
opening turn demoted and staged, as extraction would now do). Replayed in
one process against one frozen prompt list.

```
                                              before      after
vault rules                                     1820       1814
vault correction_backed                           78         75
prompts.fired                                    226        225
prompts.carried                                  117        117
prompts.carried_correction_backed                  3          2
prompts.hindsight                                 24         23
prompts.not_yet_learned                           85         85
rules.carried_distinct                           106        104
rules.carried_correction_backed_distinct           3          2
```

None of the 9 touched pages fired in either run. The 3→2 is
`move-destructive-actions-to-dropdown` on the approval prompt above: its
score moved 4.959 → 4.680 because the evidence field's average length
shifted (0.99 → 0.92 tokens) when 9 evidence-bearing pages moved, and it
fell under the 1.5 relative gap against the runner-up at 3.168. Hindsight
and not-yet-learned did not move up. The number the issue asked to move up
went down by one, and the one it lost was never a correction.

## Re-briefing 5 real sessions with the new prompt (haiku, $0.75)

| session | old items | new proposed | kept after `verify` | what was dropped |
|---|---|---|---|---|
| mnemo dispatched child, 1 user turn | 5 | 1 | 0 | all five were dispatch boilerplate; the one proposal quoted the brief |
| clearframe, 9 turns | 4 | 4 | 4 | unchanged — spec questions the model still reads as corrections |
| clubinho, 24 turns | 3 | 1 | 0 | request, approval, status update |
| sg-imports, 5 turns | 3 | 1 | 1 | "faca em uma worktree" (real, 4 content words), an instruction; kept an approval |
| meunu, 26 turns | 1 | 2 | 0 | bug report; the two proposals quoted assistant text |

16 → 5 items, 0 corrections gained because these sessions contained none.
n=5, stochastic; a direction, not a rate.

## What this says about the issue's premise

"Extraction produces reference pages, not corrections" is half right. It
produces *feedback* pages at 5× the rate corrections exist, and the gate
demotes them correctly. The debt is not in the gate or the type the
extractor picks; it is that genuine textual corrections are rare (~15 in
355 sessions by hand count) and the label that counts them has been
written by three different bars. Moving "carried, correction-backed" up
honestly means either (a) the user correcting Claude in text more often, or
(b) a wider definition of "your own words" that the replay would then have
to state — both are the maintainer's call, not a prompt edit.
