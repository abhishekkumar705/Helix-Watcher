# Does writing to the corpus help?

Every session in the eval ran without org context, or read it and was not helped by it. Until
a session goes better because of something the watcher wrote, the rest of the pipeline is
unjustified.

`probes.json` holds decision points where a real session went wrong, each stated as the agent
had it, with what actually happened and what would count as avoiding it. `run.py` asks the
same question twice — once with the seed corpus page, once with the page the findings
produced — and a third call grades whether the answer avoids the failure. Both arms see a
page, so the comparison is content against content.

```bash
uv run harness/apply.py --out /tmp/corpus-improved
uv run harness/loop/run.py --improved /tmp/corpus-improved --n 3
```

The probes are constructed, not live: a model answering with a page in context is not an
agent in a sandbox with tools. It is the closest test available without the product harness.
