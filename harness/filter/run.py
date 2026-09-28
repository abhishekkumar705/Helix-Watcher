# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2", "anthropic>=0.40"]
# ///
"""Part 1: the filter. One model call per session, no tools.

    uv run harness/filter/run.py --labelled          # every session with a gold label
    uv run harness/filter/run.py --sessions a,b,c --model claude-sonnet-5

Writes one JSON per session to out/filter/<tag>/. Resumable: a session already written is
skipped unless --force.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import material, schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "evals/labels"
PROMPT = Path(__file__).parent / "prompt.md"


def build_input(s: material.Session, user_answers: bool = True) -> str:
    return f"""# Session facts

{material.session_facts(s)}

---

# The corpus this organization already has

{material.corpus_index(s.org_id)}

---

# The conversation

{material.compact_view(s.messages, user_answers=user_answers)}
"""


def call(client, model: str, effort: str, system: str, user: str) -> tuple[dict, dict]:
    kwargs = dict(
        model=model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": schema.output_format()},
    )
    if effort:
        kwargs["output_config"]["effort"] = effort
    msg = client.beta.messages.create(**kwargs)
    usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
    for block in msg.content:
        if block.type == "text":
            return json.loads(block.text), usage
    raise RuntimeError("no text block in response")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", help="comma-separated ids")
    ap.add_argument("--labelled", action="store_true", help="every session with a gold label")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="", help="output_config effort, e.g. low/medium/high")
    ap.add_argument("--tag", default="run1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-user-answers", action="store_true",
                    help="ablation: drop `question` tool outputs, as the material did before "
                         "the fix. Use it to measure that change rather than reverting files")
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble the input and report its size; make no API call")
    args = ap.parse_args()

    if args.labelled:
        ids = sorted(p.stem for p in LABELS.glob("*.json") if "." not in p.stem)
    elif args.sessions:
        ids = [x.strip() for x in args.sessions.split(",") if x.strip()]
    else:
        return ap.error("pass --labelled or --sessions")

    out = ROOT / "out/filter" / args.tag
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    meta.update({"model": args.model, "effort": args.effort or "default",
                 "user_answers": not args.no_user_answers})
    meta.setdefault("sessions", {})
    started = time.time()
    system = PROMPT.read_text().replace("{{VOCABULARY}}", schema.vocabulary_markdown())

    client = None
    if not args.dry_run:
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
            user = build_input(s, user_answers=not args.no_user_answers)
            if args.dry_run:
                print(f"[{i}/{len(ids)}] {sid[:8]} {s.n:>4} msgs  "
                      f"{len(system) + len(user):>8,} chars  ~{(len(system) + len(user)) // 4:>7,} tok")
                continue
            t0 = time.time()
            try:
                result, usage = call(client, args.model, args.effort, system, user)
            except Exception as exc:  # noqa: BLE001 — one bad session must not stop the sweep
                print(f"[{i}/{len(ids)}] {sid[:8]} FAILED {exc}", file=sys.stderr)
                continue
            dest.write_text(json.dumps(result, indent=2) + "\n")
            meta["sessions"][sid] = {**usage, "seconds": round(time.time() - t0, 1)}
            meta_path.write_text(json.dumps(meta, indent=2) + "\n")
            print(f"[{i}/{len(ids)}] {sid[:8]} {s.n:>4} msgs  "
                  f"pub={result['anything_publishable']:.2f}  "
                  f"{usage['input']:,}->{usage['output']} tok  {time.time() - t0:.0f}s")
    meta["wall_seconds"] = round(time.time() - started, 1)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"\n{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
