# Helix Watcher — design

Reads finished Express sessions, works out what should be written back into an organization's
context corpus, so the next session goes better.

Measurements are in `docs/report.md`.

---

## The shape

```
                    every finished session
                              │
                      ┌───────▼────────┐
                      │  1. FILTER     │  agentic, session tools only
                      │  ranks sessions│  how much is here, where to look
                      └───────┬────────┘
                              │  highest rank first
                      ┌───────▼────────┐
                      │  2. EXTRACT    │  agentic, tools + contract
                      │  per session   │  findings, each with cited evidence
                      └───────┬────────┘
                              │
                      ┌───────▼────────┐
                      │  3. SYNTHESISE │  per org, across sessions
                      │  merge, resolve│  promote, reconcile
                      └───────┬────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       org context corpus            tool descriptions and
       (writes, automatic)           source configs (automatic)
              │
              │  every write, every edit, every deletion
              ▼
      ┌───────────────┐
      │  5. MONITOR   │  periodic, per org
      │  what survived│  survival, churn, who changed it
      └───────┬───────┘
              │
              └──────────▶  tunes the filter's ranking and
                            what extraction is allowed to emit
```

Each stage exists because it sees something the one before it can't:

- **Filter** — a slice of the session, the whole session on request, and a list of page
  names. Enough to judge whether a session is worth a closer look.
- **Extraction** — the pages themselves, plus other sessions. Needed to say *this refines an
  existing line*, or *many sessions have settled on this*.
- **Synthesis** — an organization's accumulated findings. The only place duplicates and
  disagreements are visible.
- **Monitor** — what happened to the lines after they were written. The only place the
  pipeline finds out whether it was right.

---

## Stage 1 — Filter

An agentic loop, like extraction, but over the session alone. It opens on a compacted slice
of the transcript plus a list of what the organization already has, and fetches the rest of
the session when the slice is not enough.

| tool | what it gives |
|---|---|
| `transcript` | a range of messages from this session, in full |
| `transcript_search` | messages in this session matching a term |
| `corpus_index` | every page in this org's corpus, with descriptions |

**It gets session tools and no corpus tools.** Reading pages is what stage 2 is for and is
the expensive half; handing the filter `corpus_read` would collapse the two stages into one.
The index is enough to know whether a system has a page. It is not enough to know what that
page says, and that boundary is the whole reason the stages are separate.

- **Produces a ranking.** Almost every session with real work has something worth writing, so
  "is there anything here?" is nearly always yes. Output is *how much*, plus where to look.
- **Only asks what its material can answer.** It can suspect a session contradicts a written
  line; it can't confirm one without the line. That suspicion travels to the extractor as a
  hint for it to verify.
- **One real gate: disqualifiers.** Zero-activity sessions and demo-data sessions are
  genuinely empty and detectable outright. The only binary decision worth making here.
- **Categories name destinations.** Each says where a finding would land. Worded from the
  corpus contract so the two can't drift apart.

| category | what it means |
|---|---|
| `new_connector` | A system used here has no page yet. One page per system, never per flow; a generic transport is not a system when the endpoint or business vocabulary names one |
| `existing_connector_update` | Something new about a system that has a page — its objects, triggers, read and query behaviour, write affordances, operating constraints. A limitation counts when it changes a decision |
| `value_convergence` | An operational value — page size, date window, account or property id — frozen into a built tool and recorded nowhere, which the org has settled on |
| `contradicts_written` | The session contradicts a line that already exists in the org or shared corpus |
| `concept` | Meaning unrecoverable from one system: an org policy, a revealed language binding, a cross-system identity or join rule, a trust parameter |
| `tool_proposal` | One bounded capability worth building — returns a decision-ready result, applies org-specific rules, has an explicit read, write or approval boundary |
| `user_supplied_knowledge` | The user stated a fact, a rule or a correction the agent did not have |

`contradicts_written` is the one the filter cannot settle on its own, for the reason above.

## Stage 2 — Extraction

### How the loop runs

The model is given a slice and a map, then fetches the rest itself.

- **The opening turn** carries the session's facts — org, user, message count, connectors
  touched, tools built, errors — and the **first 40 messages** rendered in full, with tool
  outputs and the agent's reasoning both intact. Some connector limits are stated only in
  reasoning, and everything the user said arrives as tool output, because the agent asks
  questions through a tool.
- **The rest is not handed over.** A session of 350 messages arrives as 40 plus a line saying
  how many remain and which tool reads them. That is deliberate: `relation` and `page_quotes`
  require reading the target page, and telling a convention from a one-off requires reading
  other sessions. Neither is answerable from a prompt, however much is stuffed into it.
- **Up to 22 iterations**, with a stated budget of about twenty tool calls. The loop resends
  the whole conversation each turn, so cost grows with the square of the iteration count —
  the cap is what keeps that bounded.
- **It ends in a fixed schema.** The final turn is structured JSON: a list of findings, or
  `nothing_found`. Rules a JSON schema cannot express — provenance against edit operation,
  page quotes required for a contradiction, every quote naming its session — are checked in
  code after the model returns.
- **The corpus it reads is the current one**, whatever version the session ran against. A
  session from three weeks ago saw an older corpus; a later session, synthesis, or a person
  may have written the same fact since. Every relation is judged against the page as it
  stands today.
- **Every tool call is recorded** per session. Which tools actually get used is half of what
  the stage measures.

### The tools

| tool | what it gives |
|---|---|
| `corpus_index` | every page in this org's corpus, with descriptions |
| `corpus_read` | one org page in full |
| `corpus_search` | search this org's corpus for a term |
| `shared_read` | the shared connector guide for one system |
| `shared_search` | search every shared guide |
| `transcript` | a range of messages from the session under review, in full |
| `transcript_search` | messages in this session matching a term |
| `related_sessions` | other sessions in this org mentioning a term, with a quotable line |
| `related_transcript` | part of another session in this org |
| `corpus_contract` | the contract the corpus is written to |
| `guidance_read` | the instructions the agent was working from |

The org pages tell it what is already known. The shared guides separate a platform default
from an org's override. The session is what it's reviewing. The other-session tools are what
turn a one-off into a convention — and they're the most-used group in practice.

### What the prompt establishes

`harness/extract/prompt.md` carries it in full. Six things it fixes:

- **The bar.** *If this were written today, would a session next week visibly go better
  because of it?* If you cannot picture that session, drop the finding.
- **The three destinations**, and that choosing correctly is most of the work.
- **The override case.** A fact true for every organization is not an org finding — but a
  general default and an organization's override are both correct at once. Shared says 100
  rows per page, this organization has settled on 1,000: write the override and quote the
  default it differs from.
- **Our own platform failures are out of scope.** A disabled tool, a rate limit, a broken
  OAuth panel. Real problems with real owners, not changes to this org's context.
- **The budget and the stop condition.** About twenty tool calls, and stop when you can write
  the findings rather than when you run out of things to check.
- **Nothing is an answer.** Many sessions yield nothing; say so and return an empty list
  rather than stretching a weak observation into a finding.

Five things the stage requires:

- **The contract is read at run time.** Several of its rules reject a finding outright: one
  page per logical system, no provenance citations in the prose.
- **The corpus is read at head.** The session is history; the page is live. Judging a finding
  against the corpus the session saw would re-propose everything written since.
- **Evidence names its session.** Which session, which message, whose turn. Verified by string
  search against that session. Without the session field, a model that knows a value recurs
  has no way to say so, and grabs the nearest quote instead.
- **Tool outputs stay visible.** Connector behaviour lives in what the API returned. Dropping
  outputs also drops everything the user said, because the agent asks questions through a tool.
- **Reasoning stays visible.** Some limits are stated only there.

### What an edit may do

Where a finding came from governs what it can do to a page:

| came from | may |
|---|---|
| the user said it | add, overwrite, delete |
| a system returned it | add, overwrite — never delete |
| the agent concluded it | add only |

Enforced in code. A wrong inference can add noise. It can't destroy what a human
put there.

Also structural: a contradiction must quote the line it contradicts, every quote must name its
session, an insert must name the line it follows.

---

## Stage 3 — Synthesis

Per org, over accumulated findings, slower cadence than extraction. Three jobs extraction
can't do from inside one session:

- **Merge** — the same fact found in several sessions collapses to one page line.
- **Promote** — a value chosen once is a choice; the same value across many sessions and users
  is a convention. Only the second belongs on a page.
- **Resolve** — shared says 100, one use case needs 1,000, another 500. *"You can't do this
  with probabilities and you can't ask the end user."*

A set-reconciliation problem. It sits beside the extractor on its own cadence.

---

## Stage 4 — Routing

| what it is | where it goes |
|---|---|
| fact about the customer's systems | their corpus pages |
| defect in a tool description | the registered tool |
| defect in a tool's source config | that source config |

---

## Stage 5 — Monitor

Periodic, per org, over the corpus's own change history. Everything above it decides what to
write; this is the only stage that finds out whether writing it was correct.

**It needs no new store.** Helix already records every version of every path in an
append-only table, and exposes it per path — each entry carrying the version, the actor, the
writing agent, the timestamp and a tombstone when the line was removed. The monitor reads
that history. It adds a reader, not a mechanism.

### What it measures

Per line, per page, per organization:

- **When it was added, and by whom.** The watcher, a human, or another agent. A line's origin
  is what makes every number below attributable to a stage.
- **How long it survived.** Time from write to first overwrite or deletion. A line still
  standing after months is one the organization is using.
- **How often it changes.** A page rewritten every week is either genuinely volatile or a
  place two sessions keep disagreeing, and the monitor can tell those apart by who is doing
  the rewriting.
- **Whether a human removed it.** The strongest signal available. A line the watcher wrote
  and a person deleted was wrong, and nothing else in the pipeline can discover that.
- **Whether the same fact was written twice.** Two independent writes of one value means
  synthesis failed to merge them.

### What it tunes

Each measurement lands on a specific parameter:

| what the monitor sees | what it changes |
|---|---|
| a category whose lines are deleted by humans more often than they survive | that category is demoted in the filter, or extraction stops emitting it |
| sessions below a rank band yield nothing that survives | the extraction cutoff moves up, and budget goes to the top of the queue |
| a category with high survival and low volume | the filter's weighting on it rises |
| one page churning between two values | a synthesis conflict rather than an extraction error, routed accordingly |
| lines that survive but are never read | the corpus is growing in a direction nobody uses |
| the same fact written by two sessions | synthesis is not merging on that shape of finding |

Without this stage every parameter in the pipeline is set by hand against a fixed eval set,
which measures agreement with one reading at one point in time. The monitor replaces that
with what the organization actually kept.
