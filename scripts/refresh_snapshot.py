"""Pull today's market, re-mark the book, rebuild the facts.

    .venv/bin/python scripts/refresh_snapshot.py            # live
    .venv/bin/python scripts/refresh_snapshot.py --dry-run  # quotes only
    .venv/bin/python scripts/refresh_snapshot.py --snapshot # prove the default

Safe to run on a timer. It never writes to data/ - the dataset stays the
read-only record it is everywhere else in this codebase. What it writes is
build/live_quotes.json, the audit trail for the refresh itself: what was asked
for, what came back, what did not, and when. A fact derived from a quote cites
that quote; this file is how the quote is checked a week later.

The exit code is the part a scheduler cares about: non-zero when nothing came
back live, because a refresh that quietly produced snapshot numbers looks
exactly like a working one from the outside.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from engine.live_feed import (
    SOURCE_ENV,
    KeylessQuotes,
    Quote,
    SnapshotQuotes,
    fetch_all,
    load_config,
)
from engine.loader import BUILD_DIR, load
from engine.marks import derive_marks, load_sensitivities

QUOTES_FILE = "live_quotes.json"


def _as_row(quote: Quote) -> dict:
    return {
        "series_id": quote.series_id,
        "value": quote.value,
        "previous_close": quote.previous_close,
        "pct_change": quote.pct_change,
        "as_of": quote.as_of,
        "origin": quote.origin,
        "live": quote.live,
        "proxy_symbol": quote.proxy_symbol,
        "note": quote.note,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and report quotes, write nothing, rebuild nothing",
    )
    parser.add_argument(
        "--snapshot", action="store_true",
        help="use the dataset's own snapshot instead of the network",
    )
    args = parser.parse_args(argv)

    config = load_config()
    source = SnapshotQuotes() if args.snapshot else KeylessQuotes(config)
    quotes = fetch_all(source, config)

    live = {k: v for k, v in quotes.items() if v.live}
    stale = {k: v for k, v in quotes.items() if not v.live}

    print(f"{len(live)} live, {len(stale)} on snapshot fallback")
    for series_id in sorted(quotes):
        quote = quotes[series_id]
        move = (
            f"{quote.pct_change:+8.4f}%"
            if quote.pct_change is not None
            else "     n/a "
        )
        flag = "live" if quote.live else "SNAP"
        proxy = f"  proxy={quote.proxy_symbol}" if quote.proxy_symbol else ""
        print(
            f"  {flag}  {series_id:<22}{quote.value:>14,.4f}  {move}  "
            f"{quote.as_of}{proxy}"
        )
    for series_id in sorted(stale):
        if stale[series_id].note:
            print(f"        {series_id}: {stale[series_id].note}")

    dataset = load()
    marks = derive_marks(dataset, quotes, load_sensitivities())
    moved = [mark for mark in marks.values() if mark.moved]
    print(f"\n{len(moved)} of {len(marks)} instruments re-marked")

    if args.dry_run:
        print("dry run: nothing written, nothing rebuilt")
        return 0 if (live or args.snapshot) else 1

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    (BUILD_DIR / QUOTES_FILE).write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "snapshot" if args.snapshot else "live",
                "live_count": len(live),
                "fallback_count": len(stale),
                "quotes": [_as_row(quotes[k]) for k in sorted(quotes)],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote build/{QUOTES_FILE}")

    # Rebuild through the same entry point `make build` uses, with the price
    # source pinned, so a scheduled refresh and a hand-run build cannot drift.
    os.environ[SOURCE_ENV] = "snapshot" if args.snapshot else "live"
    from engine.build import build

    build()
    return 0 if (live or args.snapshot) else 1


if __name__ == "__main__":
    sys.exit(main())
