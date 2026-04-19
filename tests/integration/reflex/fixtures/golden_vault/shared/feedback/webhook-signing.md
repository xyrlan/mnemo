---
name: webhook-signing
description: Verify incoming webhook payloads with hmac signature before trusting the webhook body
tags:
  - webhook
  - security
  - hmac
aliases:
  - webhook
  - hmac
  - signature
sources:
  - bots/projA/memory/webhook-sign.md
  - bots/projB/memory/webhook-hmac.md
stability: stable
---
Always verify an incoming webhook with an hmac signature before parsing the webhook body. The provider sends a signature header computed from the raw webhook body and a shared secret; recompute the hmac on your side using the raw bytes, and compare with a constant-time equality to defeat timing attacks. Do not parse json before verifying the webhook signature — json normalisation can change the bytes and break the hmac. A webhook that is not signature-checked lets any attacker drive your workflow by posting a forged webhook to the public endpoint.
