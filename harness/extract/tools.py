"""Tools the extractor calls, instead of being handed everything at once.

The filter gets one call and a compact transcript. This is the other half: a slice and a
map, with the rest fetched on demand. Three things only a tool-using reviewer can do, and
each is load-bearing for a category in `common/finding.py`:

  - **Read the target page before judging it.** `relation` and `page_quotes` are unanswerable
    from a corpus index, and `contradicts_written` is exactly the case the filter cannot see.
  - **Read the shared corpus.** `shared_connector_fact` needs to know what the general
    default already says before claiming an organization differs from it.
  - **Look at other sessions.** "Has this happened before here?" separates a fluke from a
    convention, and no amount of context stuffing answers it, because the other sessions were
    never in the prompt.

Every tool is read-only and scoped to one session's organization: none of them accepts an
organization argument, so another tenant's corpus is unreachable by construction.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import material  # noqa: E402

MAX_MATCHES = 40
MAX_CHARS = 20_000


def _clip(text: str, cap: int = MAX_CHARS) -> str:
    return text if len(text) <= cap else text[:cap] + f"\n… [trimmed, {len(text) - cap} more]"


def build_tools(session: material.Session):
    """The tool set for one session, closed over its organization and transcript."""
    from anthropic import beta_tool

    org_root = material.corpus_dir(session.org_id)

    # ------------------------------------------------------------- org corpus

    @beta_tool
    def corpus_index() -> str:
        """List every page in this organization's context corpus, with descriptions.

        Read this first: it is the map. Open a page with `corpus_read` only when its
        description bears on what you are judging.
        """
        return material.corpus_index(session.org_id)

    @beta_tool
    def corpus_read(path: str) -> str:
        """Read one of this organization's context pages in full.

        You must do this before reporting `relation` as refinement, contradiction, obsolete
        or already_present — those require exact `page_quotes` from the page itself.

        Args:
            path: The page path as `corpus_index` lists it, e.g. `connectors/hubspot.md`.
        """
        if org_root is None:
            return "This organization has no context corpus."
        target = org_root / path
        if not target.is_file():
            return f"No page at {path}. `corpus_index` lists what exists."
        return _clip(target.read_text(errors="replace"))

    @beta_tool
    def corpus_search(pattern: str) -> str:
        """Search this organization's corpus for a term.

        Use it before claiming a fact is missing — a page may say it in wording you did not
        anticipate, which makes the finding `already_present` rather than an addition.

        Args:
            pattern: A regular expression, matched case-insensitively.
        """
        return _grep(org_root, pattern, "This organization has no context corpus.")

    # ---------------------------------------------------------- shared corpus

    @beta_tool
    def shared_read(system: str) -> str:
        """Read the shared connector guide for one system.

        This is the general default that an organization's page may override. A fact that is
        true for everyone belongs here, not on the org page; a value this organization tuned
        belongs on the org page and should quote what it differs from.

        Args:
            system: A connector slug, e.g. `hubspot`, `linkedin-ads`, `google-ads`.
        """
        page = material.SHARED_CORPUS / f"{system}.md"
        if not page.is_file():
            have = ", ".join(sorted(p.stem for p in material.SHARED_CORPUS.glob("*.md")))
            return f"No shared page for {system!r}. Available: {have}"
        return _clip(page.read_text(errors="replace"))

    @beta_tool
    def shared_search(pattern: str) -> str:
        """Search every shared connector guide for a term.

        Args:
            pattern: A regular expression, matched case-insensitively.
        """
        return _grep(material.SHARED_CORPUS, pattern, "No shared corpus on disk.")

    # ------------------------------------------------------------- transcript

    @beta_tool
    def transcript(from_message: int, to_message: int) -> str:
        """Read a range of messages from the session under review, in full.

        You were given a compact view with tool payloads stripped. Use this to read a stretch
        properly once something points you at it.

        Args:
            from_message: First message number, 1-based and inclusive.
            to_message: Last message number, inclusive.
        """
        lo, hi = max(1, from_message), min(session.n, to_message)
        if lo > hi:
            return f"This session has {session.n} messages; that range is empty."
        return _clip(material.detailed_view(session.messages[lo - 1 : hi], start=lo, cap=MAX_CHARS))

    @beta_tool
    def transcript_search(pattern: str) -> str:
        """Find messages in this session matching a term, with excerpts.

        Args:
            pattern: A regular expression, matched case-insensitively.
        """
        try:
            rx = re.compile(pattern, re.I)
        except re.error as exc:
            return f"Not a usable regular expression: {exc}"
        hits = []
        for i, msg in enumerate(session.messages, 1):
            # Search the rendered message, not the raw dict: str(msg) is a Python repr, so
            # excerpts taken from it are unquotable and the model ends up citing
            # "'type': 'tool', 'state': {'time'..." as if it were something someone said.
            text = material.detailed_view([msg], start=i, cap=MAX_CHARS)
            if m := rx.search(text):
                hits.append(text[max(0, m.start() - 200) : m.end() + 400])
                if len(hits) >= MAX_MATCHES:
                    break
        if not hits:
            return f"No message matches {pattern!r}."
        return (f"{len(hits)} matching messages. Quotes below are verbatim and safe to cite:\n\n"
                + "\n\n---\n\n".join(hits))

    # --------------------------------------------------------- other sessions

    @beta_tool
    def related_sessions(pattern: str, limit: int = 8) -> str:
        """Find other sessions in this organization that mention a term, with a quotable line.

        This is how a one-off is told from a convention. A value that appears in one session
        is a choice; the same value in six is something the organization has settled, and only
        the second is worth writing down. Only this organization is searched.

        Each hit comes back with the matching message number and the text around it, so a
        finding about recurrence can cite the session it recurs in. **A claim about other
        sessions must quote one of them** — set `session` on the evidence item to that id.

        Args:
            pattern: Text to look for — a connector name, an object name, an error string, or
                a literal default such as `page_size` or an account id.
            limit: How many sessions to return, newest first.
        """
        sql = """
            SELECT session_id,
                   (opencode_session->>'message_count')::int AS msgs,
                   built_mcp_id IS NOT NULL AS built,
                   (session_ai_usage->>'last_aggregated_at')::timestamptz::date AS active,
                   opencode_session
              FROM "express-code".express_code_sessions
             WHERE nexla_org_id = %(org)s AND deleted_at IS NULL
               AND session_id <> %(self)s
               AND opencode_session::text ILIKE %(needle)s
             ORDER BY 4 DESC NULLS LAST LIMIT %(limit)s
        """
        with psycopg.connect(material.dsn()) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"org": session.org_id, "self": session.session_id,
                              "needle": f"%{pattern}%", "limit": min(limit, 20)})
            rows = cur.fetchall()
        if not rows:
            return f"No other session in this organization mentions {pattern!r}."

        rx = re.compile(re.escape(pattern), re.I)
        out = [f"{len(rows)} other sessions mention {pattern!r}. Quotes below are verbatim and "
               f"safe to cite — set the evidence item's `session` to the id shown.\n"]
        for r in rows:
            msgs = (r["opencode_session"] or {}).get("messages") or []
            excerpt = "(matched in session metadata, not in a message)"
            for i, msg in enumerate(msgs, 1):
                text = material.detailed_view([msg], start=i, cap=MAX_CHARS)
                if m := rx.search(text):
                    excerpt = f"m{i}: …{text[max(0, m.start() - 160): m.end() + 260]}…"
                    break
            out.append(
                f"{r['session_id']}  {r['active']}  {r['msgs']} messages  "
                f"{'built a server' if r['built'] else 'no server built'}\n    {excerpt}"
            )
        return "\n\n".join(out)

    @beta_tool
    def related_transcript(session_id: str, from_message: int, to_message: int) -> str:
        """Read part of another session in this same organization.

        Use it to confirm something you saw here happened there too, and in the same way.
        A session in another organization cannot be read.

        Args:
            session_id: An id returned by `related_sessions`.
            from_message: First message number, 1-based and inclusive.
            to_message: Last message number, inclusive.
        """
        with psycopg.connect(material.dsn()) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                'SELECT opencode_session FROM "express-code".express_code_sessions '
                "WHERE session_id = %s AND nexla_org_id = %s AND deleted_at IS NULL",
                (session_id, session.org_id),
            )
            row = cur.fetchone()
        if row is None:
            return "No such session in this organization."
        msgs = (row["opencode_session"] or {}).get("messages") or []
        lo, hi = max(1, from_message), min(len(msgs), to_message)
        if lo > hi:
            return f"That session has {len(msgs)} messages; the range is empty."
        return _clip(material.detailed_view(msgs[lo - 1 : hi], start=lo, cap=MAX_CHARS))

    @beta_tool
    def corpus_contract() -> str:
        """Read the contract the corpus is written to.

        It defines what each collection means, what a page may contain, and what is refused.
        Read it before proposing a new page or a tool proposal — several of its rules reject a
        finding outright rather than merely shaping it.
        """
        page = material.EXPRESS / "research/org-context/COLLECTION_CORPUS_CONTRACT.md"
        if not page.is_file():
            return "The corpus contract is not available in this checkout."
        return _clip(page.read_text(errors="replace"), 40_000)

    # ---------------------------------------------------------- our guidance

    @beta_tool
    def guidance_read(path: str) -> str:
        """Read the instructions the agent was working from.

        Use it to settle whether a `prompt_defect` is real: quote the passage that misled the
        agent, or drop the finding.

        Args:
            path: A repository path, e.g. `skills/mcp-studio/reference/build.md` or
                `agent_modes/mcp-studio/prompts/mcp-studio.md`.
        """
        target = (material.EXPRESS / path).resolve()
        try:
            target.relative_to(material.EXPRESS.resolve())
        except ValueError:
            return "Path outside the repository."
        if not target.is_file():
            return f"No file at {path}."
        return _clip(target.read_text(errors="replace"))

    return [corpus_index, corpus_read, corpus_search, corpus_contract,
            shared_read, shared_search,
            transcript, transcript_search, related_sessions, related_transcript,
            guidance_read]


def _grep(root: Path | None, pattern: str, empty_message: str) -> str:
    if root is None or not root.exists():
        return empty_message
    try:
        rx = re.compile(pattern, re.I)
    except re.error as exc:
        return f"Not a usable regular expression: {exc}"
    hits = []
    for page in sorted(root.rglob("*.md")):
        rel = page.relative_to(root).as_posix()
        for n, line in enumerate(page.read_text(errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                if len(hits) >= MAX_MATCHES:
                    return "\n".join(hits) + "\n… [more matches suppressed]"
    return "\n".join(hits) if hits else f"Nothing matches {pattern!r}."
