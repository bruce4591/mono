# Crypto Futures Kline Design

## Goal

Make the market UI treat crypto as one top-level area with three sub-tabs:
spot Crypto, Binance USD-M Futures, and Binance USD-M TradeFi. Add the missing
historical K-line, realtime WebSocket, and local aggregation path for the two
futures sub-tabs while keeping the existing spot crypto path unchanged.

## Scope

In scope:

- Keep the existing spot board and spot K-line behavior under the Crypto
  sub-tab.
- Add futures historical K-line backfill from Binance USD-M Futures REST.
- Add futures 1m K-line WebSocket collection from Binance USD-M Futures streams.
- Add futures local aggregation from 1m into 5m, 15m, 8h, and 1d bars.
- Make TradeFi use the same futures K-line storage as the futures total board;
  TradeFi is a filtered futures universe, not a separate market.
- Keep gap fill event-driven: fill missing bars when a user/API window is short
  or when the WebSocket reconnect path detects a gap. Do not add periodic
  scheduled gap fill.

Out of scope:

- Futures order book, funding rates, open interest, or liquidation data.
- Futures ticker WebSocket for sub-minute latest-price snapshots.
- New database tables or schema migrations.
- Changing the existing spot stablecoin ranking exclusions.

## UI Model

The current top-level tab row should stop presenting Crypto, Futures, and
TradeFi as peer top-level tabs. The target model is:

- Top-level tabs: ETF and Crypto.
- Crypto sub-tabs:
  - Crypto: existing spot board `CRYPTO_TURNOVER_TOP50`.
  - Futures: board `CRYPTO_FUTURES_TURNOVER_TOP50`.
  - TradeFi: board `CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50`.

Clicking a row in each sub-tab should open the existing instrument detail page
with the correct `market` and `symbol`. Spot rows continue to use
`market=CRYPTO`. Futures and TradeFi rows use `market=CRYPTO_FUTURES`.

The instrument detail page should not need a separate TradeFi market. TradeFi
rows point at the futures instrument because the K-line data is stored under
the futures market.

## Data Sources

Spot keeps the current Binance Spot sources:

- REST K-lines: `https://api.binance.com/api/v3/klines`.
- WebSocket 1m K-lines: `wss://stream.binance.com:9443`.

Futures uses Binance USD-M Futures sources:

- REST K-lines: `https://fapi.binance.com/fapi/v1/klines`.
- WebSocket 1m K-lines: `wss://fstream.binance.com`.
- Metadata and 24h ranking data stay with the existing futures board sources:
  `/fapi/v1/exchangeInfo` and `/fapi/v1/ticker/24hr`.

The futures symbols are the same Binance symbol strings already stored for
futures instruments, for example `BTCUSDT`, `ETHUSDT`, and `COINUSDT`.

## Instrument And Storage Model

No schema changes are required. Store futures bars in the existing bar tables:

- Futures instruments stay `market='CRYPTO_FUTURES'`.
- Futures instruments stay `instrument_type='crypto_futures'`.
- Futures 1m and aggregated bars use the existing `bar_intraday` table.
- Futures daily bars use the existing `bar_daily` table if the current daily
  aggregation path writes daily output there; otherwise use the existing local
  convention for crypto 1d bars.

The source values should distinguish futures from spot:

- Futures REST gap fill / history: `binance_futures_gap_fill`.
- Futures WebSocket 1m bars: `binance_futures_ws_kline`.
- Futures WebSocket price snapshots: `binance_futures_ws_kline_price`.
- Futures local aggregation: use a futures-specific source if the existing
  aggregator stores source names, for example `local_futures_aggregate`.

Spot source names must remain unchanged.

## Historical K-line Flow

Add futures equivalents to the existing spot K-line helpers:

1. Fetch USD-M futures klines from `/fapi/v1/klines`.
2. Parse rows into existing `IntradayBar` objects with UTC timestamps and the
   futures instrument id.
3. Upsert bars into `bar_intraday`.
4. Preserve Binance numeric fields as floats in the same way as spot K-lines.

The API gap-fill behavior should generalize from `market='CRYPTO'` to both:

- `CRYPTO` with spot fetcher and spot instrument mapping.
- `CRYPTO_FUTURES` with futures fetcher and futures instrument mapping.

This means opening a futures or TradeFi detail page can fill only the requested
missing window, rather than relying on a scheduled job.

## WebSocket Flow

Add a futures WebSocket collector parallel to the existing spot kline collector.
It should reuse the same collector shape where practical:

- Build combined stream URLs from symbols and interval.
- Subscribe to 1m kline streams.
- Upsert 1m bars without aggregating inside the realtime message path.
- Upsert latest-price snapshots from kline events using futures source names.
- Keep reconnect behavior and conservative stream grouping.
- Enable reconnect gap fill in the deploy script, still only after a reconnect
  gap is detected.

The futures WebSocket service should subscribe to the current top futures
universe. A practical default is the top 60 USD-M futures by 24h quote volume,
matching the spot service size and the existing performance constraint. TradeFi
does not need its own WebSocket service because TradeFi symbols are part of the
futures market. If a TradeFi symbol is not in the top 60 futures universe, its
detail page can still fill the requested K-line window through REST.

## Aggregation Flow

Generalize the local aggregation path so it can aggregate either spot or
futures 1m bars:

- Inputs: market, instrument type, symbols, and source-unrestricted 1m bars.
- Outputs: 5m, 15m, 8h, and 1d bars for the same market/instrument ids.
- Incremental behavior: start after the last existing target bar and only write
  bars that can be derived from local 1m rows.
- Do not aggregate inside WebSocket message handling.

Deploy separate cron entries or scripts for spot and futures aggregation. The
futures aggregation cadence should mirror the current spot approach:

- 5m aggregation effectively every 5 minutes.
- 15m / 8h / 1d aggregation handled by the same incremental command without
  rewriting large history windows.

The important constraint is that aggregation reads local 1m data and writes only
new target bars after the latest available target bar. It must not refetch REST
history on a timer.

## Gap Fill Policy

Do not add scheduled gap fill for futures. Missing data should be filled only
when one of these happens:

- A detail API request asks for a window and local bars are absent or short.
- The futures WebSocket reconnect path sees a time gap between the last handled
  bar and the next received bar.

If a REST fill encounters database locks, keep the write windows small enough
that the WebSocket writer is not blocked for long. If lock evidence appears in
production, split large fills into smaller per-symbol or per-chunk transactions
before adding broader retry behavior.

## Performance And Limits

Keep the futures implementation close to the current spot limits:

- Default WebSocket universe: top 60 futures symbols.
- Keep one combined stream connection unless stream count grows beyond the
  existing safe limit.
- Use Binance USD-M REST only for targeted history or gap windows.
- Avoid REST pulls for every futures ranking sync.
- Avoid a separate TradeFi collector unless production evidence shows top-60
  coverage is not enough.

The futures board sync remains lightweight and independent from K-line history.

## Deployment

Add deploy artifacts parallel to the existing spot scripts:

- A futures WebSocket systemd service.
- A futures WebSocket run script with `--gap-fill-on-reconnect`.
- A futures aggregation script and cron entry.

The existing futures ranking cron remains separate. It updates 24h ranking and
snapshot data; it should not fetch K-lines or aggregate bars.

After deployment:

1. Start or restart the futures WebSocket service.
2. Run futures aggregation once.
3. Open/query a futures symbol such as `ETHUSDT` with `market=CRYPTO_FUTURES`.
4. Open/query a TradeFi symbol such as `COINUSDT` with
   `market=CRYPTO_FUTURES`.
5. Confirm API windows return recent 1m bars and aggregated 5m/15m bars.

## Error Handling And Observability

Log futures WebSocket lifecycle events with futures-specific wording so spot and
futures logs are easy to distinguish:

- connecting
- opened
- closed
- reconnect scheduled
- message skipped because database locked
- reconnect gap check started and done

Futures REST history and aggregation commands should record job/source health
using distinct job names. They should fail visibly rather than silently
refreshing partial futures K-line data.

## Testing

Add focused tests for:

- Futures REST kline fetching URL and parsing into futures `IntradayBar`.
- Futures range sync writes bars under `market='CRYPTO_FUTURES'`.
- Futures detail API gap fill uses the futures fetcher and futures instrument.
- Futures WebSocket kline event writes `binance_futures_ws_kline` bars.
- Futures WebSocket reconnect gap fill uses the futures REST range fill path.
- Futures aggregation writes 5m, 15m, 8h, and 1d bars without touching spot
  bars.
- Frontend top-level Crypto tab exposes Crypto, Futures, and TradeFi sub-tabs.
- Existing spot Crypto K-line tests continue to pass unchanged.

## Acceptance Criteria

- The board UI shows ETF as one top-level area and Crypto as one top-level area
  with Crypto, Futures, and TradeFi sub-tabs.
- Futures and TradeFi rows open detail pages under `market=CRYPTO_FUTURES`.
- `CRYPTO_FUTURES/ETHUSDT` can return 1m, 5m, 15m, 8h, and 1d bars.
- A TradeFi symbol such as `CRYPTO_FUTURES/COINUSDT` can return the same
  intervals.
- Futures WebSocket logs show stable open/ping behavior and use futures source
  names when writing bars.
- No periodic futures REST gap-fill cron is added.
- Existing spot Crypto board, spot K-lines, spot WebSocket, and spot aggregation
  behavior remain unchanged.
