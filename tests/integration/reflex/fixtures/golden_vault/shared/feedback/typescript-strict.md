---
name: typescript-strict
description: Enable typescript strict mode in tsconfig for safer types across the codebase
tags:
  - typescript
  - tsconfig
aliases:
  - tipos
  - strict
  - tsconfig
sources:
  - bots/projA/memory/tsconfig-strict.md
  - bots/projB/memory/typescript-strict.md
stability: stable
---
Turn on strict mode in tsconfig for every new typescript project. Strict mode enables noimplicitany, strictnullchecks, strictfunctioncheck, and strictpropertyinitialization, which together catch the most common typescript bugs at compile time. Opting out of strict hides real defects and creates a culture where any-typing is the path of least resistance. If a legacy typescript codebase cannot enable strict mode all at once, enable strictnullchecks first and escalate the rest over a quarter. New tsconfig files should always start strict and stay strict.
