- **The SessionStart briefing now goes through a selection step, and it still
  picks the newest on purpose.** `briefing_select.pick(vault, project, query=)`
  ranks the last 10 briefings with reflex's BM25 scorer and gates, and keeps
  the newest unless a query clearly names an older one. The hook has no query
  to give it: it runs before the first prompt is written, and the one task
  signal it does have, the checked-out branch, picked the best briefing 5
  times in 20 on mnemo's real briefings, the same as newest-wins
  (`tools/measure_briefing_query.py`). The hook also records which briefing it
  injected, through `record_briefing_read`. (channels/briefing-query)
