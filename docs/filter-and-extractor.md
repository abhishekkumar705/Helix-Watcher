# Helix Watcher — Filter and Extractor

**Why two parts:**

Reading a session properly takes work. It means opening the corpus pages a finding would
touch, reading the shared connector guide to tell a general default from an org's override,
and searching other sessions to tell a one-off from a convention. That is an agentic loop
over several minutes.

Deciding whether a session is worth that work is one call. It needs the conversation and a
list of what the org already has, and nothing else.

So the watcher does it in two passes. The filter reads everything and says where to look.
The extractor looks and says what to write. Nothing we tried lets one call do both jobs.

**What the filter does:**

One model call, no tools. It gets the session facts, a compacted transcript, and the index of
the org's corpus — page paths and descriptions, not page contents.

It answers one question: does this session contain anything that belongs in this
organization's context corpus? It scores seven categories, each naming where a finding would
land rather than what the friction felt like:

- a system used here with no page yet
- something new about a system that already has one
- an operational value the org has settled on and recorded nowhere
- a line in the corpus the session contradicts
- meaning that can't be recovered from any single system
- a bounded capability worth building
- a fact or correction the user supplied

The wording comes from the corpus contract, because a category defined more loosely than the
contract invites the filter to pass sessions whose findings the corpus would refuse.

**What the extractor does:**

An agentic loop with eleven read-only tools. It gets an opening slice of the session and
fetches the rest itself: corpus pages, the shared connector guides, the corpus contract, our
own skill files, other sessions in the same org, and any stretch of transcript it wants in
full.

It returns findings in a fixed shape — the claim, verbatim evidence, the target page and
section, how the finding stands to what is already written there, and the exact edit anchored
to an existing sentence.

Three things only this pass can do:

- **Read the target before judging it.** Whether a finding refines, contradicts or is already
  present cannot be answered from a page description. It needs the page.
- **Tell an org override from a general truth.** The shared corpus says one thing, an org may
  have settled on another, and both are correct. Deciding which is which means reading both.
- **Tell a convention from a choice.** A value used once is a choice. The same value in nine
  sessions across four users is a convention, and only the second belongs on a page. No amount
  of context stuffing answers this, because the other sessions were never in the prompt.

**Why they are separate:**

- **Deciding is one call; reading is not.** The filter is one call on a compacted transcript.
  The extractor makes a dozen tool calls, reads several pages, and takes a few minutes. One
  pass doing both would either read everything in full or decide without the pages open.
- **They need different material.** The filter gets a summary with tool payloads stripped.
  The extractor needs the payloads: what an endpoint actually returned is the evidence for
  every connector finding.
- **They scale differently.** Filtering every session is routine; extracting every session is
  an order of magnitude more work. Separating them makes how much to extract an operational
  decision rather than an architectural one.

**Evidence and verification:**

Every finding carries verbatim quotes with message numbers, and each quote names the session
it came from. That last part matters: a message number only means something inside one
session, so a claim that a value recurs across an org has to quote a session it recurs in.
Without it the model states the recurrence and attaches a quote from the session in front of
it, which reads as support and is not.

Two checks run on every finding, and they catch different things:

- **A mechanical pass** confirms each quote appears in the session it names. No model
  involved. It reads the other sessions, so a fabricated cross-session quote cannot pass.
- **A model judge** grades against the transcript: is this the same finding as the reference,
  is it in scope, and do the quotes actually carry the claim. It is told not to check whether
  quotes exist, because it only sees one session and would call correct citations fake.

**Where findings go:**

- org facts, connector behaviour, concepts and tool proposals go to that org's pages
- tool description and source configuration defects go to the built tool
- prompt and skill defects are reported for review rather than applied

Facts true of a connector for every organization, and our own platform failures, are out of
scope. Both are real, both have owners, and neither is a change to an org's context.

---

# The experiment

The design above is not a proposal. Both stages are built and were run against hand-labelled
sessions; the claims in it are what those runs support.

**The sets:**

Two hand-labelled sets, one per stage, both derived from real sessions rather than written
to order.

- **The pool.** 36 sessions for the filter, drawn from three organizations, five agent modes
  and thirteen users, ranging from zero messages to 381. Chosen by shape rather than by
  interest: rich builds, builds that went smoothly and taught nothing, sessions where the user
  was only poking at the product, sessions run against demo fixtures, sessions blocked by
  permissions, and long sessions that delivered real work. 19 of them contain something
  publishable and 17 do not.
- **The labels are binary.** Whether a connector has a page is a fact about a directory, not
  an opinion, so the reference is 0 or 1 and the model's probability is scored against it.
  Every label is a readout from a stated check, so two people should derive the same one.
- **The extractor set** is 18 of those sessions labelled a second time, at the level of
  individual findings — 38 of them, each with verbatim evidence.

The filter was run 11 times over all 36 sessions: Opus at three effort levels, Sonnet, Haiku,
and a six-run ablation testing one change with three runs on each side. The extractor was run
over 14 sessions across three passes, each one followed by a mechanical quote check and a
model judge.

**How the extractor is graded:**

Findings cannot be compared as data. The same fact can be written two ways, and a finding the
reference lacks may be right rather than wrong. So grading is split:

- **A mechanical pass** confirms every quote appears in the session it names. Deterministic,
  and it can read the other sessions.
- **A model judge** reads the transcript alongside both answer sets and rules on every finding
  the reference does not have: is it a gap in the reference, out of scope, unsupported by its
  evidence, or trivial. A gap counts as correct, so a run that finds what the labelling missed
  is not punished for it.

That choice is load-bearing: it is what lets the grading show that the reference is the
weaker of the two readings.

**What came out:**

- The filter separates positives from negatives at F1 0.86 and AUC 0.93. It is not fooled by
  friction without knowledge: sessions where the user was only exploring the product, or where
  the data was a demo fixture, all score below 0.25.
- One category, contradicting a written line, scores zero at every model and effort level
  while ranking correctly. It is given page descriptions and asked about page contents. That
  is not a tuning problem, and the extractor owns it instead.
- The extractor returns findings at precision 0.85, with two unsupported and two trivial out
  of forty.
- Across nine sessions the judge confirmed **twenty-five findings the hand labelling missed,
  against nineteen it had**. One session labelled as yielding nothing — 381 messages ending in
  "this is good" — produced five findings, all five confirmed.

The last result is the important one. Nearly every session with real work in it contains
something worth writing. The filter's job is not to find the rare session that does; it is to
rank, so the richest sessions are extracted first.

Three further notes on reading any of these numbers:

- **Run-to-run variance is real.** The same model, prompt and material scored 0.87 and 0.93 on
  separate runs. Single-run comparisons at that resolution are noise; three runs a side
  separated a 0.14 effect and could not separate a 0.04 one.
- **The reference is incomplete.** Hand labelling finds less than the extractor does. A
  disagreement is not evidence of a model error; audit the label first.
- **A quarter of findings are about us.** Of forty findings in the nine-session pass, ten were
  defects in our own prompts and skills. That was the original goal's second half, and it
  is reported for review.
