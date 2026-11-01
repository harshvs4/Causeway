"""Live quotes for the real series behind a synthetic book.

The instruments in data/ are invented. The twenty-three series in
market_context.csv are not - SPX, Brent, USDSGD and the rest are observable in
the real world right now, and this module is the only place in the codebase
permitted to go and look.

Three rules shape it.

**Change is measured against the provider's own previous close.** Never against
the dataset's snapshot value. The snapshot figures are synthetic, so
differencing a real quote against one would report the distance between fiction
and reality as though it were a market move - a large, confident, meaningless
number of exactly the kind this product exists to prevent.

**A missing quote is reported, not filled in.** Three series have no honest live
source and say so in market_feed.yaml. A failed fetch degrades to the snapshot
value with `live=False`, and every fact derived from it carries that in its own
source. Silence would be indistinguishable from a working feed.

**The network is never touched by a test, or by a default build.** The source is
injected; `SnapshotQuotes` is the default and reads the CSV. Live is opt-in,
because `make build` has to stay reproducible - the goldens pinned in tests/ are
exact to four decimal places and cannot depend on what Brent did this morning.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Protocol

import yaml

from engine.loader import DATA_DIR, LATEST_SNAPSHOT

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "market_feed.yaml"

# A live fetch is a demo dependency, not a correctness one. Fail fast and fall
# back rather than hanging a build or a console refresh on a slow endpoint.
TIMEOUT_SECONDS = 8.0

# Yahoo rejects the default urllib agent.
_USER_AGENT = "Mozilla/5.0 (compatible; Anchorline/1.0)"

# How far back to ask Frankfurter, to be sure of catching a previous
# publication across a weekend or a holiday.
_FX_LOOKBACK_DAYS = 10


class FeedConfigError(RuntimeError):
    """The feed map is malformed. Better than quoting the wrong instrument."""


@dataclass(frozen=True)
class Quote:
    """One observation of one series, and everything needed to defend it.

    `pct_change` is the move from `previous_close` to `value`. It is the only
    figure downstream code uses, because a proxy's *level* is not comparable to
    the series it stands in for, while its *move* is a fair estimate.
    """

    series_id: str
    value: float
    previous_close: float | None
    as_of: str                       # ISO 8601, UTC
    origin: str                      # provider identifier or "market_context.csv"
    live: bool                       # False when this is the snapshot fallback
    proxy_symbol: str | None = None  # set when a stand-in was quoted
    note: str = ""                   # why a proxy, or why a gap

    @property
    def pct_change(self) -> float | None:
        if not self.previous_close:
            return None
        return (self.value / self.previous_close - 1) * 100

    @property
    def row_ref(self) -> str:
        """How a Source points at this quote. Timestamped, because a live
        number without the moment it was taken cannot be checked later."""
        return f"{self.series_id}@{self.as_of}"


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = yaml.safe_load(path.read_text())
    for key in ("providers", "series", "gaps"):
        if key not in config:
            raise FeedConfigError(f"{path.name} is missing the {key!r} section")
    overlap = set(config["series"]) & set(config["gaps"])
    if overlap:
        raise FeedConfigError(
            f"series declared both quotable and a gap: {sorted(overlap)}"
        )
    for series_id, spec in config["series"].items():
        if spec.get("proxy") and not spec.get("why"):
            raise FeedConfigError(
                f"{series_id} is marked proxy with no 'why'. A stand-in whose "
                f"reasoning is not written down is not auditable."
            )
        if spec.get("provider") not in config["providers"]:
            raise FeedConfigError(
                f"{series_id} names unknown provider {spec.get('provider')!r}"
            )
    return config


class QuoteSource(Protocol):
    """Anything that can answer "what is this series worth".

    Implementations must never raise for one bad series - a single unreachable
    symbol has to degrade to a fallback rather than taking the refresh with it.
    """

    def fetch(self, series_ids: Iterable[str]) -> dict[str, Quote]: ...


# ---------------------------------------------------------------------------
# The default: no network at all
# ---------------------------------------------------------------------------

class SnapshotQuotes:
    """The dataset's own last snapshot, dressed as quotes.

    This is the default source, so a build with no connectivity produces exactly
    the numbers the tests pin. `live=False` with `previous_close=None` together
    mean "nothing moved, because nothing was asked".
    """

    origin = "market_context.csv"

    def __init__(self, snapshot: str = LATEST_SNAPSHOT) -> None:
        self.snapshot = snapshot
        self._values = _snapshot_values(snapshot)

    def fetch(self, series_ids: Iterable[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for series_id in series_ids:
            if series_id not in self._values:
                continue
            out[series_id] = Quote(
                series_id=series_id,
                value=self._values[series_id],
                previous_close=None,
                as_of=self.snapshot,
                origin=self.origin,
                live=False,
                note="snapshot value; no live quote requested",
            )
        return out


def _snapshot_values(snapshot: str) -> dict[str, float]:
    """series_id -> value at one snapshot, read with the standard library.

    Deliberately not pandas: this is called from a refresh script that should
    not need the analytical stack loaded just to go and get a price.
    """
    import csv

    values: dict[str, float] = {}
    with open(DATA_DIR / "market_context.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["snapshot_date"] != snapshot:
                continue
            try:
                values[row["series_id"]] = float(row["value"])
            except (TypeError, ValueError):
                continue
    return values


# ---------------------------------------------------------------------------
# Live, keyless
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode())


_FETCH_ERRORS = (
    urllib.error.URLError, OSError, KeyError, IndexError, TypeError,
    ValueError, json.JSONDecodeError,
)


class YahooQuotes:
    """Equity indices, volatility, commodities and the 10-year yield.

    Unofficial and keyless. The endpoint returns both the current price and its
    own previous close, which is what makes an honest percentage change possible
    without keeping any state between refreshes.
    """

    origin = "yahoo"

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_config()
        self.base_url = self.config["providers"]["yahoo"]["base_url"]

    def fetch(self, series_ids: Iterable[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for series_id in series_ids:
            spec = self.config["series"].get(series_id)
            if not spec or spec.get("provider") != self.origin:
                continue
            quote = self._fetch_one(series_id, spec)
            if quote is not None:
                out[series_id] = quote
        return out

    def _fetch_one(self, series_id: str, spec: dict) -> Quote | None:
        symbol = spec["symbol"]
        url = f"{self.base_url}{urllib.parse.quote(symbol)}?range=5d&interval=1d"
        try:
            meta = _get_json(url)["chart"]["result"][0]["meta"]
            value = meta["regularMarketPrice"]
        except _FETCH_ERRORS:
            return None                      # caller falls back to the snapshot
        if value is None:
            return None

        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        stamp = meta.get("regularMarketTime")
        as_of = (
            datetime.fromtimestamp(int(stamp), timezone.utc)
            if stamp
            else datetime.now(timezone.utc)
        ).isoformat(timespec="seconds")

        return Quote(
            series_id=series_id,
            value=float(value),
            previous_close=float(previous) if previous else None,
            as_of=as_of,
            origin=f"{self.origin}:{symbol}",
            live=True,
            proxy_symbol=symbol if spec.get("proxy") else None,
            note=str(spec.get("why", "")).strip(),
        )


class FrankfurterQuotes:
    """FX, from European Central Bank reference rates.

    The exact leg of this feed. A series id <base><quote> means quote-per-base,
    so the request is made with the matching base and the rate used exactly as
    returned - no inversion anywhere, that being the one FX bug which produces a
    plausible wrong number rather than an obvious one.
    """

    origin = "frankfurter"

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_config()
        self.base_url = self.config["providers"]["frankfurter"]["base_url"]

    def fetch(self, series_ids: Iterable[str]) -> dict[str, Quote]:
        wanted: dict[str, dict] = {}
        for series_id in series_ids:
            spec = self.config["series"].get(series_id)
            if spec and spec.get("provider") == self.origin:
                wanted[series_id] = spec
        if not wanted:
            return {}

        # One request per base currency, each covering every quote currency
        # asked of it. Grouping this way keeps the call count at two or three
        # rather than one per pair.
        by_base: dict[str, list[tuple[str, str]]] = {}
        for series_id, spec in wanted.items():
            base, _, quote = str(spec["symbol"]).partition(">")
            if not base or not quote:
                raise FeedConfigError(
                    f"{series_id}: frankfurter symbol must be 'BASE>QUOTE', "
                    f"got {spec['symbol']!r}"
                )
            by_base.setdefault(base, []).append((series_id, quote))

        out: dict[str, Quote] = {}
        for base, pairs in by_base.items():
            symbols = ",".join(sorted({quote for _, quote in pairs}))
            series = self._timeseries(base, symbols)
            if not series:
                continue
            dates = sorted(series)
            latest = dates[-1]
            previous = dates[-2] if len(dates) > 1 else None
            for series_id, quote_ccy in pairs:
                value = series[latest].get(quote_ccy)
                if value is None:
                    continue
                spec = wanted[series_id]
                out[series_id] = Quote(
                    series_id=series_id,
                    value=float(value),
                    previous_close=(
                        float(series[previous][quote_ccy])
                        if previous and quote_ccy in series[previous]
                        else None
                    ),
                    # The ECB publishes once a day; the publication date is the
                    # honest timestamp, not the moment we happened to ask.
                    as_of=f"{latest}T16:00:00+00:00",
                    origin=f"{self.origin}:{base}{quote_ccy}",
                    live=True,
                    proxy_symbol=(
                        f"{base}{quote_ccy}" if spec.get("proxy") else None
                    ),
                    note=str(spec.get("why", "")).strip(),
                )
        return out

    def _timeseries(self, base: str, symbols: str) -> dict[str, dict]:
        start = (
            datetime.now(timezone.utc) - timedelta(days=_FX_LOOKBACK_DAYS)
        ).date().isoformat()
        url = f"{self.base_url}{start}..?base={base}&symbols={symbols}"
        try:
            return _get_json(url).get("rates", {})
        except _FETCH_ERRORS:
            return {}


class KeylessQuotes:
    """Both live providers behind one source, with snapshot fallback.

    Every series the config knows about comes back, so downstream code never has
    to ask whether a provider answered - only whether a given quote is `live`.
    """

    def __init__(self, config: dict | None = None,
                 fallback: QuoteSource | None = None) -> None:
        self.config = config or load_config()
        self.providers: list[QuoteSource] = [
            YahooQuotes(self.config),
            FrankfurterQuotes(self.config),
        ]
        self.fallback = fallback or SnapshotQuotes()

    def fetch(self, series_ids: Iterable[str]) -> dict[str, Quote]:
        wanted = list(series_ids)
        out: dict[str, Quote] = {}
        for provider in self.providers:
            out.update(provider.fetch(wanted))

        # Anything the live providers could not answer - a declared gap, a
        # timeout, a symbol that stopped resolving - degrades to the snapshot
        # and carries the reason with it.
        missing = [series_id for series_id in wanted if series_id not in out]
        if missing:
            gaps = self.config.get("gaps", {})
            for series_id, quote in self.fallback.fetch(missing).items():
                reason = str(gaps.get(series_id, "")).strip()
                out[series_id] = Quote(
                    series_id=quote.series_id,
                    value=quote.value,
                    previous_close=None,
                    as_of=quote.as_of,
                    origin=quote.origin,
                    live=False,
                    note=reason or "no live quote available at refresh time",
                )
        return out


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

SOURCE_ENV = "ANCHORLINE_PRICE_SOURCE"


def quote_source(name: str | None = None) -> QuoteSource:
    """Snapshot unless explicitly told otherwise.

    The default matters: `make build` is expected to be reproducible, and the
    goldens in tests/ are pinned to four decimals. A build that silently reached
    for the network would make them a function of the morning's tape.
    """
    import os

    choice = (name or os.environ.get(SOURCE_ENV) or "snapshot").strip().lower()
    if choice == "snapshot":
        return SnapshotQuotes()
    if choice == "live":
        return KeylessQuotes()
    raise ValueError(f"{SOURCE_ENV}={choice!r}; expected 'snapshot' or 'live'")


def fetch_all(source: QuoteSource | None = None,
              config: dict | None = None) -> dict[str, Quote]:
    """Every series the feed map knows about, quotable or not."""
    config = config or load_config()
    source = source or quote_source()
    series_ids = list(config["series"]) + list(config.get("gaps", {}))
    return source.fetch(series_ids)
