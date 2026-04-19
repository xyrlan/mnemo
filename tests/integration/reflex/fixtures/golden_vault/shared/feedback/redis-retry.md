---
name: redis-retry
description: Use exponential backoff retry with jitter for transient redis connection failures
tags:
  - redis
  - retry
  - resilience
aliases:
  - retry
  - backoff
  - falha
sources:
  - bots/projA/memory/redis-retry.md
  - bots/projB/memory/redis-backoff.md
stability: stable
---
When a redis call fails with a transient connection error, retry with exponential backoff and jitter. A tight retry loop on redis hammers the server while it is recovering from a failover, so add a random jitter on each redis backoff attempt and cap the total retry budget. Only retry idempotent redis commands — a retry on an unguarded incrby can double-count. For non-idempotent redis writes, wrap them in a check-and-set using watch or a lua script before retry. Distinguish redis transient failures from permanent ones: authentication errors should never retry, network timeouts should.
