# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2"]
# ///
"""Write findings into a working copy of the corpus.

    uv run harness/apply.py --out /tmp/corpus-improved
    uv run harness/apply.py --out /tmp/corpus-improved --orgs <org-id>

Copies each organization's seed corpus, then applies the corpus-targeted findings to the
copy. The seed is never modified.

Only findings whose target is a corpus page are applied. Defects in tools, tool sources,
prompts and skills have other owners and other write paths; they are counted and listed.

An edit that cannot be placed is reported rather than guessed at: an `insert_after` whose
anchor is not on the page would otherwise land somewhere arbitrary, which is worse than not
landing at all.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import material  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals/extract"

#: `org/<id>/connectors/hubspot.md` and `connectors/hubspot.md` both name the same page.
TARGET = re.compile(r"^(?:org/(?P<org>\d+)/)?(?P<rest>.+\.md)$")


def corpus_root(org: int) -> Path | None:
    return material.corpus_dir(org)


def resolve(path: str, default_org: int) -> tuple[int, str] | None:
    m = TARGET.match(path.strip())
    if not m:
        return None
    return int(m["org"] or default_org), m["rest"]


def place(text: str, edit: dict, section: str) -> tuple[str, str]:
    """Apply one edit. Returns the new text and what happened."""
    op, anchor, addition = edit["op"], edit["anchor_quote"].strip(), edit["text"].strip()
    if op == "none" or not addition:
        return text, "skipped"

    if op in {"insert_after", "replace"} and anchor:
        idx = text.find(anchor)
        if idx == -1:
            squashed = re.sub(r"\s+", " ", anchor)
            for line in text.splitlines():
                if squashed and squashed in re.sub(r"\s+", " ", line):
                    idx = text.find(line)
                    anchor = line
                    break
        if idx == -1:
            return text, "anchor not found"
        if op == "replace":
            return text[:idx] + addition + text[idx + len(anchor):], "replaced"
        end = text.find("\n", idx + len(anchor))
        end = len(text) if end == -1 else end
        return text[:end] + "\n" + addition + text[end:], "inserted"

    # append: end of the named section, else end of the page
    if section:
        heads = [(m.start(), m.group(0)) for m in re.finditer(r"(?m)^#{1,6} .*$", text)]
        for i, (pos, head) in enumerate(heads):
            if section.lower().strip("# ") in head.lower():
                stop = heads[i + 1][0] if i + 1 < len(heads) else len(text)
                body = text[:stop].rstrip()
                return body + "\n" + addition + "\n\n" + text[stop:].lstrip("\n"), "appended to section"
    return text.rstrip() + "\n" + addition + "\n", "appended"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="directory to build the improved copy in")
    ap.add_argument("--orgs", help="comma-separated org ids; default is every org with findings")
    args = ap.parse_args()

    findings = []
    for f in sorted(GOLD.glob("*.json")):
        if f.name.startswith("_"):
            continue
        for x in json.loads(f.read_text())["findings"]:
            findings.append((f.stem, x))

    wanted = {int(o) for o in args.orgs.split(",")} if args.orgs else None
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)

    todo: dict[int, list] = {}
    elsewhere = Counter()
    for sid, x in findings:
        if x["target"]["kind"] != "helix_page":
            elsewhere[x["type"]] += 1
            continue
        org_guess = int(re.search(r"org/(\d+)/", x["target"]["path"]).group(1)) if "org/" in x["target"]["path"] else None
        if org_guess is None:
            # the session's own org
            org_guess = int(json.loads((GOLD / f"{sid}.json").read_text()).get("_org") or 0)
        if wanted and org_guess not in wanted:
            continue
        todo.setdefault(org_guess, []).append((sid, x))

    result = Counter()
    for org, items in sorted(todo.items()):
        src = corpus_root(org)
        if src is None:
            print(f"org {org}: no seed corpus, skipping {len(items)} findings")
            continue
        dst = out / f"org-{org}"
        shutil.copytree(src, dst)
        print(f"\n══ org {org} — {len(list(dst.rglob('*.md')))} pages copied, {len(items)} findings")
        for sid, x in items:
            r = resolve(x["target"]["path"], org)
            if r is None:
                print(f"  ?  unparsable target {x['target']['path']}")
                result["bad target"] += 1
                continue
            _, rel = r
            page = dst / rel
            if x["edit"]["op"] == "new_page":
                if page.exists():
                    # Several sessions independently propose the same new page. Without a
                    # synthesis pass to merge them first, the later ones append.
                    page.write_text(page.read_text().rstrip() + "\n\n"
                                    + x["edit"]["text"].strip() + "\n")
                    outcome = "merged into existing page"
                else:
                    page.parent.mkdir(parents=True, exist_ok=True)
                    page.write_text(f"# {x['summary']}\n\n{x['edit']['text'].strip()}\n")
                    outcome = "created"
            elif not page.exists():
                outcome = "page missing"
            else:
                text, outcome = place(page.read_text(), x["edit"], x["target"]["section"])
                if outcome in {"inserted", "replaced", "appended", "appended to section"}:
                    page.write_text(text)
            result[outcome] += 1
            mark = " " if outcome in {"inserted", "replaced", "appended", "appended to section",
                                      "created", "merged into existing page"} else "!"
            print(f"  {mark} {outcome:24} {rel:46} {sid[:8]}")

    print(f"\napplied: {dict(result)}")
    if elsewhere:
        print(f"not corpus targets (other owners): {dict(elsewhere)}")
    print(f"\n{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
