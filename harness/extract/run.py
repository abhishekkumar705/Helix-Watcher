# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2", "anthropic>=1.8"]
# ///
"""Part 2: the extractor. An agentic loop with ten read-only tools.

    uv run harness/extract/run.py --labelled              # every session with gold findings
    uv run harness/extract/run.py --sessions a,b --tag t2

Unlike the filter, this one is given a slice and a map rather than everything: an opening
stretch of transcript and the session's facts, then it fetches what it needs. That is the
point — `relation` and `page_quotes` require reading the target page, and telling a
convention from a one-off requires looking at other sessions. Neither is answerable from a
prompt, however much you put in it.

Writes one JSON per session to out/extract/<tag>/, plus `_meta.json` with the tool calls each
session made — which tools actually get used is half of what this experiment is measuring.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools as extract_tools  # noqa: E402
from common import finding, material  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evals/extract"
PROMPT = Path(__file__).parent / "prompt.md"

#: How much transcript to hand over unprompted. Enough to know what the session was, not so
#: much that `transcript` never gets called.
OPENING = 40

#: The loop resends the whole conversation each turn, so cost grows with the square of the
#: iteration count.
MAX_ITERATIONS = 22


def build_input(s: material.Session) -> str:
    head = material.detailed_view(s.messages[:OPENING], cap=60_000, output_chars=600)
    more = (f"\n\n… {s.n - OPENING} further messages. Use `transcript` or `transcript_search` "
            f"to read them." if s.n > OPENING else "")
    return f"""# Session facts

{material.session_facts(s)}

---

# The conversation, first {min(OPENING, s.n)} of {s.n} messages

{head}{more}
"""


def run_one(client, model: str, effort: str, system: str, s: material.Session) -> tuple[dict, dict]:
    kwargs = dict(
        model=model,
        max_tokens=16_000,
        system=system,
        messages=[{"role": "user", "content": build_input(s)}],
        tools=extract_tools.build_tools(s),
        max_iterations=MAX_ITERATIONS,
        output_config={"format": finding.output_format()},
    )
    if effort:
        kwargs["output_config"]["effort"] = effort

    runner = client.beta.messages.tool_runner(**kwargs)
    calls: list[str] = []
    usage = {"input": 0, "output": 0}
    last = None
    for message in runner:
        last = message
        usage["input"] += message.usage.input_tokens
        usage["output"] += message.usage.output_tokens
        calls += [b.name for b in message.content if b.type == "tool_use"]

    for block in (last.content if last else []):
        if block.type == "text":
            return json.loads(block.text), {**usage, "tool_calls": calls}
    raise RuntimeError("the loop ended without a structured answer")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions")
    ap.add_argument("--labelled", action="store_true", help="every session with gold findings")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--tag", default="run1")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.labelled:
        ids = sorted(p.stem for p in GOLD.glob("*.json") if not p.name.startswith("_"))
    elif args.sessions:
        ids = [x.strip() for x in args.sessions.split(",") if x.strip()]
    else:
        return ap.error("pass --labelled or --sessions")

    out = ROOT / "out/extract" / args.tag
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    meta.update({"model": args.model, "effort": args.effort or "default"})
    meta.setdefault("sessions", {})

    system = PROMPT.read_text().replace("{{VOCABULARY}}", finding.vocabulary_markdown())

    from anthropic import Anthropic

    client = Anthropic()
    with psycopg.connect(material.dsn()) as conn:
        for i, sid in enumerate(ids, 1):
            dest = out / f"{sid}.json"
            if dest.exists() and not args.force:
                print(f"[{i}/{len(ids)}] {sid[:8]} cached")
                continue
            try:
                s = material.load_session(conn, sid)
            except KeyError:
                print(f"[{i}/{len(ids)}] {sid[:8]} NOT FOUND", file=sys.stderr)
                continue
            t0 = time.time()
            try:
                result, stats = run_one(client, args.model, args.effort, system, s)
            except Exception as exc:  # noqa: BLE001 — one bad session must not stop the sweep
                print(f"[{i}/{len(ids)}] {sid[:8]} FAILED {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue
            bad = [v for f in result["findings"] for v in finding.violations(f)]
            dest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
            meta["sessions"][sid] = {**stats, "seconds": round(time.time() - t0, 1),
                                     "violations": bad}
            meta_path.write_text(json.dumps(meta, indent=2) + "\n")
            print(f"[{i}/{len(ids)}] {sid[:8]} {s.n:>4} msgs  "
                  f"{len(result['findings'])} findings  {len(stats['tool_calls'])} tool calls  "
                  f"{stats['input']:,}->{stats['output']} tok  {time.time() - t0:.0f}s"
                  + (f"  ⚠ {len(bad)} rule violations" if bad else ""))
    print(f"\n{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
