# Superseded: v0.1.0 results

These files are the outputs committed with v0.1.0 (commit `078d3f4`). They are
kept for traceability only. **Do not quote them.**

The Stage 1 audit ([docs/AUDIT.md](../../docs/AUDIT.md)) reproduced them
exactly, but found that:

- **The gradient-boosting results (`ml`) are invalid (D1).** The model used PV
  and wind forecasts that are published about 18:00 on D−1, after the 12:00
  day-ahead gate closure. In an audit rerun without those features, the
  capture ratio fell from 84.5 % to 70.8 %, below the seasonal-naive forecast.
- **`payback_years` is not an investment metric (D13).** It is undiscounted and
  based on revenue only.
- All figures depend on an unsourced degradation-cost assumption (D11) and a
  fixed, price-independent day-ahead schedule (D16).

Replacement results will be produced by the validated pipeline in Stages 6–7 and
documented in `docs/RESULTS.md`. Fresh runs of the v0.1 scripts now write to
`runs/latest/` (git-ignored).

Data: Bundesnetzagentur | SMARD.de, CC BY 4.0.
