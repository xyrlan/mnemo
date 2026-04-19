---
name: graphql-cache
description: Use normalized graphql cache with stable ids for every graphql entity
tags:
  - graphql
  - cache
aliases:
  - cache
  - normalizacao
  - normalized
sources:
  - bots/projA/memory/graphql-cache.md
  - bots/projB/memory/apollo-cache.md
stability: stable
---
A graphql cache must normalize entities by a stable id or the cache will duplicate the same record across every graphql query. Apollo and urql both support normalized cache configurations keyed on a typename plus id pair. Return the id field on every graphql type or the cache falls back to path-based keys and you lose the main benefit: one graphql mutation updating every screen that already rendered the entity. Avoid mixing normalized cache with hand-rolled graphql field policies unless the cache behaviour is documented, because cache merges on paginated graphql lists surprise every new contributor.
