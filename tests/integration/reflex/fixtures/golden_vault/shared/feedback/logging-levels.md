---
name: logging-levels
description: Use correct logging levels info warn error debug in application logs
tags:
  - logging
  - observability
aliases:
  - log
  - info
  - warn
  - error
sources:
  - bots/projA/memory/logging.md
  - bots/projB/memory/observability.md
stability: stable
---
Pick the right logging level for every log line. Use debug logging for developer-only traces that should never ship to production, info logging for business-level events like a user signup, warn logging for recoverable degradations like a retry succeeding after a transient failure, and error logging only for real application errors that need attention. Misusing error for noisy conditions floods on-call alerts and dulls the signal. Misusing info for every branch bloats observability costs. Keep a single logging convention across services so dashboards and alerts stay consistent.
