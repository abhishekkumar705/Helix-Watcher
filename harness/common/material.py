"""Loading and shaping the material a reviewer sees.

Shared by both parts. The filter gets a compact transcript and the corpus *index*; the
extractor gets tools that reach the same things in full. Keeping the loaders here means
the two parts cannot silently disagree about what a session is.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]

#: The express-code checkout, which is a separate repository and not vendored here. It
#: supplies the seed corpora and the shared connector guides that findings are judged
#: against. Override with EXPRESS_CODE_PATH when it does not sit beside this one.
EXPRESS = Path(os.environ.get("EXPRESS_CODE_PATH") or ROOT.parent / "express-code")
if not EXPRESS.is_dir():                      # tolerate the nested layout too
    EXPRESS = ROOT / "express-code"
SEED_CORPUS = EXPRESS / "research/org-context/seed-corpus/orgs"
SHARED_CORPUS = EXPRESS / "nexla_context/connectors"
SECRETS = Path.home() / ".config/nexla-triage/secrets.env"

TRANSCRIPT_CAP = 120_000

#: Tools whose output is the user talking, not a payload.
USER_SPEECH_TOOLS = {"question", "ask", "elicit"}


def dsn() -> str:
    """The read-only DSN, from the triage secrets file or the environment."""
    if url := os.environ.get("TRIAGE_DATABASE_URL"):
        return url
    if SECRETS.is_file():
        for line in SECRETS.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "TRIAGE_DATABASE_URL":
                return value.strip().strip("'\"")
    raise SystemExit("TRIAGE_DATABASE_URL not set and not in ~/.config/nexla-triage/secrets.env")


@dataclass
class Session:
    session_id: str
    org_id: int
    user_id: int
    mode: str
    built: bool
    messages: list[dict]

    @property
    def n(self) -> int:
        return len(self.messages)


SELECT_ONE = """
SELECT session_id, nexla_org_id, nexla_user_id,
       opencode_config->>'mode_id' AS mode,
       built_mcp_id IS NOT NULL     AS built,
       opencode_session
  FROM "express-code".express_code_sessions
 WHERE session_id = %s AND deleted_at IS NULL
"""


def load_session(conn, session_id: str) -> Session:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SELECT_ONE, (session_id,))
        row = cur.fetchone()
    if row is None:
        raise KeyError(session_id)
    return Session(
        session_id=str(row["session_id"]),
        org_id=row["nexla_org_id"],
        user_id=row["nexla_user_id"],
        mode=row["mode"],
        built=row["built"],
        messages=(row["opencode_session"] or {}).get("messages") or [],
    )


def compact_view(messages: list[dict], start: int = 1, cap: int = TRANSCRIPT_CAP,
                 user_answers: bool = True) -> str:
    """What the user asked, what the agent answered, which tools ran and which failed.

    Drops reasoning and the bodies of successful calls — a filter is deciding whether a
    session is worth reading, and neither helps with that.

    Two exceptions, both because the body *is* the evidence:

      - **Errors** are never clipped.
      - **`question` outputs** carry the user's own answer. In the mcp-studio flow that is
        the main channel for anything a user states: a channel id the agent cannot resolve,
        a property name, a correction.

    Args:
        user_answers: False reproduces the pre-fix material, so the two can be compared on
            equal footing rather than by reverting the file.
    """
    out: list[str] = []
    total = 0
    dropped = 0
    for i, msg in enumerate(messages, start):
        info = msg.get("info") or {}
        body: list[str] = []
        for part in msg.get("parts") or []:
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                body.append(part["text"].strip()[:4000])
            elif kind == "tool":
                state = part.get("state") or {}
                status = state.get("status", "?")
                line = f"  · {part.get('tool', '?')} [{status}]"
                if arg := state.get("input"):
                    line += f" {str(arg)[:180]}"
                if status == "error":
                    line += f"\n    ERR: {str(state.get('error') or state.get('output') or '')[:600]}"
                elif (user_answers and part.get("tool") in USER_SPEECH_TOOLS
                      and (answer := state.get("output"))):
                    line += f"\n    -> {str(answer)[:1500]}"
                body.append(line)
        if err := info.get("error"):
            body.append(f"  MESSAGE ERROR: {err.get('name', '')} {str(err.get('message', ''))[:300]}")
        if not body:
            continue
        block = f"[m{i} · {info.get('role', '?')}]\n" + "\n".join(body)
        if total + len(block) > cap:
            dropped += 1
            continue
        total += len(block)
        out.append(block)
    if dropped:
        out.append(f"\n… [{dropped} further messages omitted: transcript cap reached]")
    return "\n\n".join(out)


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "-", "\t")):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip("'\"")
    return out


def corpus_dir(org_id: int) -> Path | None:
    for path in sorted(SEED_CORPUS.glob(f"{org_id}-*/corpus")):
        return path
    return None


def corpus_index(org_id: int) -> str:
    """Page paths and one-line descriptions — not contents.

    The filter is one cheap call, so it sees the map rather than the territory. That is
    enough to answer "is this system already covered"; it is deliberately *not* enough to
    verify a contradiction, which is the extractor's job with the pages open.
    """
    root = corpus_dir(org_id)
    if root is None:
        return "This organization has no context corpus. Every system it uses is uncovered."
    rows = []
    for page in sorted(root.rglob("*.md")):
        meta = _frontmatter(page.read_text(errors="replace"))
        rel = page.relative_to(root).as_posix()
        rows.append(f"{rel} — {meta.get('description', '')[:150]}")
    if not rows:
        return "The corpus directory exists but is empty."
    shared = sorted(p.stem for p in SHARED_CORPUS.glob("*.md"))
    return (
        f"{len(rows)} pages in this organization's corpus:\n\n"
        + "\n".join(rows)
        + f"\n\nThe shared connector corpus covers {len(shared)} systems: "
        + ", ".join(shared)
    )


def session_facts(s: Session) -> str:
    """Mechanical counts, so the model is not asked to estimate what a grep answers."""
    tools = sum(1 for m in s.messages for p in (m.get("parts") or []) if p.get("type") == "tool")
    errors = sum(
        1
        for m in s.messages
        for p in (m.get("parts") or [])
        if p.get("type") == "tool" and (p.get("state") or {}).get("status") == "error"
    )
    users = sum(1 for m in s.messages if (m.get("info") or {}).get("role") == "user")
    ctx = sum(
        1
        for m in s.messages
        for p in (m.get("parts") or [])
        if p.get("type") == "tool"
        and re.search(r"/workspace/context|/workspace/nexla_context", str(p.get("state") or ""))
    )
    return (
        f"- Mode: {s.mode}\n"
        f"- Organization: {s.org_id}, user {s.user_id}\n"
        f"- Messages: {s.n} ({users} from the user)\n"
        f"- Tool calls: {tools}, of which {errors} failed\n"
        f"- Context files the agent opened: {ctx}\n"
        f"- Outcome: {'a server was built' if s.built else 'no server was built'}"
    )


def detailed_view(messages: list[dict], start: int = 1, cap: int = 400_000,
                  output_chars: int = 1200, input_chars: int = 1200) -> str:
    """Like `compact_view`, but keeps tool outputs.

    The filter decides *whether* a session is worth reading, so a summary is enough. The
    extractor decides *what is in it* — and what an endpoint returned is the evidence for
    every `connector_behavior` finding, so dropping outputs makes those unreachable. Bodies
    are clipped rather than omitted; errors stay whole.
    """
    out: list[str] = []
    total = 0
    dropped = 0
    for i, msg in enumerate(messages, start):
        info = msg.get("info") or {}
        body: list[str] = []
        for part in msg.get("parts") or []:
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                body.append(part["text"].strip()[:6000])
            elif kind == "reasoning" and part.get("text"):
                # The agent stating what it worked out is sometimes the only place a
                # connector limit is named, so it has to be quotable.
                body.append("  (reasoning) " + part["text"].strip()[:2000])
            elif kind == "tool":
                state = part.get("state") or {}
                status = state.get("status", "?")
                lines = [f"  · {part.get('tool', '?')} [{status}]"]
                if arg := state.get("input"):
                    lines.append(f"    IN : {str(arg)[:input_chars]}")
                if status == "error":
                    lines.append(f"    ERR: {str(state.get('error') or state.get('output') or '')}")
                elif answer := state.get("output"):
                    keep = output_chars * 3 if part.get("tool") in USER_SPEECH_TOOLS else output_chars
                    lines.append(f"    OUT: {str(answer)[:keep]}")
                body.append("\n".join(lines))
        if err := info.get("error"):
            body.append(f"  MESSAGE ERROR: {err.get('name', '')} {err.get('message', '')}")
        if not body:
            continue
        block = f"[m{i} · {info.get('role', '?')}]\n" + "\n".join(body)
        if total + len(block) > cap:
            dropped += 1
            continue
        total += len(block)
        out.append(block)
    if dropped:
        out.append(f"\n… [{dropped} further messages omitted: cap reached]")
    return "\n\n".join(out)
