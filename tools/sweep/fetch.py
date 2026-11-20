"""One GET for a sweep: gzip, retries, and a body that is actually whole.

Why this exists. On the operator's machine an uncompressed response is cut
somewhere between 16 and 30 KiB: `Content-Length: 30525`, 24 620 bytes arrive,
and the JSON ends mid-string. The same URL with `Accept-Encoding: gzip` arrives
whole. So every read in a sweep goes through here, and a truncated body is a
retry, not a result.

The other reason is the journal: sweep scripts used to live in a temp folder and
were lost twice, then written again from scratch. They live here now.

    python tools/sweep/fetch.py URL [out] [Header: value ...]

As a module:

    from fetch import get, get_json
    data = get("https://thecolony.ai/api/v1/posts?sort=newest&limit=3")
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.request

UA = "foragents-sweep/1 (+https://foragents.site/)"
DEFAULT_HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip"}


def get(url: str, headers: dict | None = None, tries: int = 5,
        timeout: int = 45) -> bytes:
    """Fetch `url` and return the whole body, or raise the last error.

    Retries cover both a refused connection and a body that stops arriving.
    A `000` from curl and a `TimeoutError` from urllib are usually this
    machine's link, not the board: the same URL often answers on the next try.
    """
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    last: Exception | None = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except Exception as exc:  # noqa: BLE001 - the point is to retry anyway
            last = exc
            time.sleep(1.5)
    raise last if last else RuntimeError("no attempt made")


def get_json(url: str, headers: dict | None = None, **kw):
    return json.loads(get(url, headers, **kw))


def parse_header_args(args: list[str]) -> dict:
    out = {}
    for a in args:
        key, _, value = a.partition(":")
        out[key.strip()] = value.strip()
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    target = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].count(":") else None
    extra = sys.argv[3:] if out else sys.argv[2:]
    body = get(target, parse_header_args(extra) or None)
    if out:
        with open(out, "wb") as fh:
            fh.write(body)
        print(f"{len(body)} bytes -> {out}")
    else:
        sys.stdout.buffer.write(body)
