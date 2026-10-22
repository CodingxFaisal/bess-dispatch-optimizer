# Data dictionary

Status: Stage 2 (data contracts and validation). Applies to `gridflex.data` and `configs/default.yaml`.

All market data come from **SMARD.de**, the market-data platform of the Bundesnetzagentur (German Federal Network Agency).

> Data: **Bundesnetzagentur | SMARD.de**, licensed under **CC BY 4.0**. SMARD's download-centre data-use note states that the data may be downloaded, stored and reused free of charge under CC BY 4.0. Exports, charts and the UI must carry this attribution.

---

## 1. Time conventions

| Aspect | Convention |
|---|---|
| Timestamp identity | Timezone-aware **UTC** instant at the **start** of a market time unit (MTU). Column/index name `timestamp_utc`. |
| Display and calendar | `Europe/Berlin`. A local delivery day runs from local midnight to local midnight, so it lasts 23 h (last Sunday of March), 25 h (last Sunday of October) or 24 h. |
| Repeated local hour | On the 25 h day the local hour 02:00 occurs twice (CEST, then CET). UTC timestamps stay unique, and the local view shows both hours with their offsets. |
| Missing local hour | On the 23 h day local 02:00 does not exist. The UTC grid has no gap. |
| Window semantics | Datasets cover whole local delivery days `[start_day, end_day)`, converted to UTC bounds. |
| MTU regimes (DE-LU day-ahead) | **60 min** for delivery days 2018-10-01 to 2025-09-30. **15 min** from 2025-10-01 (SDAC 15-minute MTU go-live, trading day 2025-09-30). A dataset must lie inside a single regime at that regime's resolution; a mixed or mismatched request raises `MarketRegimeError`. |

### Why hourly prices after 2025-10-01 are rejected

From 2025-10-01 the day-ahead auction trades 15-minute products. SMARD's `hour` resolution for series 4169 is then the arithmetic mean of four different quarter-hour prices.

This was verified on 2026-10-22. For the delivery week starting 2025-10-06, 168/168 hours had differing quarter-hour prices and the hourly value equalled their mean. For the week starting 2025-09-15, all quarter-hour values simply repeated the hourly price.

Optimising or settling against those averages would understate spreads, so such requests are refused.

---

## 2. Series

Configured under `data.series` in `configs/default.yaml`. "Raw unit" is what SMARD serves; "clean unit" is what `gridflex.data` returns.

| Key | SMARD ID | Description | Nature | Raw unit | Clean unit | Coverage (SMARD index) | Public for delivery day D from (Europe/Berlin) |
|---|--:|---|---|---|---|---|---|
| `price` | 4169 | Day-ahead price, DE-LU bidding zone | realised | EUR/MWh | EUR/MWh | from 2018-10-01 | D−1 13:00 |
| `load_forecast` | 411 | Day-ahead forecast of total grid load (Netzlast), Germany | forecast | MWh per interval | MW (mean) | from 2014-12-29 | D−1 10:00 |
| `solar_forecast` | 125 | Day-ahead forecast of PV generation, Germany | forecast | MWh per interval | MW (mean) | from 2014-12-29 | D−1 18:00 |
| `wind_onshore_forecast` | 123 | Day-ahead forecast of onshore wind generation | forecast | MWh per interval | MW (mean) | from 2014-12-29 | D−1 18:00 |
| `wind_offshore_forecast` | 3791 | Day-ahead forecast of offshore wind generation | forecast | MWh per interval | MW (mean) | from 2014-12-29 | D−1 18:00 |

Both `hour` and `quarterhour` resolutions exist for every series listed.

### Units: energy per interval, not MW

SMARD reports generation and load as **energy per MTU in MWh**. This was verified on 2026-10-22 for 411, 125, 123, 3791 and 410: over 48 hours in June 2024, the four quarter-hour values summed to exactly the hourly value (mean ratio 1.0000). The opt-in test `tests/data/test_smard_live.py` re-checks this.

An hourly value therefore equals the mean MW for that hour numerically, but a quarter-hour value is **one quarter** of the mean MW. Cleaning converts every energy series to mean MW:

```
mean_power_MW = energy_MWh_per_interval / interval_hours      (interval_hours = 1 or 0.25)
```

The v0.1 code labelled these series "MW". That was numerically correct only at hourly resolution. Prices are not volumes, and their quarter-hour values are not divided.

### Availability rules and sources

The publication times come from the rules and observations below; they are configured per series as `availability: {day_offset, local_time, source}`. The day-ahead decision for delivery day D is taken at **D−1 12:00** (gate closure).

| Series | Rule | Basis |
|---|---|---|
| price | D−1 13:00 | SDAC results are normally published around 12:45 CET on D−1. 13:00 is used as the cut-off. Delayed or decoupled auctions are not modelled. At the decision for D, prices are known **through D−1**. |
| load_forecast | D−1 10:00 | ENTSO-E Regulation 543/2013 art. 6.1(b): no later than 2 h before day-ahead gate closure. Observed: the SMARD file covering D+1 was created 08:00 UTC (10:00 CEST) on 2026-10-22. |
| solar / wind forecasts | D−1 18:00 | ENTSO-E art. 14.1(d): day-ahead wind/solar forecast "no later than 18.00 Brussels time, one day before". Observed: the SMARD file was created 16:07 UTC (18:07 CEST) on 2026-10-21; at 13:50 UTC on 2026-10-22 no values for 23 Oct existed. **These forecasts are not available at gate closure and are excluded from day-ahead decision inputs** (audit D1). |

`gridflex.data.availability` implements these rules:

- `decision_time(market, D)` gives the UTC instant of gate closure for D.
- `available_from(rule, D, tz)` gives the UTC instant from which values for D are public.
- `latest_available_delivery_day(rule, at, tz)` gives the most recent delivery day whose values are public at instant `at`.
- `assert_available(cfg, key, index, at)` raises `InformationLeakageError` when the index contains data that was not yet public at `at`.
- `day_ahead_usable_series(cfg, D)` returns the series usable as delivery-day inputs. With the defaults that is `["load_forecast"]`.

**Vintage limitation.** SMARD stores one (final) version of each forecast. The rules describe *when* a forecast is published, not *which version* was visible then. Any later revision of a stored value cannot be detected or undone with SMARD data.

---

## 3. Retrieval, raw preservation and caching

### Endpoints

Verified 2026-10-22:

```
https://www.smard.de/app/chart_data/{id}/{region}/index_{resolution}.json
    -> {"timestamps": [epoch_ms, ...]}                    weekly chunk starts
https://www.smard.de/app/chart_data/{id}/{region}/{id}_{region}_{resolution}_{chunk_ms}.json
    -> {"meta_data": {"version", "created"}, "series": [[epoch_ms, value|null], ...]}
```

`region` is `DE`. Chunks start at local Monday 00:00 and therefore hold 167, 168 or 169 hourly points; a chunk ends where the next one starts. `null` marks intervals without data, such as the future part of the current week.

### HTTP behaviour

Configured under `data.http`:

- Per-request timeout (`timeout_s`, default 30 s).
- At most `1 + max_retries` attempts (default 4) on timeouts, connection errors and HTTP 429/500/502/503/504, with exponential backoff `backoff_s · 2^(attempt−1)` capped at `backoff_max_s`.
- HTTP 404 raises `SourceNotFoundError`; other 4xx statuses fail immediately.
- Requests are paced at least `min_interval_s` apart.

### Raw cache

Default location `data/cache/`, git-ignored:

```
data/cache/smard/<region>/<id>/<resolution>/index.json          raw bytes as served
data/cache/smard/<region>/<id>/<resolution>/index.meta.json     provenance sidecar
data/cache/smard/<region>/<id>/<resolution>/chunk_<ms>.json
data/cache/smard/<region>/<id>/<resolution>/chunk_<ms>.meta.json
```

Each sidecar records:

- `url` and `retrieved_at_utc`;
- `sha256` of the payload;
- `status_code` and `attempts`;
- `n_points` and `n_null`;
- SMARD's own `created` stamp (`source_created_utc`).

A payload whose hash no longer matches its sidecar raises `DataIntegrityError`. Payloads are validated before they are cached, so malformed responses are never stored.

### Cache invalidation policy

Configured under `data.cache`:

| Object | Re-downloaded when |
|---|---|
| Index | older than `index_max_age_minutes` (default 60) |
| *Final* chunk: no nulls, and retrieved ≥ `finalisation_lag_days` (default 14) after the chunk ended | never, unless `refresh=True` |
| Any other chunk | older than `open_chunk_max_age_minutes` (default 360) |

Consequence: if SMARD revises a final chunk, the revision is only picked up with `refresh=True` (`scripts/download_data.py --refresh`).

### Offline mode

Set `data.offline: true`, or pass `--offline` / `offline=True`. Offline mode makes no network requests at all:

- cached files are used whatever their age;
- expired non-final chunks are flagged `stale` in the provenance and logged;
- a missing file raises `DataUnavailableError` naming the first missing chunk.

The committed test fixture is a complete offline cache (§6).

---

## 4. Cleaning

`gridflex.data.clean`. Cleaning does three things and nothing else:

1. **Duplicates.** Identical repeated timestamps are collapsed and counted. Conflicting repeats raise `DataIntegrityError`.
2. **Ordering.** Values are sorted by UTC timestamp.
3. **Units.** Energy per interval is converted to mean MW (§2). Prices are unchanged.

Nulls remain NaN. **Nothing is interpolated, forward-filled, back-filled or replaced with zero.** The v0.1 behaviours that did this are removed: dropping null points (`smard.py` L108), dropping NaN-price rows (L182), and `ffill().bfill()` on features (L184).

---

## 5. Validation

`gridflex.data.validate.validate_series` checks a cleaned series against the complete UTC grid of the window and returns a `ValidationReport`. It never modifies data.

| Code | Severity | Meaning |
|---|---|---|
| `INDEX_NOT_UTC` | error | index is not tz-aware UTC |
| `NOT_SORTED` | error | timestamps not increasing |
| `DUPLICATE_TIMESTAMPS` | error | repeated timestamps remain |
| `DUPLICATES_COLLAPSED` | info | identical repeats removed during cleaning (count) |
| `OFF_GRID_TIMESTAMPS` | error | timestamp outside the window or not on the MTU grid |
| `RESOLUTION_MISMATCH` | error | observed interval length differs from the regime's MTU |
| `MISSING_INTERVALS` | error for price, warning otherwise | expected interval absent |
| `NULL_VALUES` | error for price, warning otherwise | interval present but null at the source |
| `INCOMPLETE_DAYS` | error for price, warning otherwise | local delivery days with missing or null intervals |
| `BELOW_MINIMUM` / `ABOVE_MAXIMUM` | error | value outside the plausibility envelope |
| `DST_DAYS` | info | 23 h / 25 h days in the window |

**Plausibility envelopes:**

- **Price:** the SDAC harmonised clearing-price limits in force on each delivery day (`market.price_limits`):
  - −500 / +3,000 EUR/MWh from 2018-10-01;
  - −500 / +4,000 from 2022-05-10;
  - −600 / +4,000 from delivery 2026-05-29.
  
  Sources disagree on whether the maximum rose to +5,000 from 2022-09-20. The NEMO Committee list says it did; EPEX SPOT (13 Sep 2022) says it "remains at 4,000". The +4,000 value is kept and flagged as unresolved. An out-of-range price is reported as an error for a person to check, never clipped.
- **Energy series:** `validation_min: 0`.

`build_market_dataset(..., strict=True)` (the default) raises `DataValidationError` if any report contains an error. With `strict=False` the dataset is returned with NaN left in place, together with the failing reports.

---

## 6. Datasets and the committed fixture

`build_market_dataset(cfg, client, start_day, end_day, series=..., resolution=None, strict=True, refresh=False)` returns a `MarketDataset`:

- `frame`: one column per series key, on the complete UTC grid (NaN where the source has no value).
- `local_frame()`: the same data with the index in Europe/Berlin.
- `metadata`: per-series provenance (SMARD ID, units, resolution, number of chunks downloaded/stale, retrieval time range, latest SMARD `created` stamp, duplicates collapsed, availability rule, attribution, licence).
- `reports`: one validation report per series.
- `provenance()`: all of the above as JSON. The scripts write it to `runs/latest/data_provenance_<tag>.json`.

**Test fixture** (`tests/fixtures/smard_cache/`, 168 KB). Produced by `scripts/capture_smard_fixtures.py` through the production client on 2026-10-22, it holds raw payloads and sidecars for series 4169 and 411:

| Resolution | Local delivery days | Purpose |
|---|---|---|
| hour | 2024-03-30 → 2024-04-01 | spring DST (31 Mar = 23 h) |
| hour | 2024-10-26 → 2024-10-28 | autumn DST (27 Oct = 25 h, repeated 02:00) |
| hour | 2025-09-29 → 2025-09-30 | last hourly days before the 15-min switch |
| quarterhour | 2025-10-01 → 2025-10-03 | first 15-minute days |

`.gitattributes` marks the fixture `-text` so line-ending normalisation cannot break the stored hashes.

---

## 7. Known limitations

- Single vintage of forecasts (see §2). Publication-time rules cannot recover historical vintages.
- Price availability uses a fixed 13:00 cut-off. Auction delays, decoupling events and partial re-runs are not modelled.
- Only SMARD is integrated. ENTSO-E (vintaged forecasts) and regelleistung.net (balancing) are not yet sources.
- Final chunks are not re-validated against SMARD unless refreshed, so silent historical revisions can go unnoticed.
- The price plausibility maximum after 2022-09-20 is unresolved between sources (+4,000 vs +5,000 EUR/MWh).
