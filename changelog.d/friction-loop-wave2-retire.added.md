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
