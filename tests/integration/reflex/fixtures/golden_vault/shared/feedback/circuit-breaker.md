---
name: circuit-breaker
description: Wrap downstream calls in a circuit breaker to prevent cascading failure
tags:
  - resilience
  - circuit-breaker
aliases:
  - breaker
  - cascading
sources:
  - bots/projA/memory/circuit-breaker.md
  - bots/projB/memory/resilience.md
stability: stable
---
Protect every remote downstream dependency with a circuit breaker. The circuit breaker opens when the downstream failure rate crosses a threshold, short-circuits outgoing calls for a cool-down window, and half-opens to probe recovery. Without a circuit breaker, a slow downstream causes every request thread to pile up on it, and the slowness cascades into your own service until it falls over. Tune the circuit breaker thresholds per dependency — a chatty cache has a different failure profile than a payments gateway. Pair the circuit breaker with a sensible timeout; a circuit breaker around an infinite wait does not help.
