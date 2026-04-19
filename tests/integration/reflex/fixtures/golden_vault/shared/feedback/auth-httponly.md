---
name: auth-httponly
description: Use httponly secure cookie for session auth tokens instead of localstorage
tags:
  - auth
  - security
  - cookie
aliases:
  - cookie
  - sessao
  - autenticacao
sources:
  - bots/projA/memory/auth-cookie.md
  - bots/projB/memory/auth-session.md
stability: stable
---
Store session auth tokens in an httponly secure cookie, not in localstorage. A cookie with httponly and secure flags is unreachable from javascript, which defeats the most common xss token-exfiltration attack against auth flows. Set samesite=lax or samesite=strict to harden csrf on the same auth cookie. Localstorage tokens are readable by any script on the page, so a single xss bug leaks the entire session. An httponly cookie plus a csrf token pattern is the standard web auth shape; do not roll a custom bearer scheme just to avoid cookie handling.
