# /// script
# requires-python = ">=3.11"
# ///
"""Score a filter run against the binary gold labels.

    uv run harness/filter/score.py run1 [run2 ...]

Gold is 0/1 (evals/RUBRIC.md); the model emits a probability, so the run is scored both as
a hard decision and as a ranking.

  - **Decision at 0.5** — precision, recall, F1, accuracy and the confusion counts. What you
    get if you ship the obvious threshold.
  - **Ranking and calibration** — AUC (does it order positives above negatives, regardless of
    calibration), Brier against the base-rate baseline, and the best F1 reachable by sweeping
    the threshold.

The gap between F1@0.5 and F1@best is the part that is a threshold problem rather than a
model problem. A category where Brier beats base but AUC is near 0.5 is calibrated noise;
one where AUC is high and F1@0.5 is poor only needs a different cut.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "evals/labels"
YIELD = ROOT / "evals/yield.json"
RUNS = ROOT / "out/filter"

KEYS = ["anything_publishable", "new_connector", "existing_connector_update",
        "value_convergence", "contradicts_written", "concept", "tool_proposal",
        "user_supplied_knowledge"]


def auc(pairs):
    """Rank-based AUC, ties shared. None when one class is absent."""
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def confusion(pairs, t):
    tp = sum(1 for x, y in pairs if x >= t and y == 1)
    fp = sum(1 for x, y in pairs if x >= t and y == 0)
    fn = sum(1 for x, y in pairs if x < t and y == 1)
    tn = sum(1 for x, y in pairs if x < t and y == 0)
    return tp, fp, fn, tn


def prf(pairs, t):
    tp, fp, fn, tn = confusion(pairs, t)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    acc = (tp + tn) / len(pairs)
    return prec, rec, f1, acc, (tp, fp, fn, tn)


def best_threshold(pairs):
    """The cut that maximises F1, searched over the probabilities actually emitted."""
    best = (0.0, 0.5)
    for t in sorted({round(x, 3) for x, _ in pairs} | {0.5}):
        f1 = prf(pairs, t)[2]
        if f1 > best[0]:
            best = (f1, t)
    return best[1], best[0]


def spearman(pairs) -> float | None:
    """Rank correlation, ties averaged. None when either side is constant."""
    n = len(pairs)
    if n < 3:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    a, b = ranks([p for p, _ in pairs]), ranks([y for _, y in pairs])
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else None


def score_yield(tag: str) -> None:
    """How well the run orders sessions by how much is actually in them.

    The binary label answers whether a session has anything, and almost every real session
    does. What decides where budget goes is how much, so the run is also scored against the
    org_fact count the extractor has confirmed.
    """
    if not YIELD.is_file():
        return
    y = json.loads(YIELD.read_text())["yield"]
    pairs, rows = [], []
    for sid, n in y.items():
        f = RUNS / tag / f"{sid}.json"
        if not f.is_file():
            continue
        p = json.loads(f.read_text())["anything_publishable"]
        pairs.append((p, n))
        rows.append((sid[:8], p, n))
    if not pairs:
        return
    rho = spearman(pairs)
    empty = [p for p, n in pairs if n == 0]
    has = [p for p, n in pairs if n > 0]
    print(f"\nRANKING BY YIELD — {len(pairs)} sessions with a confirmed org_fact count")
    print(f"  Spearman rho {rho:.2f}" if rho is not None else "  Spearman undefined")
    if empty and has:
        print(f"  zero-yield sessions score at most {max(empty):.2f}; "
              f"sessions with content score at least {min(has):.2f}"
              + ("  (cleanly separated)" if max(empty) < min(has) else "  (overlapping)"))


def score(tag: str) -> None:
    run = RUNS / tag
    rows = []
    for gold_path in sorted(LABELS.glob("*.json")):
        if "." in gold_path.stem:
            continue
        pred_path = run / gold_path.name
        if not pred_path.is_file():
            continue
        rows.append((gold_path.stem, json.loads(gold_path.read_text()),
                     json.loads(pred_path.read_text())))
    if not rows:
        print(f"{tag}: no predictions found in {run}")
        return

    per = {k: [(float(p.get(k, 0.0)), int(g[k])) for _, g, p in rows] for k in KEYS}

    print(f"\n══ {tag} — {len(rows)} sessions\n")
    print("DECISION AT 0.5")
    print(f"{'category':26} {'pos':>4} {'prec':>6} {'rec':>6} {'F1':>6} {'acc':>6}   "
          f"{'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3}")
    macro = []
    for k in KEYS:
        prec, rec, f1, acc, (tp, fp, fn, tn) = prf(per[k], 0.5)
        macro.append((prec, rec, f1, acc))
        print(f"{k:26} {sum(y for _, y in per[k]):>4} {prec:>6.2f} {rec:>6.2f} {f1:>6.2f} "
              f"{acc:>6.2f}   {tp:>3} {fp:>3} {fn:>3} {tn:>3}")
    n = len(macro)
    print(f"{'macro average':26} {'':>4} " + " ".join(
        f"{sum(m[i] for m in macro) / n:>6.2f}" for i in range(4)))

    print("\nRANKING AND CALIBRATION")
    print(f"{'category':26} {'base':>5} {'AUC':>6} {'brier':>6} {'vs base':>8} "
          f"{'best t':>7} {'F1@best':>8} {'gain':>6}")
    for k in KEYS:
        pairs = per[k]
        base = sum(y for _, y in pairs) / len(pairs)
        brier = sum((x - y) ** 2 for x, y in pairs) / len(pairs)
        bbase = sum((base - y) ** 2 for _, y in pairs) / len(pairs)
        a = auc(pairs)
        t, f1b = best_threshold(pairs)
        f1_half = prf(pairs, 0.5)[2]
        skill = (1 - brier / bbase) if bbase else 0.0
        print(f"{k:26} {base:>5.0%} {'    — ' if a is None else f'{a:>6.2f}'} "
              f"{brier:>6.3f} {skill:>7.0%}  {t:>7.2f} {f1b:>8.2f} {f1b - f1_half:>+6.2f}")

    print(f"\n{'session':10} " + " ".join(f"{k[:4]:>9}" for k in KEYS))
    for sid, g, p in sorted(rows, key=lambda r: -r[2]["anything_publishable"]):
        flag = "  <" if (p["anything_publishable"] >= 0.5) != bool(g["anything_publishable"]) else ""
        print(f"{sid[:8]:10} "
              + " ".join(f"{p.get(k, 0.0):>4.2f}/{g[k]}" for k in KEYS) + flag)


if __name__ == "__main__":
    for tag in sys.argv[1:] or ["run1"]:
        score(tag)
        score_yield(tag)
