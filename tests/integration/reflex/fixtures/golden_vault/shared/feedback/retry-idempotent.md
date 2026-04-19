---
name: retry-idempotent
description: Only retry idempotent operations automatically to avoid duplicate side effects
tags:
  - retry
  - idempotency
  - distributed
aliases:
  - idempotent
  - idempotency
sources:
  - bots/projA/memory/retry-idempotent.md
  - bots/projB/memory/idempotency.md
stability: stable
---
Automatic retry is only safe on idempotent operations. A retry on a non-idempotent request like "create a payment" will double-charge the customer the moment the network hiccups. Mark every endpoint idempotent or not in its contract, and teach retry middleware to read that flag. For operations that must be retried but are not naturally idempotent, require an idempotency key from the caller and deduplicate on the server. Never retry transparently across a gateway without an idempotency story; every operator has a duplicate-side-effect outage story from ignoring this.
