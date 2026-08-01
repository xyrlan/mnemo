# Browsing the vault in Obsidian

Entirely optional. The vault is plain Markdown — any editor with graph support
reads it as is, and mnemo works identically with no editor at all. Obsidian is
**not** a dependency.

If you point [Obsidian](https://obsidian.md/) at `~/mnemo/`:

- rules link to the briefings they were extracted from,
- briefings link back to the rules they spawned,
- the **Graph view** renders the rule↔briefing network out of the box.

Run `mnemo regen-graph-edges` once to refresh the wikilink sections on rules
and briefings that already exist (the extractor emits them automatically for
new ones). The section is bookended by an HTML comment marker so it stays
invisible to mnemo's retrieval — zero impact on Claude's context, zero impact
on BM25F scoring.

## Colouring the graph

For a more readable graph, open Graph view → settings → **Groups** and add the
following. Order matters — first match wins.

| Query                              | Suggested color |
|------------------------------------|-----------------|
| `path:shared/feedback`             | green           |
| `path:shared/user`                 | yellow          |
| `path:shared/reference`            | purple          |
| `path:shared/_inbox`               | orange          |
| `path:briefings/sessions`          | blue (hubs)     |
| `path:memory`                      | cyan            |
| `file:HOME`                        | red (dashboards)|
| `path:bots`                        | light gray      |

These groups live in your local `.obsidian/` folder and never leave it.
