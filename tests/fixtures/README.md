# Test fixtures

## `smard_cache/`

A raw SMARD cache in the layout `gridflex.data.cache.RawCache` uses. Payloads are
byte-for-byte as served by smard.de, each with a provenance sidecar
(`*.meta.json`: URL, retrieval time, SHA-256, point and null counts).

- **Source:** Bundesnetzagentur | SMARD.de, licensed **CC BY 4.0**
  (<https://www.smard.de/en/downloadcenter/download-market-data/>).
- **Retrieved:** 2026-10-22 by `scripts/capture_smard_fixtures.py`, using the
  production client.
- **Contents:** series 4169 (DE-LU day-ahead price) and 411 (load forecast).
  - Hourly: chunks containing 2024-03-30 to 2024-04-01 (spring DST),
    2024-10-26 to 2024-10-28 (autumn DST), and 2025-09-29 to 2025-09-30.
  - Quarter-hourly: 2025-10-01 to 2025-10-03 (after the 15-minute switch).
  - Full chunk indexes for both resolutions.
- **Modifications:** none. Tests that need defective data copy the fixture to a
  temporary directory and alter the copy there.

`.gitattributes` marks this directory `-text` so that line-ending normalisation
cannot change the bytes, which would break the stored SHA-256 hashes.
