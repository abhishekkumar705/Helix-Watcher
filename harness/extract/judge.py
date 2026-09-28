# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2", "anthropic>=1.8"]
# ///
"""Grade a extractor run with a model, against the gold and the transcript.

    uv run harness/extract/judge.py s5
    uv run harness/extract/judge.py s5 --model claude-opus-5 --tag-out j1

Findings cannot be compared mechanically — the same fact can be written two ways, and a
prediction the gold lacks may be a real finding rather than an error. Both judgements need
reading, so the judge gets the transcript as well as the two answer sets, verifies the
evidence quotes, and rules on every unmatched prediction: is it a gap in the gold, out of
scope, or unsupported?

Writes one verdict per session to out/judged/<run-tag>/ and prints the aggregate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import material  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evals/extract"
RUNS = ROOT / "out/extract"
PROMPT = Path(__file__).parent / "judge_prompt.md"

MATCH_QUALITY = ["exact", "partial", "weak"]
EXTRA_VERDICT = ["gold_gap", "out_of_scope", "unsupported", "duplicate", "trivial"]

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "description": "One entry per prediction that corresponds to a gold finding",
            "items": {
                "type": "object",
                "properties": {
                    "gold_index": {"type": "integer"},
                    "pred_index": {"type": "integer"},
                    "quality": {"type": "string", "enum": MATCH_QUALITY},
                    "note": {"type": "string", "description": "What differs, if anything"},
                },
                "required": ["gold_index", "pred_index", "quality", "note"],
                "additionalProperties": False,
            },
        },
        "missed": {
            "type": "array",
            "description": "Gold findings the run did not locate",
            "items": {
                "type": "object",
                "properties": {
                    "gold_index": {"type": "integer"},
                    "why": {"type": "string", "description": "Why you think it was missed"},
                },
                "required": ["gold_index", "why"],
                "additionalProperties": False,
            },
        },
        "extra": {
            "type": "array",
            "description": "Predictions with no gold counterpart, each ruled on",
            "items": {
                "type": "object",
                "properties": {
                    "pred_index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": EXTRA_VERDICT},
                    "why": {"type": "string"},
                },
                "required": ["pred_index", "verdict", "why"],
                "additionalProperties": False,
            },
        },
        "evidence_problems": {
            "type": "array",
            "description": (
                "Findings whose quotes do not carry the claim. Not whether a quote exists — "
                "verify.py checks that mechanically and can read the other sessions"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "pred_index": {"type": "integer"},
                    "problem": {"type": "string"},
                },
                "required": ["pred_index", "problem"],
                "additionalProperties": False,
            },
        },
        "recall": {"type": "number", "description": "0 to 1; exact counts 1, partial 0.5"},
        "precision": {"type": "number", "description": "0 to 1; gold_gap counts as correct"},
        "verdict": {"type": "string", "description": "One line a person could act on"},
    },
    "required": ["matches", "missed", "extra", "evidence_problems",
                 "recall", "precision", "verdict"],
    "additionalProperties": False,
}


def build_input(s: material.Session, gold: dict, pred: dict) -> str:
    def render(name: str, findings: list[dict]) -> str:
        if not findings:
            return f"## {name}\n\n(empty — this session was judged to yield nothing)\n"
        out = [f"## {name}\n"]
        for i, f in enumerate(findings):
            out.append(
                f"### [{i}] {f['summary']}\n"
                f"- type: `{f['type']}` · kind: `{f['kind']}` · relation: `{f['relation']}`\n"
                f"- target: `{f['target']['kind']}` `{f['target']['path']}` "
                f"§{f['target']['section'] or '-'}\n"
                f"- provenance: `{f['provenance']}` · confidence: `{f['confidence']}`\n"
                f"- claim: {f['claim']}\n"
                f"- evidence: " + " | ".join(
                    (f"m{e['message']} ({e['role']})" if (e.get("session") or "this") == "this"
                     else f"session {e['session'][:8]} m{e['message']} ({e['role']}) "
                          f"[another session, already verified]")
                    + f": \"{e['quote'][:300]}\""
                    for e in f["evidence"])
                + f"\n- edit: `{f['edit']['op']}` → {f['edit']['text'][:300]}\n"
            )
        return "\n".join(out)

    return f"""# Session {s.session_id}

org {s.org_id} · user {s.user_id} · {s.mode} · {s.n} messages · built={s.built}

{render("GOLD — the hand-written reference", gold["findings"])}

---

{render("RUN OUTPUT — what you are grading", pred["findings"])}

---

# The transcript

{material.detailed_view(s.messages, cap=260_000, output_chars=900)}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", help="the extractor run to grade")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    run = RUNS / args.tag
    out = ROOT / "out/judged" / args.tag
    out.mkdir(parents=True, exist_ok=True)
    system = PROMPT.read_text()

    from anthropic import Anthropic

    client = Anthropic()
    results = []
    with psycopg.connect(material.dsn()) as conn:
        for gold_path in sorted(p for p in GOLD.glob("*.json") if not p.name.startswith("_")):
            pred_path = run / gold_path.name
            if not pred_path.is_file():
                continue
            dest = out / gold_path.name
            if dest.exists() and not args.force:
                results.append((gold_path.stem, json.loads(dest.read_text())))
                print(f"{gold_path.stem[:8]} cached")
                continue
            s = material.load_session(conn, gold_path.stem)
            gold = json.loads(gold_path.read_text())
            pred = json.loads(pred_path.read_text())
            kwargs = dict(
                model=args.model,
                max_tokens=8000,
                system=system,
                messages=[{"role": "user", "content": build_input(s, gold, pred)}],
                output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            )
            if args.effort:
                kwargs["output_config"]["effort"] = args.effort
            msg = client.beta.messages.create(**kwargs)
            verdict = next(json.loads(b.text) for b in msg.content if b.type == "text")
            dest.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")
            results.append((gold_path.stem, verdict))
            print(f"{gold_path.stem[:8]}  recall {verdict['recall']:.2f}  "
                  f"precision {verdict['precision']:.2f}  {verdict['verdict'][:70]}")

    if not results:
        print(f"nothing to grade in {run}")
        return 1

    print(f"\n{'session':10} {'gold':>4} {'pred':>4} {'rec':>5} {'prec':>5}  extras")
    tally: dict[str, int] = {}
    for sid, v in results:
        gold = json.loads((GOLD / f"{sid}.json").read_text())["findings"]
        pred = json.loads((run / f"{sid}.json").read_text())["findings"]
        ex = {}
        for e in v["extra"]:
            ex[e["verdict"]] = ex.get(e["verdict"], 0) + 1
            tally[e["verdict"]] = tally.get(e["verdict"], 0) + 1
        print(f"{sid[:8]:10} {len(gold):>4} {len(pred):>4} {v['recall']:>5.2f} "
              f"{v['precision']:>5.2f}  " + ", ".join(f"{k}={n}" for k, n in sorted(ex.items())))
    n = len(results)
    print(f"\nmean recall {sum(v['recall'] for _, v in results) / n:.2f}   "
          f"mean precision {sum(v['precision'] for _, v in results) / n:.2f}")
    if tally:
        print("unmatched predictions: " + ", ".join(f"{k}={c}" for k, c in sorted(tally.items())))
    ep = sum(len(v["evidence_problems"]) for _, v in results)
    print(f"evidence problems: {ep}")
    gaps = [(sid, e) for sid, v in results for e in v["extra"] if e["verdict"] == "gold_gap"]
    if gaps:
        print(f"\n── {len(gaps)} findings the gold should have had ──")
        for sid, e in gaps:
            pred = json.loads((run / f"{sid}.json").read_text())["findings"]
            print(f"  {sid[:8]}  {pred[e['pred_index']]['summary'][:90]}")
            print(f"            {e['why'][:140]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
