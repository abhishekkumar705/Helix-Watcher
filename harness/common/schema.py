"""The filter's output contract, which both harnesses must satisfy.

One question: **does this session contain anything that belongs in this organization's
context corpus?** Not what, not where, not how it should be written — those are a
separate problem for a later call, on the messages cited here.

Two arms cannot be compared unless they answer in the same shape, so this is enforced
through structured outputs rather than requested in prose. The model is used as a
multi-label classifier over one flat set of categories: a probability for each, and one
for the answer itself.

Three properties this shape is chosen for:

  - **Absences are data.** A probability is emitted for every bucket, not only the ones
    that fired, so sessions stay comparable and the threshold can be set afterwards
    from real numbers.
  - **Doubt is visible.** A session split evenly between "struggled" and "platform
    blocked" is telling you the filter cannot separate a knowledge gap from our own
    bug, which a hard label would hide.
  - **Flat.** One object, one number per category, nothing nested. A run's output and a
    hand-written gold file are the same seven keys.
"""

from __future__ import annotations

# ============================================================
# The categories
# ============================================================

#: Every category the filter scores, in one flat set. Each names **where a finding would
#: land**, not what the friction felt like: the corpus structure is already fixed, so the
#: destination is the useful label. Derivation thresholds are in evals/RUBRIC.md.
#:
#: The wording follows express-code's COLLECTION_CORPUS_CONTRACT.md, which decides what each
#: collection will actually accept. A category defined more loosely than the contract invites
#: the filter to pass sessions whose findings the corpus would refuse.
CATEGORIES: dict[str, str] = {
    "new_connector": "A logical system used here has no page in this organization's corpus yet. One page per system, never per flow, and a generic transport (REST, FTP, file upload, API) is not a system when the endpoint or business vocabulary names one",
    "existing_connector_update": "Something new about a system that already has a page: its roles, objects, triggers, read and query behaviour, write affordances, operating constraints or liveness. A limitation counts only when it changes a decision or prevents an unsafe inference",
    "value_convergence": "An operational value — page size, date window, account or property id, record limit — frozen into a built tool and recorded nowhere, which this organization has settled on rather than chosen once",
    "contradicts_written": "What the session did contradicts a line that already exists in the org or shared corpus",
    "concept": "Meaning that cannot be recovered by inspecting one system: an organization policy, a revealed language binding, a cross-system identity or join rule, a trust or authorization parameter. Evidence absence, corpus scope and test-state bookkeeping are not concepts",
    "tool_proposal": "One bounded capability worth building: it returns a decision-ready or composition-ready result, applies organization-specific rules, and has an explicit read, preview, write, approval or escalation boundary. A generic CRUD operation, a test query or a one-off experiment is not one",
    "user_supplied_knowledge": "The user stated a fact, a rule or a correction the agent did not have. Reporting a platform error is not knowledge",
}


def _prob(description: str) -> dict:
    # Structured outputs reject `minimum`/`maximum` on a number, so the range is stated
    # in the description and checked after the call instead.
    return {"type": "number", "description": f"{description}. A probability from 0.0 to 1.0"}


FILTER_SCHEMA = {
    "type": "object",
    "description": (
        "A filter and nothing more. One question: does this session contain anything that "
        "belongs in this organization's context corpus? What exactly, and where it goes, is "
        "a separate problem. Do not decide it now. Give a number for every key; zero is a "
        "real answer."
    ),
    "properties": {
        "anything_publishable": _prob(
            "The answer. Every other key exists to justify this number"
        ),
        **{k: _prob(v) for k, v in CATEGORIES.items()},
    },
    "required": ["anything_publishable", *CATEGORIES],
    "additionalProperties": False,
}


def output_format() -> dict:
    """The `output_config.format` value for either arm's request."""
    return {"type": "json_schema", "schema": FILTER_SCHEMA}


def vocabulary_markdown() -> str:
    """The categories as prose, injected into both prompts so the two arms are told
    exactly the same thing about what they mean."""
    rows = "\n".join(f"| `{k}` | {v} |" for k, v in CATEGORIES.items())
    return f"| Category | Where it lands |\n|---|---|\n{rows}\n"
