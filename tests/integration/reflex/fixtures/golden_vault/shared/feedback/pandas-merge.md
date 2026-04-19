---
name: pandas-merge
description: Prefer pandas merge on explicit keys over dataframe join for readable joins
tags:
  - pandas
  - python
  - dataframe
aliases:
  - dataframe
  - join
  - merge
sources:
  - bots/projA/memory/pandas-join.md
  - bots/projB/memory/pandas-merge.md
stability: stable
---
Use pandas merge with an explicit on or left_on plus right_on list when joining two dataframes. Merge makes the join keys obvious at the call site, which keeps dataframe pipelines readable weeks after they were written. Dataframe.join is terser but silently falls back to index-based joining, which hides bugs when one dataframe has a stale index. Always pass how explicitly so the merge is visibly a left, inner, or outer join. Validate the merge with a shape assertion afterwards so a many-to-many accident does not multiply the dataframe silently in pandas.
