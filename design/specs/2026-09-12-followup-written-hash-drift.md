# Bulk migrations rewrite rule files without reconciling written_hash

`core/migrations/slugs.py:112` stamps a `slug:` into every live page via
`atomic_write_bytes` and never touches `entry.written_hash`. On the real vault
that left 1,638 of 1,762 state entries hash-drifted (marker
`.mnemo/slugs-stamped.v1` dated 2026-09-02 20:23; 1234 live pages share that
mtime and 1234/1234 carry `slug:`).

Consequence: every one of those rules reads as "user edited" to
`extract/inbox/branches/*`, which is the condition that stages a `.proposed.md`.
It is inert *today* only because those rules' sources are no longer scanned —
`scanner.scan()` walks source files, and a rule whose memory file is gone never
becomes dirty. Any future run that re-scans one of those sources will stage a
rewrite for a rule nobody edited.

Fix belongs at the migration site: a bulk rewriter that owns the new bytes should
advance `written_hash` to `content_hash(new_text)` for the entry it just rewrote,
the way `reclassify_apply.py:247` already does.

Found while implementing #159, which fixes the same class of bug at the accept
path. Out of scope there on purpose.
