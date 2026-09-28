# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2"]
# ///
"""Check every evidence quote against the session it names.

    uv run harness/extract/verify.py s5
    uv run harness/extract/verify.py s5 --gold      # check the hand-written gold instead

A quote either appears in that session or it does not, so this needs no model. It exists
because the judge cannot do it: the judge is given the session under review, so a quote
naming another session is one it has no way to check.

Reports, per finding:

  - `ok`        the quote is in that session, at or near the stated message
  - `wrong_msg` present in the session, but at a different message
  - `missing`   not in that session at all
  - `no_session` the named session does not exist or is not in this organization

Normalises whitespace before comparing, and falls back to the longest quoted fragment, so a
quote clipped at a word boundary still verifies. Only a quote that is genuinely absent fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import material  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evals/extract"
RUNS = ROOT / "out/extract"

#: A quote shorter than this is too generic to locate meaningfully.
MIN_QUOTE = 8

#: Characters that differ between how a transcript stores text and how a quote reproduces it.
FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                      "\u2013": "-", "\u2014": "-", "\u00a0": " "})
#: How far from the stated message still counts as the right place.
MSG_SLACK = 2


def norm(text: str) -> str:
    """Fold the differences that are not the quote being wrong.

    Smart quotes, dashes and non-breaking spaces vary between how a transcript stores text
    and how a quote reproduces it; so does whitespace around a line break.
    """
    return re.sub(r"\s+", " ", text.translate(FOLD)).strip().lower()


def find_in(messages: list[dict], quote: str) -> list[int]:
    """Message numbers whose rendered text contains the quote."""
    needle = norm(quote)
    if len(needle) < MIN_QUOTE:
        return []
    hits = []
    for i, msg in enumerate(messages, 1):
        # Two renderings: the one a reviewer is shown, and the raw JSON. Tool inputs are
        # displayed as a Python repr, so a quote copied from JSON elsewhere carries double
        # quotes the display never has.
        shown = material.detailed_view([msg], start=i, cap=10**7, output_chars=10**6,
                                       input_chars=10**6)
        if needle in norm(shown) or needle in norm(json.dumps(msg, ensure_ascii=False)):
            hits.append(i)
    if hits:
        return hits
    # A quote clipped mid-sentence still verifies on its longest intact fragment.
    frag = max(re.split(r"[.;\n]", needle), key=len).strip()
    if len(frag) >= MIN_QUOTE * 2:
        for i, msg in enumerate(messages, 1):
            if frag in norm(material.detailed_view([msg], start=i, cap=10**7, output_chars=10**6,
                                             input_chars=10**6)):
                hits.append(i)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", nargs="?", default="", help="a run under out/extract/")
    ap.add_argument("--gold", action="store_true", help="verify evals/extract instead of a run")
    args = ap.parse_args()

    if args.gold:
        files = sorted(p for p in GOLD.glob("*.json") if not p.name.startswith("_"))
        label = "gold"
    else:
        if not args.tag:
            return ap.error("pass a run tag, or --gold")
        files = sorted(p for p in (RUNS / args.tag).glob("*.json") if p.stem != "_meta")
        label = args.tag
    if not files:
        print("nothing to verify")
        return 1

    counts = {"ok": 0, "wrong_msg": 0, "missing": 0, "no_session": 0}
    problems = []
    cache: dict[str, list[dict]] = {}
    with psycopg.connect(material.dsn()) as conn:
        for path in files:
            here = material.load_session(conn, path.stem)
            cache[path.stem] = here.messages
            for fi, f in enumerate(json.loads(path.read_text())["findings"]):
                for e in f["evidence"]:
                    sid = e.get("session") or "this"
                    target = path.stem if sid in ("this", "", path.stem) else sid
                    if target not in cache:
                        try:
                            other = material.load_session(conn, target)
                        except (KeyError, Exception):  # noqa: BLE001
                            counts["no_session"] += 1
                            problems.append((path.stem[:8], fi, f["summary"], "no_session",
                                             f"{target[:8]} not readable"))
                            continue
                        if other.org_id != here.org_id:
                            counts["no_session"] += 1
                            problems.append((path.stem[:8], fi, f["summary"], "no_session",
                                             f"{target[:8]} is org {other.org_id}, not {here.org_id}"))
                            continue
                        cache[target] = other.messages
                    hits = find_in(cache[target], e["quote"])
                    if not hits:
                        counts["missing"] += 1
                        problems.append((path.stem[:8], fi, f["summary"], "missing",
                                         f'{target[:8]} m{e["message"]}: "{e["quote"][:70]}"'))
                    elif any(abs(h - e["message"]) <= MSG_SLACK for h in hits):
                        counts["ok"] += 1
                    else:
                        counts["wrong_msg"] += 1
                        problems.append((path.stem[:8], fi, f["summary"], "wrong_msg",
                                         f'said m{e["message"]}, found at {hits[:4]}'))

    total = sum(counts.values())
    print(f"\n══ {label} — {total} evidence items across {len(files)} sessions\n")
    for k, v in counts.items():
        print(f"  {k:11} {v:>4}  {v / total:>5.0%}" if total else f"  {k:11} {v:>4}")
    if problems:
        print(f"\n── {len(problems)} problems ──")
        for sid, fi, summary, kind, detail in problems:
            print(f"  {sid} [{fi}] {kind:9} {summary[:62]}")
            print(f"            {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
