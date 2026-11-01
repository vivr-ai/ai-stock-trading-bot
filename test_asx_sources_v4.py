#!/usr/bin/env python3
"""Round 4 - same deal: python3 test_asx_sources_v4.py

Every guess in round 3 came back "HTTP 200 but data: {}" - that's not a
block, it's an empty-but-valid result, which usually means one of two
things: (a) the endpoint wants a browser-style "Origin" header we weren't
sending, or (b) "search/price" is a NAME search (like typing into a search
box), not a ticker lookup, so it wants company names ("Fortescue"), not
bare codes ("FMG"). This round tries both fixes together, plus a bare
no-parameter call to see the endpoint's natural response shape.

Paste the output back to Claude when done (~15 seconds).
"""
from __future__ import annotations

import sys

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.asx.com.au/markets/company/VAS",
    "Origin": "https://www.asx.com.au",
}
TIMEOUT = 15
URL = "https://asx.api.cmfyapp.com/asx-research/1.0/search/price"

# Company names instead of bare codes, since "search" smells like a
# name-autocomplete box rather than an exact-ticker lookup.
NAME_QUERIES = ["Vanguard", "Fortescue", "AGL Energy", "TerraCom"]


def line(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def call(params: dict) -> None:
    try:
        resp = requests.get(URL, headers=HEADERS, params=params, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        print(f"{params!r:40s} -> request failed: {exc}")
        return
    snippet = resp.text[:300].replace("\n", " ")
    print(f"{params!r:40s} -> HTTP {resp.status_code}  {snippet}")


def main() -> int:
    line("1) No parameters at all - what's the endpoint's natural shape?")
    call({})

    line("2) Same ticker-code guesses as last time, but WITH an Origin header this time")
    for key in ("q", "query", "symbol", "code", "keyword"):
        call({key: "VAS"})

    line("3) Company NAMES instead of ticker codes")
    for name in NAME_QUERIES:
        for key in ("q", "query", "keyword"):
            call({key: name})

    line("Done - paste everything above back to Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
