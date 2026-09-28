# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=1.8"]
# ///
"""Does writing findings into the corpus make the next session go better?

    uv run harness/loop/run.py --improved /tmp/corpus-improved
    uv run harness/loop/run.py --improved /tmp/corpus-improved --n 3

Every session measured so far ran without org context, or read it and was not helped by it.
Until a session demonstrably goes better, the rest of the pipeline is unjustified. This is
the smallest test of that.

Each probe is a decision point where a real session went wrong, stated as the agent had it.
The same question is asked twice: once with the seed corpus page in context, once with the
page the findings produced. Both arms see a page, so the comparison is content against
content rather than context against nothing. A third model, shown neither page, decides
whether each answer avoids the failure the real session hit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBES = Path(__file__).parent / "probes.json"
SEED_ENV = "EXPRESS_CODE_PATH"

ANSWER_SYSTEM = """You are a Nexla agent building MCP tools against customer systems.

You have one page of this organization's context. Use it. Answer the question directly and
concretely: name the call, the parameter, the value. Six sentences at most.

Do not hedge, do not list options you would not take, and do not ask the user anything you
could settle from the page."""

JUDGE_SYSTEM = """You are grading one answer against one criterion.

You are given a situation, what a real agent did, and what would count as avoiding that
failure. Decide only whether the answer avoids it.

`avoided` is true when the answer meets the stated criterion, even if it is worded
differently or arrives by another route. It is false when the answer repeats the failure,
hedges so much that it commits to nothing, or would need a round trip to the user to settle
something the criterion says it should already know.

Be strict about commitment: an answer that lists the failing approach among several is not
avoiding it."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "avoided": {"type": "boolean"},
        "why": {"type": "string", "description": "One sentence"},
    },
    "required": ["avoided", "why"],
    "additionalProperties": False,
}


def seed_page(org: int, rel: str) -> str | None:
    import os

    base = Path(os.environ.get(SEED_ENV) or ROOT.parent / "express-code")
    for d in (base / "research/org-context/seed-corpus/orgs").glob(f"{org}-*/corpus"):
        p = d / rel
        return p.read_text() if p.is_file() else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--improved", required=True, help="corpus built by harness/apply.py")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--judge-model", default="claude-opus-5")
    ap.add_argument("--n", type=int, default=1, help="repeats per arm")
    ap.add_argument("--out", default=str(ROOT / "out/loop"))
    args = ap.parse_args()

    probes = json.loads(PROBES.read_text())["probes"]
    improved_root = Path(args.improved)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from anthropic import Anthropic

    client = Anthropic()

    def ask(page: str, p: dict) -> str:
        msg = client.beta.messages.create(
            model=args.model, max_tokens=900, system=ANSWER_SYSTEM,
            messages=[{"role": "user", "content":
                       f"# Context page: {p['page']}\n\n{page}\n\n---\n\n"
                       f"# Situation\n\n{p['situation']}\n\n# Question\n\n{p['question']}"}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")

    def grade(p: dict, answer: str) -> dict:
        msg = client.beta.messages.create(
            model=args.judge_model, max_tokens=500, system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content":
                       f"# Situation\n{p['situation']}\n\n# Question\n{p['question']}\n\n"
                       f"# What the real agent did\n{p['what_happened']}\n\n"
                       f"# Avoiding the failure means\n{p['avoids_failure_if']}\n\n"
                       f"# The answer to grade\n{answer}"}],
            output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        )
        return json.loads(next(b.text for b in msg.content if b.type == "text"))

    rows = []
    for p in probes:
        control = seed_page(p["org"], p["page"])
        treated_path = improved_root / f"org-{p['org']}" / p["page"]
        treated = treated_path.read_text() if treated_path.is_file() else None
        if treated is None:
            print(f"{p['id']:22} SKIP — no improved page at {treated_path}")
            continue
        if control is None:
            control = ("This organization has no page for this system yet.\n")
        for arm, page in (("seed", control), ("improved", treated)):
            for i in range(args.n):
                a = ask(page, p)
                v = grade(p, a)
                rows.append({"probe": p["id"], "arm": arm, "run": i,
                             "avoided": v["avoided"], "why": v["why"], "answer": a})
                print(f"{p['id']:22} {arm:9} {'avoided' if v['avoided'] else 'FAILED ':8} {v['why'][:78]}")

    (out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")

    print(f"\n{'probe':22} {'seed':>6} {'improved':>9}")
    tot = {"seed": 0, "improved": 0}
    n = {"seed": 0, "improved": 0}
    for p in probes:
        cells = {}
        for arm in ("seed", "improved"):
            r = [x for x in rows if x["probe"] == p["id"] and x["arm"] == arm]
            if not r:
                continue
            k = sum(x["avoided"] for x in r)
            cells[arm] = f"{k}/{len(r)}"
            tot[arm] += k
            n[arm] += len(r)
        if cells:
            print(f"{p['id']:22} {cells.get('seed',''):>6} {cells.get('improved',''):>9}")
    seed_tot = f"{tot['seed']}/{n['seed']}"
    imp_tot = f"{tot['improved']}/{n['improved']}"
    print(f"{'TOTAL':22} {seed_tot:>6} {imp_tot:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
