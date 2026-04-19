"""Apply-time dispatch branches for the extraction inbox.

Three modules — one per top-level apply branch:

- ``auto_promoted``: pages whose target lives in ``shared/<type>/`` (the
  sacred dir). Single-source pages auto-promote to this branch.
- ``inbox_flow``: pages whose target lives in ``shared/_inbox/<type>/``
  (the v0.2 review dir). Multi-source pages route here.
- ``upgrade``: re-emission of an already-auto_promoted page with a new
  multi-source set. Stages a sibling ``.proposed.md`` for human review.

Split out of the pre-v0.9 ``inbox.py`` monolith in PR I so each branch
can grow independently without bloating one file.
"""
