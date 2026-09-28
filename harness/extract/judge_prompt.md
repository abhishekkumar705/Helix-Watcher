# Grading a extractor run

You are grading one session. You have three things: the **session transcript**, a
**hand-written gold set** of findings, and the **run's output**. Decide how well the run did.

## The gold is a reference, not an oracle

It was written by a person reading the transcript. It is incomplete: a run can report
something real that it simply does not contain.

So grade against **the transcript**, using the gold as the reference answer. When the run
reports something the gold does not have, say which of these it is:

- `gold_gap` — real, supported by the transcript, and the gold should have had it.
- `out_of_scope` — real but not this pipeline's business. Facts true of a connector for every
  organization, and our own platform failures, are both explicitly out of scope.
- `unsupported` — the evidence does not carry the claim. The quote is not in the transcript,
  or it is but does not say what the claim says.
- `duplicate` — the same finding as another one it already reported.
- `trivial` — supported but not worth writing: the specifics of this one request, a
  transient id, a restatement of what the tools obviously do.

`unsupported` and `trivial` are the run's fault. `gold_gap` is the gold's fault. `out_of_scope`
is a scope error by the run — real, but it should not have reported it.

## Matching

A prediction matches a gold finding when they are **the same finding**, however differently
worded: the same fact about the same thing, landing in the same place. Different wording,
different emphasis, and a different `kind` are all still a match. A different **target** or a
different **claim** is not.

Grade each match:

- `exact` — same fact, same target, and the edit would achieve the same thing.
- `partial` — same fact, but the target, the relation or the proposed edit is wrong.
- `weak` — gestures at the right thing without stating it usefully.

## Check that the evidence carries the claim

**Do not check whether quotes exist.** A separate mechanical pass confirms every quote
against the session it names. Evidence items carry a `session` field, and one naming a
session other than this one is quoting a transcript you cannot see, so you have no basis to
call it missing.

What you check is different and cannot be automated: **does the quoted text actually support
the claim?** Use `evidence_problems` for these, and only these:

- The claim asserts more than its quotes show — most often a claim that something recurs
  across the organization, carrying only quotes from the session in front of you. A
  cross-session claim needs an evidence item whose `session` is one of those other sessions.
- The quote is real but says something else, or has been read to mean more than it says.
- The speaker is wrong in a way that changes the meaning — a tool result attributed to the
  user reads as a human statement, which outranks it.

A finding whose load-bearing half is uncited is the defect worth naming. A finding whose
quotes are all in another session, properly labelled, is doing exactly what it should.

## An empty gold set

Some sessions genuinely yield nothing. If the gold is empty, the run should have returned
`nothing_found`. Anything it reported is judged the same way — most will be `out_of_scope` or
`trivial`, but check whether it is a `gold_gap` before saying so.

## Scoring

- `recall` — of the gold findings, what fraction the run located, counting `exact` as 1 and
  `partial` as 0.5.
- `precision` — of the run's findings, what fraction are worth keeping. `gold_gap` counts as
  correct; `out_of_scope`, `unsupported`, `trivial` and `duplicate` do not.
- `verdict` — one line a person could act on. Say what the run does well and what it gets
  wrong, not how many numbers you produced.

Be strict about `unsupported` and generous about `gold_gap`. A run that finds real things the
gold missed is doing the job; a run that writes confident claims the transcript does not
support is worse than one that finds nothing.
