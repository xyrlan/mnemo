"""Cold-start backfill: synthesise memory files from historical transcripts.

New vaults inject nothing until enough sessions accrue to extract from. This
package converts the Claude Code session transcripts already on disk at
``~/.claude/projects/<slug>/*.jsonl`` into ``bots/<repo>/memory/*.md`` files —
the seam the existing extraction pipeline already reads from.

See ``docs/superpowers/specs/2026-08-01-cold-start-backfill-design.md``.
"""
