# Crypto Futures Boards Design

## Goal

Add two Binance USD-M futures turnover boards while keeping the existing spot
crypto board unchanged.

## Boards

- `CRYPTO_TURNOVER_TOP50`: existing Binance Spot crypto turnover board. Keep the
  current API name, frontend tab, cron, and filtering behavior.
- `CRYPTO_FUTURES_TURNOVER_TOP50`: Binance USD-M futures turnover Top 50 across
  active USDT-margined perpetual contracts.
- `CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50`: Binance USD-M futures turnover Top 50
  limited to TradeFi / stock-like futures contracts.

## Data Sources

- Use Binance Spot `/api/v3/ticker/24hr` for the existing spot board.
- Use Binance USD-M Futures `/fapi/v1/ticker/24hr` for futures 24h price,
  volume, and quote volume.
- Use Binance USD-M Futures `/fapi/v1/exchangeInfo` for futures symbol metadata
  and category detection when category fields are available.

## Instrument Model

Store futures instruments separately from spot instruments:

- Spot remains `market = 'CRYPTO'`, `instrument_type = 'crypto'`.
- Futures use `market = 'CRYPTO_FUTURES'`, `instrument_type = 'crypto_futures'`.
- Futures symbols may match spot symbols, so the distinct market value prevents
  key collisions in the existing `(market, symbol)` uniqueness rule.

Futures instruments should keep useful metadata in `extra_meta`, including
contract type, margin asset, underlying type, and underlying subtype when
Binance provides it.

## Ranking Logic

The futures total board ranks all active USD-M perpetual contracts by 24h
`quoteVolume` descending.

The futures TradeFi board ranks only the futures instruments identified as
TradeFi / stock-like by the futures universe filter. The filter should prefer
Binance metadata when it clearly identifies the category. If Binance metadata
does not expose a reliable category field, use a small local allowlist as a
fallback. The allowlist is only a universe filter; prices, volumes, and ranks
still come from Binance futures 24h ticker data.

The existing spot stablecoin ranking exclusions stay scoped to `market =
'CRYPTO'`. They must not unintentionally hide futures instruments unless the
futures-specific filter asks for that.

## CLI And Jobs

Add a futures board sync command that:

1. Fetches futures exchange metadata.
2. Fetches futures 24h ticker data.
3. Upserts futures instruments and market snapshots.
4. Refreshes both futures ranking boards for the same snapshot timestamp.

The command should not alter spot board behavior and should be suitable for a
cron cadence similar to the existing lightweight spot board sync.

## API And Frontend

No schema migration is required for new board names. Existing board APIs can
serve both new boards through `/api/boards/{board_name}` once the new
`ranking_snapshot` rows exist.

The frontend should add two tabs:

- Futures
- TradeFi

The existing Crypto tab remains mapped to `CRYPTO_TURNOVER_TOP50`.

## Error Handling

If futures metadata fetch fails, the sync job should fail visibly through
`job_state` and `source_health`; it should not silently refresh partial boards.

If the TradeFi metadata filter finds no symbols, the job should fall back to the
configured local allowlist. If both metadata and allowlist produce no symbols,
refresh the total futures board and leave the TradeFi board empty with a
successful job result only if this state is explicit in logs/metadata.

## Testing

Add tests for:

- Parsing futures 24h ticker snapshots into `MarketSnapshot`.
- Futures instruments using `CRYPTO_FUTURES` so they do not collide with spot
  symbols.
- Selecting futures total Top 50 by quote volume.
- Selecting TradeFi futures by metadata and by allowlist fallback.
- CLI sync creating both futures ranking boards.
- Existing `CRYPTO_TURNOVER_TOP50` behavior remaining unchanged.
- Frontend board tabs include Crypto, Futures, and TradeFi.

## Deployment

Deploying this change requires:

- Installing the updated package on `tencent-market`.
- Adding a cron script for futures board sync.
- Running the futures sync once after deployment.
- Verifying the three board APIs return the expected board names and item
  counts.
