"""Autopilot Tier 3 — Rule Proposer.

Two features:
1. End-of-session rule proposer (eos_extractor) — analyzes git signals,
   denial log, and Tier 0 proposals to surface rule candidates.
2. Pre-emptive briefing (preempt) — predicts relevant rules before first
   prompt and caches them for SessionStart injection.
"""
