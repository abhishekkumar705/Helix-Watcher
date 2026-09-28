# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2"]
# ///
"""Print one session, for reading by hand.

    uv run harness/dump.py <session-id>                 # raw: everything
    uv run harness/dump.py <session-id> --compact       # what the filter sees
    uv run harness/dump.py <session-id> --from 38 --to 70

Two views, because a wrong label has two possible causes. `--compact` is exactly what
`harness/filter` sends; the default is everything, including reasoning and full tool
payloads. If something appears in the raw view and not the compact one, the material is at
fault rather than the label.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import material  # noqa: E402


def raw_view(messages: list[dict], start: int = 1) -> str:
    """Everything, with tool inputs and outputs kept."""
    out: list[str] = []
    for i, msg in enumerate(messages, start):
        info = msg.get("info") or {}
        body: list[str] = []
        for part in msg.get("parts") or []:
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                body.append(part["text"])
            elif kind == "reasoning" and part.get("text"):
                body.append("(reasoning) " + part["text"])
            elif kind == "tool":
                state = part.get("state") or {}
                body.append(
                    f"TOOL {part.get('tool', '?')} [{state.get('status', '?')}]\n"
                    f"  IN : {json.dumps(state.get('input'), ensure_ascii=False)[:8000]}\n"
                    f"  OUT: {str(state.get('output') or state.get('error') or '')[:8000]}"
                )
        if err := info.get("error"):
            body.append(f"MESSAGE ERROR: {err.get('name', '')} {err.get('message', '')}")
        if body:
            rule = "=" * 90
            out.append(f"{rule}\n[m{i} · {info.get('role', '?')}]\n{rule}\n" + "\n\n".join(body))
    return "\n\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session_id")
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=10**6)
    ap.add_argument("--compact", action="store_true", help="what the filter is given")
    args = ap.parse_args()

    with psycopg.connect(material.dsn()) as conn:
        try:
            s = material.load_session(conn, args.session_id)
        except KeyError:
            print("no such session", file=sys.stderr)
            return 1

    lo, hi = max(1, args.lo), min(s.n, args.hi)
    window = s.messages[lo - 1 : hi]
    print(f"# {s.session_id} — org {s.org_id} u{s.user_id} — {s.mode} — "
          f"messages {lo}-{hi} of {s.n} — built={s.built}\n")
    print(material.compact_view(window, start=lo, cap=10**9) if args.compact
          else raw_view(window, start=lo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
