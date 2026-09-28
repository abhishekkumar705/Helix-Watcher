"""Part 2's output contract: what the extractor emits per finding.

The filter answers *whether* a session is worth reading. This answers *what is in it* —
with the pages open, the shared corpus readable, and other sessions searchable. So unlike
the filter, its scope is wider than the filter's: an organization's own context, our
prompts and skills, the tool descriptions we generate, and the sources those tools are built
on.

Deliberately out of scope: facts about a connector that hold for every organization, and our
own platform failures. Both are real and both have owners, but neither is a change to this
organization's context, and carrying them here made the extractor report them in place of
the org knowledge it was asked for.

Two rules are enforced here rather than left to the prompt, because both are load-bearing:

  - **Provenance limits the edit.** What the user stated may overwrite or delete a line; what
    a tool returned may overwrite but not delete; what the agent concluded may only add. A
    fluent inference does not get to erase a human's sentence.
  - **A judgement about existing text must quote that text.** `page_quotes` carries the exact
    sentences the relation was decided against, so "already present" and "contradiction" are
    checkable rather than asserted.
  - **Evidence names its session.** A message number is only meaningful inside one session, so
    a claim that something recurs across an organization has to quote the session it recurs
    in. Without that field the model states the recurrence and cites the session in front of
    it, which reads as support and is not.
"""

from __future__ import annotations

#: What the finding is about, which decides who can act on it.
TYPES: dict[str, str] = {
    "org_fact": "True of this organization, not of the system in general",
    "prompt_defect": "Our mode prompt or skill file misled the agent",
    "tool_description_defect": "A generated tool's name or description misdescribes what it does",
    "source_config_defect": "A built source or tool was configured wrongly — a frozen default, a bad parameter",
}

#: For a fact, what kind of knowledge it is. Mirrors the corpus's own concept kinds.
KINDS: dict[str, str] = {
    "language_binding": "What this organization calls something, tied to a real system object",
    "cross_system_identity": "Join keys, matching rules, which system is authoritative",
    "policy_definition": "Cadence, gating, approvals, governance",
    "trust_parameter": "What is withheld, tolerated or trusted",
    "connector_behavior": "Pagination, limits, auth quirks, error shapes, what an endpoint will not do",
    "tool_proposal": "A capability wanted and not delivered",
}

#: Where the evidence came from. The ordering is the trust ordering, and it constrains `edit.op`.
PROVENANCE: dict[str, str] = {
    "user_stated": "The user said it outright, or corrected the agent",
    "user_document": "The user supplied a brief, runbook or file that says it",
    "observed_in_tool_output": "A tool, probe or API returned it",
    "existing_context": "A corpus page already says it",
    "agent_inferred": "The agent concluded it",
}

#: How the finding stands to what is already written at the target.
RELATIONS: dict[str, str] = {
    "addition": "New, and nothing at the target speaks to it",
    "refinement": "The target says something close; this makes it precise",
    "contradiction": "The target says the opposite",
    "obsolete": "The target was true and no longer is",
    "already_present": "The target already says it. Report it, change nothing",
    "already_fixed": "A defect that has since been corrected",
    "new_page": "No page exists for this subject",
}

TARGET_KINDS = ["helix_page", "tool", "tool_source", "prompt_file", "skill_file", "none"]
EDIT_OPS = ["insert_after", "append", "replace", "delete", "new_page", "none"]
OUTCOMES = ["resolved", "workaround", "abandoned"]
DURABILITY = ["durable", "transient"]
CONFIDENCE = ["low", "medium", "high"]

#: The precedence rule, as data. An op not listed for a provenance is refused.
ALLOWED_OPS: dict[str, set[str]] = {
    "user_stated": {"insert_after", "append", "replace", "delete", "new_page", "none"},
    "user_document": {"insert_after", "append", "replace", "delete", "new_page", "none"},
    "observed_in_tool_output": {"insert_after", "append", "replace", "new_page", "none"},
    "existing_context": {"insert_after", "append", "replace", "new_page", "none"},
    "agent_inferred": {"insert_after", "append", "new_page", "none"},
}


def _enum(vocab, description: str) -> dict:
    return {"type": "string", "enum": list(vocab), "description": description}


FINDING = {
    "type": "object",
    "properties": {
        "type": _enum(TYPES, "What this is about. " + "; ".join(f"{k}: {v}" for k, v in TYPES.items())),
        "summary": {"type": "string", "description": "One line"},
        "claim": {"type": "string", "description": "The fact or the defect, stated plainly"},
        "kind": _enum(list(KINDS) + ["none"], "For a fact, what kind. `none` for a defect or platform issue"),
        "root_cause": {"type": "string", "description": "Why the struggle happened"},
        "evidence": {
            "type": "array",
            "minItems": 1,
            "description": (
                "Verbatim quotes with their message numbers. A finding without one is not "
                "reportable, and a claim about other sessions needs a quote from one of them"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "session": {
                        "type": "string",
                        "description": (
                            "`this` for the session under review, otherwise the id of the "
                            "session the quote came from. A message number only means "
                            "something inside one session"
                        ),
                    },
                    "message": {"type": "integer"},
                    "role": {"type": "string", "enum": ["user", "assistant", "tool"]},
                    "quote": {"type": "string", "description": "Copied word for word"},
                },
                "required": ["session", "message", "role", "quote"],
                "additionalProperties": False,
            },
        },
        "provenance": _enum(PROVENANCE, "Where the evidence came from; it limits what edit.op may be"),
        "impact": {
            "type": "object",
            "properties": {
                "turns_wasted": {"type": "integer"},
                "outcome": {"type": "string", "enum": OUTCOMES},
            },
            "required": ["turns_wasted", "outcome"],
            "additionalProperties": False,
        },
        "target": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": TARGET_KINDS},
                "path": {"type": "string", "description": "e.g. org/<id>/connectors/hubspot.md"},
                "section": {"type": "string"},
            },
            "required": ["kind", "path", "section"],
            "additionalProperties": False,
        },
        "relation": _enum(RELATIONS, "How this stands to what the target already says"),
        "page_quotes": {
            "type": "array",
            "description": "Exact sentences from the target that the relation was decided against. "
                           "Required for refinement, contradiction, obsolete and already_present",
            "items": {"type": "string"},
        },
        "edit": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": EDIT_OPS,
                       "description": "`insert_after` names the sentence it follows; `append` adds to the end of target.section and needs no anchor"},
                "anchor_quote": {"type": "string", "description": "The existing sentence this sits beside or replaces"},
                "text": {"type": "string", "description": "Exactly what should be written"},
            },
            "required": ["op", "anchor_quote", "text"],
            "additionalProperties": False,
        },
        "time": {
            "type": "object",
            "properties": {
                "observed_at_message": {"type": "integer"},
                "durability": {"type": "string", "enum": DURABILITY},
                "stated_period": {"type": "string"},
                "valid_from": {"type": "string"},
                "valid_until": {"type": ["string", "null"]},
            },
            "required": ["observed_at_message", "durability", "stated_period",
                         "valid_from", "valid_until"],
            "additionalProperties": False,
        },
        "confidence": {"type": "string", "enum": CONFIDENCE},
    },
    "required": ["type", "summary", "claim", "kind", "root_cause", "evidence", "provenance",
                 "impact", "target", "relation", "page_quotes", "edit", "time", "confidence"],
    "additionalProperties": False,
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": FINDING},
        "nothing_found": {
            "type": "boolean",
            "description": "True when the session yields nothing. An empty result is a real answer",
        },
    },
    "required": ["findings", "nothing_found"],
    "additionalProperties": False,
}

NEEDS_PAGE_QUOTES = {"refinement", "contradiction", "obsolete", "already_present"}


def violations(finding: dict) -> list[str]:
    """Rule checks the JSON schema cannot express. Empty means the finding is well formed."""
    out = []
    prov, op = finding.get("provenance"), (finding.get("edit") or {}).get("op")
    if prov in ALLOWED_OPS and op not in ALLOWED_OPS[prov]:
        out.append(f"provenance {prov!r} may not use edit.op {op!r}")
    if finding.get("relation") in NEEDS_PAGE_QUOTES and not finding.get("page_quotes"):
        out.append(f"relation {finding['relation']!r} requires page_quotes")
    for e in finding.get("evidence") or []:
        if not e.get("session"):
            out.append("every evidence item must name its session, or `this`")
    if op in {"insert_after", "replace"} and not (finding.get("edit") or {}).get("anchor_quote"):
        out.append(f"edit.op {op!r} requires an anchor_quote")
    if op == "new_page" and finding.get("relation") != "new_page":
        out.append("edit.op 'new_page' requires relation 'new_page'")
    return out


def output_format() -> dict:
    """The `output_config.format` value for the extractor."""
    return {"type": "json_schema", "schema": EXTRACT_SCHEMA}


def vocabulary_markdown() -> str:
    def table(title: str, vocab: dict[str, str]) -> str:
        rows = "\n".join(f"| `{k}` | {v} |" for k, v in vocab.items())
        return f"**{title}**\n\n| Value | Meaning |\n|---|---|\n{rows}\n"

    ops = "\n".join(f"| `{k}` | {', '.join(sorted(v))} |" for k, v in ALLOWED_OPS.items())
    return "\n".join([
        table("type", TYPES),
        table("kind", KINDS),
        table("provenance", PROVENANCE),
        table("relation", RELATIONS),
        f"**Which edits each provenance may make**\n\n| Provenance | Allowed `edit.op` |\n|---|---|\n{ops}\n",
    ])
