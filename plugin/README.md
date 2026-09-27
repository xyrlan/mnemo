# mnemo

> Claude Code forgets your corrections. mnemo doesn't — and the sessions it
> fans out for you start out knowing them.

You correct Claude once — *"never use npm in this repo, always yarn"* — and
mnemo turns the correction into a rule in a local Markdown vault. The next
session that touches the subject gets that rule injected before Claude
answers: one or two short lines, only when it clearly applies. A rule that
recurs in two different repos follows you everywhere.

## Install

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
```

The plugin ships a launcher, not a binary: on first use `bin/launch` fetches
the mnemo binary for your platform from the project's GitHub Releases
(checksum-verified) and caches it per version. Restart Claude Code and it is
running.

## Use

- `/mnemo:why` — why a rule was injected on your last prompts, or wasn't.
- `/mnemo:status` — vault state and hook health.
- `/mnemo:doctor` — what is wrong, if something is.
- `/mnemo:learn` — extract rules from your sessions now.
- `/mnemo:help` — everything else.

## Privacy

Local by default. Three switches can make a network call, all off until you
turn them on:

- `autopilot.network.enabled`;
- `recall.rerank.provider`, which sends the query of a `list_rules_by_topic`
  call and the first 800 characters of each rule in that topic to a
  third-party ranking model;
- `reflex.judge.provider`, which sends the first 1,200 characters of the
  prompts you type, plus up to three candidate rules, to the same model. It
  has its own consent, and `mnemo rerank --off` turns it off with the rest.

Nothing else leaves your machine. The vault and every log stay on disk. LLM
calls go through the `claude` CLI you already have, never on the prompt path.
The one other outbound call is the binary download above.
[What is sent, and why](https://github.com/xyrlan/mnemo/blob/master/docs/configuration.md).

## More

- [Full README](https://github.com/xyrlan/mnemo#readme)
- [Getting started](https://github.com/xyrlan/mnemo/blob/master/docs/getting-started.md)
- [Configuration](https://github.com/xyrlan/mnemo/blob/master/docs/configuration.md)
- [Troubleshooting](https://github.com/xyrlan/mnemo/blob/master/docs/troubleshooting.md)
- [Source](https://github.com/xyrlan/mnemo)

MIT licensed — see [LICENSE](LICENSE).
