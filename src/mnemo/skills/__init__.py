"""Skills mnemo ships to Claude Code, as package data.

One directory per skill, each holding a ``SKILL.md``. This is the source of
truth: ``tools/sync_plugin_manifest.py`` copies every skill to the repo-root
``skills/`` directory the plugin loads by convention, and ``mnemo init``
writes the same file under ``~/.claude/skills/`` (or ``<cwd>/.claude/skills/``)
for installs that never go through the plugin — see
``install.settings.inject_skills``. Two copies because a wheel can only carry
files inside the package and the plugin loader can only see the repo root;
``test_plugin_manifest`` pins them equal.
"""
