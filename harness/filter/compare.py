# /// script
# requires-python = ">=3.11"
# ///
"""One row per configuration: how each model and effort level scores on the same gold.

    uv run harness/filter/compare.py                 # every run under out/filter/
    uv run harness/filter/compare.py opus sonnet     # named runs, in that order

Reports the headline decision, the macro figures across all seven categories, and what the
run cost. A configuration is only worth its price if it moves a number that decides
something: `anything_publishable` F1 is the gate's quality, macro AUC is whether the
categories carry information at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "evals/labels"
RUNS = ROOT / "out/filter"

KEYS = ["anything_publishable", "new_connector", "existing_connector_update",
        "value_convergence", "contradicts_written", "concept", "tool_proposal",
        "user_supplied_knowledge"]

#: Published per-million-token prices, for a rough cost column only.
PRICE = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-fable-5-1": (3.0, 15.0),
}


def auc(pairs):
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return None
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def prf(pairs, t=0.5):
    tp = sum(1 for x, y in pairs if x >= t and y == 1)
    fp = sum(1 for x, y in pairs if x >= t and y == 0)
    fn = sum(1 for x, y in pairs if x < t and y == 1)
    tn = sum(1 for x, y in pairs if x < t and y == 0)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, (tp + tn) / len(pairs)


def load(tag: str):
    run = RUNS / tag
    rows = []
    for gold_path in sorted(LABELS.glob("*.json")):
        if "." in gold_path.stem:
            continue
        pred = run / gold_path.name
        if pred.is_file():
            rows.append((json.loads(gold_path.read_text()), json.loads(pred.read_text())))
    meta = {}
    if (run / "_meta.json").is_file():
        meta = json.loads((run / "_meta.json").read_text())
    return rows, meta


def summarise(tag: str) -> dict | None:
    rows, meta = load(tag)
    if not rows:
        return None
    per = {k: [(float(p.get(k, 0.0)), int(g[k])) for g, p in rows] for k in KEYS}

    prec, rec, f1, acc = prf(per["anything_publishable"])
    head_auc = auc(per["anything_publishable"])
    head_brier = sum((x - y) ** 2 for x, y in per["anything_publishable"]) / len(rows)

    sub = KEYS[1:]
    macro_f1 = sum(prf(per[k])[2] for k in sub) / len(sub)
    macro_prec = sum(prf(per[k])[0] for k in sub) / len(sub)
    macro_rec = sum(prf(per[k])[1] for k in sub) / len(sub)
    aucs = [a for k in sub if (a := auc(per[k])) is not None]
    macro_auc = sum(aucs) / len(aucs) if aucs else 0.0
    skills = []
    for k in sub:
        base = sum(y for _, y in per[k]) / len(per[k])
        b = sum((x - y) ** 2 for x, y in per[k]) / len(per[k])
        bb = sum((base - y) ** 2 for _, y in per[k]) / len(per[k])
        skills.append(1 - b / bb if bb else 0.0)

    ses = meta.get("sessions", {})
    tin = sum(v["input"] for v in ses.values())
    tout = sum(v["output"] for v in ses.values())
    secs = [v["seconds"] for v in ses.values()]
    pin, pout = PRICE.get(meta.get("model", ""), (0.0, 0.0))
    return {
        "tag": tag, "model": meta.get("model", "?"), "effort": meta.get("effort", "?"),
        "n": len(rows), "prec": prec, "rec": rec, "f1": f1, "acc": acc,
        "auc": head_auc or 0.0, "brier": head_brier,
        "mf1": macro_f1, "mprec": macro_prec, "mrec": macro_rec, "mauc": macro_auc,
        "mskill": sum(skills) / len(skills) if skills else 0.0,
        "tin": tin, "tout": tout,
        "med_s": sorted(secs)[len(secs) // 2] if secs else 0.0,
        "usd": (tin * pin + tout * pout) / 1e6,
    }


def main() -> int:
    tags = sys.argv[1:] or sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    runs = [r for t in tags if (r := summarise(t))]
    if not runs:
        print("no scored runs found")
        return 1

    print(f"\n{'run':11} {'model':26} {'eff':>7} {'n':>3} │ "
          f"{'F1':>5} {'prec':>5} {'rec':>5} {'acc':>5} {'AUC':>5} {'brier':>6} │ "
          f"{'mF1':>5} {'mAUC':>5} {'mskl':>5} │ {'med s':>6} {'$':>6}")
    print("─" * 132)
    for r in runs:
        print(f"{r['tag']:11} {r['model']:26} {r['effort']:>7} {r['n']:>3} │ "
              f"{r['f1']:>5.2f} {r['prec']:>5.2f} {r['rec']:>5.2f} {r['acc']:>5.2f} "
              f"{r['auc']:>5.2f} {r['brier']:>6.3f} │ "
              f"{r['mf1']:>5.2f} {r['mauc']:>5.2f} {r['mskill']:>5.0%} │ "
              f"{r['med_s']:>6.0f} {r['usd']:>6.2f}")
    print("\nLeft block: anything_publishable, the gate itself, at threshold 0.5.")
    print("Middle: macro over the seven categories — mF1 and mprec/mrec at 0.5, mskl = Brier skill.")
    print("Right: median seconds per session, and total cost for the run at list prices.")

    if len(runs) > 1:
        print(f"\n{'category':26} " + " ".join(f"{r['tag'][:9]:>9}" for r in runs))
        for k in KEYS:
            cells = []
            for r in runs:
                rows, _ = load(r["tag"])
                pairs = [(float(p.get(k, 0.0)), int(g[k])) for g, p in rows]
                cells.append(f"{prf(pairs)[2]:>9.2f}")
            print(f"{k:26} " + " ".join(cells))
        print("\nPer-category F1 at 0.5.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
