"""Probe every catalog entry and report what answered.

A timeout is not a verdict. On this machine a response is cut between 16 and
30 KiB (see `fetch.py`) and some hosts simply do not answer this network, so
`000`/`timeout` means "not checked from here", never "closed". Two checks make
the line stronger before it goes into BOARDS.md:

  1. repeat from an outside fetch (a different route), and
  2. fetch once with a browser User-Agent — some doors answer curl with 404
     and a browser with 200, which is a fact about the door, not the board.

    python tools/sweep/probe_catalog.py [--out _sweep/liveness.tsv]

Output: section, id, name, status, bytes, seconds, final URL.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import ssl
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"


def entries() -> list[tuple[str, str, str, str]]:
    data = json.loads((ROOT / "app" / "awesome.json").read_text(encoding="utf-8"))
    out = []
    for section in data["sections"]:
        for entry in section["entries"]:
            url = entry["url"].replace("%%BASE%%", "https://foragents.site")
            out.append((section["id"], entry["id"], entry["name"], url))
    return out


def probe(row: tuple[str, str, str, str]) -> tuple:
    section, eid, name, url = row
    ctx = ssl.create_default_context()
    last = "no attempt"
    for target in (url, url.rstrip("/") + "/llms.txt"):
        try:
            req = urllib.request.Request(
                target, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            started = time.time()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                body = r.read(200_000)
                return (section, eid, name, str(r.status), len(body),
                        round(time.time() - started, 1), r.geturl())
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}:{str(exc)[:60]}"
    return (section, eid, name, "ERR", 0, 0.0, last)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = entries()
    with cf.ThreadPoolExecutor(12) as pool:
        result = list(pool.map(probe, rows))

    lines = ["\t".join(r) for r in result]
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n{len(rows)} entries -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
