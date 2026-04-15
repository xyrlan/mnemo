"""mnemo MCP package — v0.5 injection layer.

Exposes mnemo's Tier 2 pages to Claude Code via the Model Context Protocol.
``tools.py`` holds the pure read functions; ``server.py`` wraps them in a
stdlib-only JSON-RPC stdio loop.
"""
