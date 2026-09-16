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
