# ---
# Exploratory script for inspecting the price data. Open in Jupyter or VS Code
# as a notebook (the `# %%` markers define cells), or run it directly:
#     python notebooks/01_explore_prices.py
# It looks at the day-ahead price data behind the optimizer: the daily shape,
# the negative-price hours that make the binary variable necessary, and the
# price duration curve that arbitrage revenue comes from.
# ---

# %% imports + path bootstrap
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib.pyplot as plt

from bess.config import load_config
from bess.data import build_dataset

cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "battery.yaml")

# %% load one representative year (cached after first download)
df = build_dataset(cfg, "2024-01-01", "2025-01-01",
                   cache_dir=Path(__file__).resolve().parents[1] / "data" / "raw")
print(df["price"].describe().round(1))
print("negative-price hours:", int((df["price"] < 0).sum()))

# %% average intraday price shape by season -- the arbitrage opportunity
df = df.assign(hour=df.index.hour, month=df.index.month)
summer = df[df["month"].isin([6, 7, 8])].groupby("hour")["price"].mean()
winter = df[df["month"].isin([12, 1, 2])].groupby("hour")["price"].mean()
ax = summer.plot(label="summer (solar-driven midday dip)")
winter.plot(ax=ax, label="winter")
ax.set(xlabel="hour of day", ylabel="EUR/MWh", title="Average intraday price shape")
ax.legend()
plt.show()

# %% price duration curve -- how much of the year sits at extreme prices
sorted_price = df["price"].sort_values(ascending=False).reset_index(drop=True)
ax = sorted_price.plot()
ax.axhline(0, color="r", lw=0.8)
ax.set(xlabel="hours (sorted)", ylabel="EUR/MWh", title="Price duration curve, 2024")
plt.show()

# %% price vs residual load -- the fundamental driver the ML model exploits
ax = df.plot.scatter(x="residual_load_forecast", y="price", s=3, alpha=0.2)
ax.set(title="Price vs residual load forecast (the merit-order effect)")
plt.show()
