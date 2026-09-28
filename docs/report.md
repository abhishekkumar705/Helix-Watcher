# Helix Watcher — what we built and what it does

Helix Watcher reads finished Express sessions and works out what should be written back
into an organization's context corpus, so the next session in that organization goes better.

This report covers the agent architecture: what exists, what it was measured against, and
what it changed.

---

## The short version

We built two agents that run one after the other.

The **filter** reads a session cheaply and answers one question: *is there anything in here
worth writing down?* It costs about 9 cents a session, so it can run on everything.

The **extractor** takes the sessions the filter ranked highest and answers the harder
question: *what exactly, and which page does it go on?* It has eleven read-only tools —
it can open corpus pages, search other sessions, read the corpus contract — and costs
about $2.48 a session.

We tested them against 36 hand-labelled sessions and 60 hand-verified findings. Then we did
the test that actually matters: we wrote the findings into a copy of the corpus, put a fresh
agent in front of eight decision points where a real session had gone wrong, and checked
whether the new pages changed its answer.

**They did. 11 of 24 runs got it right with the old corpus. 21 of 24 with the new one.**

---

## How the two stages split the work

They're separate because they need different material in front of them.

The filter sees a compacted transcript and a *list* of corpus page names. That's enough to
judge whether a session is worth a closer look. It doesn't need the page contents.

The extractor needs the pages themselves. To say "this refines a line that's already there"
or "this contradicts the page," you have to have read the line. And to tell an organization's
settled convention from a one-off accident, you have to look at other sessions.

If we merged them, every session would pay the expensive price. Keeping them apart means the
filter decides where the extractor's budget goes.

---

## Does the filter pick the right sessions?

The filter scores each session on seven things — is there a system here with no page yet, is
there something new about a system that does have one, was a value frozen into a tool and
recorded nowhere, and so on — plus one overall score.

The useful test: sort sessions by its score, work down the list, and see how fast we collect
the real facts.

| If we extract from the top... | we get |
|---|---|
| 5 sessions | 20 of 61 facts (33%) |
| 10 sessions | 35 of 61 facts (57%) |
| **14 sessions (half)** | **48 of 61 facts (79%)** |
| 20 sessions | 58 of 61 facts (95%) |
| all 28 | 61 of 61 (100%) |

Half the sessions give you four-fifths of the value. The bottom ten sessions hold six facts
between them.

The separation at the bottom is clean. Every session where nothing happened scores **0.06 or
below**. Every session with real content scores **0.08 or above**. Nothing is mixed up in the
middle.

*(In statistics terms this is a rank correlation of 0.80 between the filter's score and the
number of facts actually found. The table above is the same fact, in a form you can budget
against.)*

### What it's good and bad at

| What it's looking for | Works? |
|---|---|
| A system with no page yet | Yes — catches 88% of them |
| A value frozen into a tool and recorded nowhere | Yes |
| The user told the agent something it didn't know | Yes |
| Something new about a system that has a page | Finds them, but under-confident |
| This session contradicts what a page says | **No** |

The last row is the honest failure. To know a session contradicts a written line, you need
the line, and the filter is given page names only. The signal is faintly there
in the ordering, but nothing ever crosses the threshold. That check belongs to the extractor,
which has the pages open.

### On models

We ran Opus, Sonnet and Haiku. Opus is clearly best. But the interesting result is *how*
Haiku fails: it ranks sessions nearly as well as Opus, it just reports every probability far
too low to cross any threshold. If we only ever use the filter as a ranker — which is what
the table above argues for — Haiku at **39 cents for all 36 sessions** is worth a second look.

One caution on that comparison: we ran the same Opus configuration twice and got 0.81 and
0.71 on the headline number. Run-to-run noise is real, every model comparison here is a
single run, and most of the gap between similar rows is noise.

---

## Does the extractor find the right things?

We hand-built a set of findings we were confident were in these 18 sessions, then had a
separate model grade the run against it. The judge weighed each finding against the
transcript itself, so a finding could be right even where our list was wrong.

**Of 35 findings we knew were there, the run found 24. That's 69%.**

| session | found | of |
|---|---|---|
| 33360fc7 | 5 | 5 |
| 0d365415 | 3 | 6 |
| 1a9cd1f5 | 3 | 4 |
| 2f0f57ce | 3 | 3 |
| 0208d2da | 2 | 4 |
| e6d8771b | 2 | 4 |
| f816aac3 | 2 | 3 |
| 0c3d2227 | 1 | 1 |
| 40a8caa2 | 1 | 2 |
| 4539cd25 | 1 | 1 |
| 67436d0a | 1 | 1 |
| 11301cae | 0 | 1 |

Of the 24 it found, 12 were spot on, 10 were the right finding aimed at a slightly different
section of the same page, and 2 were thin.

**The 11 it missed cluster in one place.** Five of them are the same defect — a reporting tool
built with absolute start and end dates frozen into it, in five separate sessions. Three more
are tool descriptions that claim something the tool cannot do. Every one is something the
*agent did*, visible in what it sent to a tool. The extractor reads what came back from tools
far more carefully than what was sent to them, and closing that gap is the clearest single
improvement available.

**Setting aside the extras, 24 of the 37 findings it reported landed on something we already
knew was real — 65%.** The other 13 break down as 9 where it asserted something it never
quoted, and 4 that were out of scope. The 9 are all the same shape: a confident claim that
the session "contradicts the LinkedIn redaction rule," with no quote of the rule. It reached
for that phrase in four separate sessions. Our structural checks caught every one, because a
finding claiming a contradiction is required to quote the page it contradicts, but the model
keeps trying, so the fix belongs in the prompt.

*(For completeness: the run also produced 36 findings that were real and that our hand-built
list had simply missed. We've excluded them from every number above, because grading a run
against gaps it found itself proves nothing. They were verified and folded into the reference
set for future runs.)*

### The safety rule

This is enforced in code:

- What the **user said** can overwrite or delete a line.
- What a **system returned** can overwrite a line, but never delete one.
- What the **agent concluded** can only add.

So a confident wrong inference can add noise to a page. It cannot destroy something a human
put there. Alongside this we check that any finding claiming a contradiction quotes the line
it contradicts, that every quote names which session it came from, and that an insert names
the line it goes after.

**Across 60 findings, zero violated any of these.**

### Every quote was checked by machine

All 149 supporting quotes were located in the raw transcripts by string search. 149 of 149
found, at the exact message claimed.

This check caught four fabricated message numbers in our *own* hand-built reference set.
A quote you can't locate is worth nothing.

---

## The part that actually matters: does any of it help?

Everything above is a model agreeing with a label. The real question is whether writing these
findings into the corpus changes what a later agent actually does.

We applied the findings to a copy of each organization's corpus, then took eight decision
points where a real session had gone wrong and asked each one twice — once with the old page
in context, once with the new one. Three runs each way, graded by a third model.

```
probe                    old   new
li-facet-search          0/3   0/3
gaql-equality            3/3   3/3
li-report-dates          3/3   3/3
hubspot-deal-count       0/3   3/3
buyer-profile            2/3   3/3
reddit-paging            3/3   3/3
ga4-property             0/3   3/3
notion-campaign-ids      0/3   3/3
TOTAL                   11/24  21/24
```

**46% → 88%.** Three questions went from always-wrong to always-right, a fourth improved.

Getting there: 41 of the 45 corpus-bound findings were placed automatically. One named a line
that wasn't on the page, and we report that instead of guessing. The other 16 are defects in
tool descriptions and tool source configs, which have their own write paths.
