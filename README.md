# Helix Watcher

Reads finished Express sessions and works out what should be written back into Nexla's
context corpus, so the next session in that organization goes better.

Two parts, deliberately separate:

- **Filter** — one model call per session, no tools. Answers only *does this session contain
  anything worth writing?* Cheap enough to run on everything.
- **Extractor** — an agentic loop with eleven read-only tools. Answers *what exactly, and
  where*, with the target pages open and other sessions searchable.

A third stage, **synthesis**, merges an organization's findings across sessions and resolves
values that disagree. Extraction is per session, synthesis is per organization, resolution
settles conflicts. See `docs/design.md`.

They are split because the questions need different material. The filter sees a compact
transcript and the corpus *index*; deciding whether to look does not need page contents. The
extractor needs the pages themselves — `relation` and `page_quotes` are unanswerable from an
index, and telling a convention from a one-off means looking at other sessions.

## Layout

```
harness/
  common/schema.py     the filter's output contract — 7 categories, flat
  common/finding.py    the extractor's contract — findings, plus the rules a
                       JSON schema cannot express
  common/material.py   loading and shaping what a reviewer sees
  filter/              prompt.md, run.py, score.py, compare.py
  extract/             prompt.md, run.py, tools.py, verify.py, judge.py
  apply.py             write findings into a working copy of a corpus
  loop/                does a written finding change the next session?
  dump.py              print one session, raw or as the filter sees it

docs/
  report.md            what was measured, and what it showed
  design.md            the stages, and what each one can answer
```

## The eval data is held privately

`evals/` is not in this repository. The labels, the findings and the probe set are built from
real customer sessions — transcript excerpts, credential ids, account and property ids — so
they live in a private repo alongside it. `harness/loop/probes.json` ships with those
identifiers replaced by placeholders, which is enough to read the method and not enough to
run it.

That means the scripts below need `evals/` and a session database to execute. The code, the
contracts and the prompts are all here; the material they run on is not.

## Running it

Scripts carry their dependencies inline (PEP 723), so `uv run` needs no environment.

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY
set -a && . ./.env && set +a

uv run harness/filter/run.py --labelled --model claude-opus-5 --effort high --tag run1
uv run harness/filter/score.py run1
uv run harness/filter/compare.py            # one row per model and effort level

uv run harness/extract/run.py --labelled --tag run1
```

## Where express-code goes

`express-code` is a separate repository and is not vendored here. It supplies the seed
corpora, the shared connector guides and the corpus contract that findings are judged
against. Clone it **beside** this repo:

```
Projects/
├── Helix-Watcher/     this repo
└── express-code/      the product repo
```

`material.py` resolves it in that order: `EXPRESS_CODE_PATH` if set, then a sibling
directory, then a nested `./express-code`. Set the variable if yours lives anywhere else.

## Reading the numbers

`docs/report.md` has them. Two things to keep in mind when reading any of it:

- **Run-to-run variance is material.** Identical model, prompt and material can differ by
  about 0.06 F1 on the headline. Compare conditions with several runs a side.
- **The extractor's reference set is a coverage target.** It is the union of findings
  confirmed to exist, part hand-labelled and part confirmed from runs, so recall against it
  measures coverage. Precision still means something, because the judge rules against the
  transcript.
