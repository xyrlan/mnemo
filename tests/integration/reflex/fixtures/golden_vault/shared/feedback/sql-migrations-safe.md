---
name: sql-migrations-safe
description: Write backward-compatible sql migrations using expand-contract with alembic
tags:
  - database
  - migrations
  - sql
aliases:
  - migracao
  - alembic
  - migration
sources:
  - bots/projA/memory/sql-migration.md
  - bots/projB/memory/alembic-flow.md
stability: stable
---
Every production sql migration must be backward compatible with the previous application version. Use the expand-contract pattern: in migration one, add the new column or table non-destructively; in migration two, deploy the application that uses it; in migration three, drop the old column only once every replica has cut over. With alembic this means splitting a rename into add, dual-write, backfill, switch-read, drop. A destructive sql migration that runs before the new code deploys will crash every running replica. Always guard alembic migrations with online-safe patterns: no long locks, no blocking index builds on hot tables.
