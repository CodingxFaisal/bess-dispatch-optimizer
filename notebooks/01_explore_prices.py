# ---
# Exploratory script for inspecting the price data. Open in Jupyter or VS Code
# as a notebook (the `# %%` markers define cells), or run it directly:
#     uv run python notebooks/01_explore_prices.py
# It looks at the day-ahead price data behind the optimizer: the daily shape,
# the negative-price hours that make the binary variable necessary, and the
# price duration curve that arbitrage revenue comes from.
#
# Data: Bundesnetzagentur | SMARD.de, CC BY 4.0.
# ---

# %% imports + path bootstrap
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib.pyplot as plt  # noqa: E402

from gridflex.config import load_config  # noqa: E402
from gridflex.data import SmardClient, build_market_dataset  # noqa: E402

cfg = load_config(ROOT / "configs" / "default.yaml")
client = SmardClient.from_config(cfg.data, cache_dir=ROOT / cfg.data.cache_dir)

# %% load one representative year (raw payloads cached after first download)
# PV and wind forecasts are loaded here for *exploration only*: they are
# published after the day-ahead gate closure and must not drive bids (audit D1).
ds = build_market_dataset(
    cfg,
    client,
    dt.date(2024, 1, 1),
    dt.date(2025, 1, 1),
    series=(
        "price",
        "load_forecast",
        "solar_forecast",
        "wind_onshore_forecast",
        "wind_offshore_forecast",
    ),
)
df = ds.local_frame()
print(df["price"].describe().round(1))
print("negative-price hours:", int((df["price"] < 0).sum()))

# %% average intraday price shape by season -- the arbitrage opportunity
df = df.assign(hour=df.index.hour, month=df.index.month)
summer = df[df["month"].isin([6, 7, 8])].groupby("hour")["price"].mean()
winter = df[df["month"].isin([12, 1, 2])].groupby("hour")["price"].mean()
ax = summer.plot(label="summer (solar-driven midday dip)")
winter.plot(ax=ax, label="winter")
ax.set(
    xlabel="hour of day (Europe/Berlin)",
    ylabel="EUR/MWh",
    title="Average intraday price shape, 2024",
)
ax.legend()
plt.show()

# %% price duration curve -- how much of the year sits at extreme prices
sorted_price = df["price"].sort_values(ascending=False).reset_index(drop=True)
ax = sorted_price.plot()
ax.axhline(0, color="r", lw=0.8)
ax.set(xlabel="hours (sorted)", ylabel="EUR/MWh", title="Price duration curve, 2024")
plt.show()

# %% price vs residual load -- the merit-order effect (exploration only)
residual = (
    df["load_forecast"]
    - df["solar_forecast"]
    - df["wind_onshore_forecast"]
    - df["wind_offshore_forecast"]
)
ax = df.assign(residual_load_mw=residual).plot.scatter(
    x="residual_load_mw", y="price", s=3, alpha=0.2
)
ax.set(
    xlabel="residual load forecast (MW)",
    ylabel="EUR/MWh",
    title="Price vs residual load forecast, 2024",
)
plt.show()
