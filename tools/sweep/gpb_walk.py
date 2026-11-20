"""Walk getpostingboard `/b` back to a sequence number and save the messages.

The feed pages backwards: the newest page ends with `next_before`, which is the
`?before=` of the next one. A sweep needs a window, not the whole board, so the
walk stops once the oldest message on the page is at or below `--stop`.

    python tools/sweep/gpb_walk.py --stop 17250 --out _sweep/gpb.json

Writes one JSON array, oldest first. The feed carries no author field: signing
is a convention in the body (`— Zemlyak (AI)`), so counting voices means reading
signatures, and `--voices` prints that count for the saved file.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import time

from fetch import get_json

FEED = "https://getpostingboard.dev/b"
SIGNATURE = re.compile(r"—\s*([A-ZА-Я][\w .'\-]{1,40}?)(?:\s*\(|$|\n)")


def walk(stop: int, pause: float = 0.3, page_limit: int = 60) -> list[dict]:
    items: dict[int, dict] = {}
    before: int | None = None
    for _ in range(page_limit):
        url = FEED + (f"?before={before}" if before else "")
        page = get_json(url, headers={"Accept": "application/json"})
        for item in page["items"]:
            items[item["seq"]] = item
        oldest = min(items) if items else 0
        nxt = page.get("next_before")
        if nxt is None or nxt == before or oldest <= stop:
            break
        before = nxt
        time.sleep(pause)
    return [items[k] for k in sorted(items)]


def voices(items: list[dict]) -> collections.Counter:
    counter: collections.Counter = collections.Counter()
    for item in items:
        found = SIGNATURE.findall((item.get("body") or "")[-200:])
        counter[found[-1].strip() if found else "(no signature)"] += 1
    return counter


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stop", type=int, required=True,
                    help="stop once the oldest message reaches this seq")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voices", action="store_true")
    args = ap.parse_args()

    items = walk(args.stop)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False)
    first, last = items[0]["seq"], items[-1]["seq"]
    print(f"{len(items)} messages, seq {first}..{last} -> {args.out}")
    if args.voices:
        for name, count in voices(items).most_common(15):
            print(f"{count:5} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
