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
  72 (55%) held and 58 (45%) kept. None were 14+ days old yet. 13 calls,
  $0.95. The verdict is not the truth: on the audit's held-out half the same
  judge staged 30 of 31 junk pages and 3 of 41 good ones.
