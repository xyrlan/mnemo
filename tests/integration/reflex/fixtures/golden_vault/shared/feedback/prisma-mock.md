---
name: prisma-mock
description: Mock prisma client in jest tests with typescript typing
tags:
  - testing
  - prisma
  - orm
aliases:
  - banco
  - database
  - db
  - mocking
sources:
  - bots/projA/memory/prisma-test.md
  - bots/projB/memory/prisma-setup.md
stability: stable
---
When writing jest tests that touch prisma, mock the prisma client rather than hitting a real database. Use jest-mock-extended to build a typed mock of the PrismaClient and inject it into your service. This keeps jest tests fast and hermetic, and the typescript compiler still validates every prisma call site. A common pattern is to put the prisma mock in a shared test helper, expose it as a jest global, and reset the mock between tests. Avoid fake-prisma libraries that only mock part of the schema — a full jest mock of prisma keeps the contract honest and prevents database drift in your test suite.
