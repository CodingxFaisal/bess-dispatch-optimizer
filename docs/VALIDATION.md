# Validation record

Every entry lists the commands actually run and the results observed. Nothing here is projected or assumed.

---

## Stage 2: configuration, data contracts and validation (2026-10-22)

**Environment:**

- Windows 11, CPython 3.12.10, uv 0.11.21.
- Dependencies from `uv.lock`: pydantic 2.14.1, pandas 3.1.0, numpy 2.5.4, PuLP 3.3.2, scikit-learn 1.9.1, pytest 9.1.1.
- Branch `gridflex/stage-2-data-contracts`, uncommitted working tree.

### Automated checks (working tree)

| Command | Result |
|---|---|
| `uv run pytest` | **127 passed**, 5 deselected (network) |
| `uv run pytest -m network` | **5 passed** (live smard.de contract) |
| `uv run pytest --cov=gridflex` | 88 % total line coverage. `config/*` 100 %; `data/*` 90–100 % (timeutils 90, availability 97, cache 97, dataset 97, smard 98, validate 99, others 100); v0.1 `analysis/*` 0 % (rewritten in Stage 7) |
| `uv run ruff check src tests scripts notebooks` | All checks passed |
| `uv run ruff format --check src tests scripts notebooks` | 47 files already formatted |
| `uv run mypy` | Success, 24 source files. Stricter per-module flags apply to `gridflex.config.*` and `gridflex.data.*` |

### Clean-environment checks

The tracked and untracked (non-ignored) files were copied to an empty directory, and both installation routes were run:

| Route | Commands | Result |
|---|---|---|
| uv | `uv sync --locked` → ruff check, ruff format --check, mypy, pytest | all pass; **127 passed** |
| pip | `python -m venv`, `pip install -r requirements-dev.txt -e .`, pytest | **127 passed** |

**Observed pitfall.** The first attempt ran in a directory about 150 characters deep. On Windows without long-path support, scikit-learn's install then failed silently: a compiled module was missing (`_radius_neighbors_classmode`), causing 2 collection errors. With the uv route it showed up as that missing module; with pip, pydantic was not importable at all. Both routes passed from a short path. Documented in RUNNING.md.

This is not yet the spec §15 reproduction: CI on GitHub has not run (no push), and Linux and macOS were not tested.

### Test inventory added in Stage 2

| File | What it checks |
|---|---|
| `tests/test_config_models.py` (30) | default values against hand calculations (√0.86, 120000/7200, SoC MWh bounds); one-way efficiency mode; regime and price-limit lookups at boundary dates; fingerprint stability; frozen models; 21 invalid-config cases; missing/invalid YAML |
| `tests/test_dispatch_params.py` (11) | v0.1 bridge reproduces v0.1 parameters; rejects asymmetric power and non-cyclic policy; invalid-parameter cases |
| `tests/data/test_http.py` (7) | timeout passed through; retry on Timeout, ConnectionError and 503; backoff sequence 1, 2, 4 s; cap; bounded attempts; no retry on 404 or 403; pacing |
| `tests/data/test_cache.py` (7) | exact-byte round trip; sidecar hash; corruption detection; index freshness; chunk finality (null and lag rules); open vs final expiry |
| `tests/data/test_smard_client.py` (19) | 169 h DST chunk boundaries; download with provenance; cache hits; index refresh while final chunks are never refetched; open chunks with nulls refetched after expiry and nulls kept as NaN; refresh; offline with no network; clear offline errors; stale flag; coverage errors; naive or reversed windows; malformed index and chunk payloads rejected and not cached |
| `tests/data/test_clean.py` (5) | identical duplicates collapsed; conflicting duplicates raise; price unchanged; MWh per interval → MW (hour ×1, quarter-hour ×4) with hand values; NaN kept |
| `tests/data/test_validate.py` (15) | complete series OK; 23/25 h days and the 100-interval quarter-hour DST day; missing, null, duplicate, unsorted, off-grid; resolution mismatch both ways; scalar and time-varying bounds; NaN not counted as a bound violation; non-UTC index; severities |
| `tests/data/test_availability.py` (10) | gate-closure instants across CET, CEST and both DST changes; publication instants; usable series = load forecast only; latest available day; leakage errors; guard that the v0.1 ML inputs are public at decision time |
| `tests/data/test_dataset_fixture.py` (10) | real SMARD fixture offline: spring 71 h and autumn 73 h windows (missing and repeated local 02:00); quarter-hour regime with independent raw ×4 check and distinct 15-min prices; regime rejection both ways; uncovered window; unknown series; zero network access; provenance and attribution; injected null price → strict failure, and with `strict=False` NaN is kept, never filled |
| `tests/data/test_smard_live.py` (5, opt-in) | live endpoints: four quarter-hour values sum to the hourly value for 411/125/123/3791; quarter-hour prices repeat the hourly price before the switch |

**Mutation spot-check** (each change reverted afterwards):

| Deliberate fault | Result |
|---|---|
| Skip the MWh → MW conversion | 2 tests fail |
| Treat chunks with nulls as final | 1 test fails |
| Move the PV publication time to 11:00 | 4 tests fail |

### Real-data run through the new data layer

Command: `uv run python scripts/run_all.py` (12 min 23 s; exit 0; outputs in `runs/latest/`, not committed).

- **Validation**, 2022-01-01 → 2024-12-31: price and load forecast **26,304/26,304** valid hourly intervals, 0 errors, all six DST days reported. Price range −500.0 to 936.3 EUR/MWh; 827 negative-price hours.
- **Equivalence with v0.1** (same strategies, same data window):

| Strategy | v0.1 committed | Stage 2 run | |
|---|--:|--:|---|
| Perfect foresight, 2023–24 | 43,782 €/MW-yr | 43,782 €/MW-yr | identical |
| Naive | 30,354 (69.3 %) | 30,354 (69.3 %) | identical |
| Seasonal naive | 32,227 (73.6 %) | 32,227 (73.6 %) | identical |
| Gradient boosting | 37,016 (84.5 %) | **30,989 (70.8 %)** | changed by design: PV/wind inputs removed (D1); equals the Stage 1 audit rerun |
| Perfect foresight 2022–24, by year | 82,924 / 38,514 / 49,170 €/MW | same | identical |
| Duration sweep 1/2/4/8 h | 23,664 / 43,782 / 69,926 / 95,522 €/MW-yr | same | identical |

These figures confirm that the data-layer rewrite did not change the inputs. **They are not GridFlex results.** The v0.1 strategies keep the open defects D2–D4 and D11–D16 until Stages 3–7.
