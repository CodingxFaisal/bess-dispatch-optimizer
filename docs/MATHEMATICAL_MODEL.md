# Mathematical model

Status: complete for the physics (§2, Stage 3) and the optimisation model (§3, Stage 4). §2 is implemented in `gridflex.battery`, §3 in `gridflex.optimization`. The v0.1 MILP (`gridflex.optimize.dispatch`) is still used by the v0.1 backtest until Stage 6 replaces it.

---

## 1. Notation and units

| Symbol | Meaning | Unit | Code |
|---|---|---|---|
| $t = 0,\dots,T-1$ | interval index within a horizon | | |
| $\Delta t_t$ | duration of interval $t$; may differ between intervals | h | `Schedule.dt_hours` |
| $P^{ch}_t,\ P^{dis}_t \ge 0$ | charge / discharge power at the **AC grid connection**, constant over $t$ | MW | `charge_mw`, `discharge_mw` |
| $E_t$ | stored energy at the **end** of interval $t$ | MWh | `energy_trajectory` |
| $E_{start}$ | stored energy at the start of the horizon | MWh | `e_start_mwh` |
| $\eta^{ch},\ \eta^{dis} \in (0,1]$ | **one-way** charge / discharge efficiency | – | `eta_charge`, `eta_discharge` |
| $\bar P^{ch},\ \bar P^{dis}$ | rated power limits | MW | `charge_power_mw`, `discharge_power_mw` |
| $a_t \in [0,1]$ | availability factor (scalar config value or per-interval profile) | – | `availability` |
| $E^{nom}$ | nameplate energy capacity | MWh | `energy_capacity_mwh` |
| $\underline E,\ \overline E$ | state-of-charge bounds | MWh | `soc_min_mwh`, `soc_max_mwh` |
| $E^{use} = \overline E - \underline E$ | usable (cyclable) energy | MWh | `usable_energy_mwh` |

Timestamps are UTC interval starts. A local delivery day has 23, 24 or 25 hourly intervals, or 92, 96 or 100 quarter-hour intervals. $\Delta t_t$ is derived from timestamps (`interval_hours_from_index`), never assumed.

## 2. Battery physics

### 2.1 State equation

$$
E_t = E_{t-1} + \eta^{ch} P^{ch}_t \Delta t_t - \frac{P^{dis}_t \Delta t_t}{\eta^{dis}}, \qquad E_{-1} = E_{start}
$$

This is the same structure as PyPSA's `StorageUnit` balance, with standing losses, inflow and spill set to zero.

**Efficiencies are one-way and applied once per direction.** Charging 1 MWh AC stores $\eta^{ch}$ MWh. Withdrawing $x$ MWh from storage delivers $\eta^{dis} x$ MWh AC. A full round trip therefore returns $\eta^{ch}\eta^{dis}$ = RTE of the AC energy put in. When only an RTE is known, the configuration (`efficiency.mode: round_trip_symmetric`) sets $\eta^{ch} = \eta^{dis} = \sqrt{RTE}$. The default is $\sqrt{0.86} = 0.92736$.

Powers are constant within an interval, so $E$ is piecewise linear in time. Checking the bounds at $E_{start}$ and at every $E_t$ is therefore sufficient.

### 2.2 Constraints

For every interval $t$:

| Constraint | Expression |
|---|---|
| Non-negative power | $P^{ch}_t \ge 0,\ P^{dis}_t \ge 0$ |
| Power limits (AC, derated) | $P^{ch}_t \le a_t \bar P^{ch},\quad P^{dis}_t \le a_t \bar P^{dis}$ |
| No simultaneous operation | $\min(P^{ch}_t, P^{dis}_t) = 0$ |
| State-of-charge bounds | $\underline E \le E_t \le \overline E$, and $\underline E \le E_{start} \le \overline E$ |
| Positive durations | $\Delta t_t > 0$ |

**Why simultaneous operation is forbidden.** Charging and discharging at the same time burns energy through conversion losses. A linear model does that whenever it pays: at negative prices the model is paid for the extra consumption. With the default degradation cost, that happens for prices below about −102 EUR/MWh (AUDIT §2). The optimiser in Stage 4 enforces the rule with a binary operating mode; the checker verifies it.

### 2.3 Terminal policy

Configured with `battery.terminal_soc_policy` and applied per optimisation horizon:

| Policy | Requirement | Purpose |
|---|---|---|
| `cyclic` (default) | $E_{T-1} = E_{start}$ | no net energy is bought or sold across the horizon boundary |
| `fixed` | $E_{T-1} = f^{term} E^{nom}$ | a fixed hand-over level |
| `free` | none; end energy is valued at $v^{term}$ EUR/MWh | **requires** an explicit `terminal_energy_value_eur_per_mwh`, so stored energy can never be sold off "for free" |

Under `cyclic`, $E_{start}$ is the horizon's own start state. In a rolling backtest that state must be the **physically simulated** state carried over from the previous horizon (see §2.6).

### 2.4 Throughput and equivalent full cycles

Over a schedule:

| Quantity | Definition |
|---|---|
| AC charge | $\sum_t P^{ch}_t \Delta t_t$ |
| AC discharge | $\sum_t P^{dis}_t \Delta t_t$ |
| DC charge (into storage) | $\eta^{ch} \cdot$ AC charge |
| DC discharge (out of storage) | AC discharge $/\ \eta^{dis}$ |
| Conversion losses | (AC charge − DC charge) + (DC discharge − AC discharge) $\ge 0$ |
| Energy balance identity | AC charge − AC discharge = $(E_{T-1} - E_{start})$ + losses |

**Equivalent full cycles (EFC)**, set by `battery.efc_basis`:

| Basis | EFC | Notes |
|---|---|---|
| `dc_discharge_per_usable` (default) | DC discharge / $E^{use}$ | One EFC empties the usable window once, measured at the cells. |
| `ac_discharge_per_nameplate` | AC discharge / $E^{nom}$ | The v0.1 convention. |

Both values are always reported (`Throughput.efc_dc_per_usable`, `efc_ac_per_nameplate`). The configured one drives the limit.

**Cycle limit.** EFC over a horizon $\le N_{day} \cdot H / 24$, where $N_{day}$ is `max_equivalent_full_cycles_per_day` and $H = \sum_t \Delta t_t$ is the horizon length in hours. The daily limit is pro-rated, so a 25-hour day allows $1.5 \times 25/24 = 1.5625$ EFC.

### 2.5 Degradation cost

$$
c^{deg} = \frac{1000 \cdot C^{repl}}{N^{cyc} \cdot DoD} \quad [\text{EUR/MWh}], \qquad
\text{cost} = c^{deg} \cdot Q^{deg}
$$

Here $C^{repl}$ is the replacement cost (EUR/kWh), $N^{cyc}$ the rated cycle life and $DoD$ its depth of discharge. $Q^{deg}$ is the throughput on `degradation.cost_basis`:

- **`dc_discharge`** (default): $Q^{deg}$ = DC discharge. A cell cycle-life rating counts cycles of $DoD \cdot E^{nom}$ MWh *leaving the cells*. With the defaults, $DoD \cdot E^{nom} = 0.9 \times 20 = 18$ MWh, which equals the usable window, so the DC basis and the default EFC basis are mutually consistent.
- **`ac_discharge`**: $Q^{deg}$ = AC discharge. This is the v0.1 convention, which understates cell throughput by the factor $\eta^{dis}$.

With the defaults ($C^{repl}$ = 120 EUR/kWh, $N^{cyc}$ = 8000, $DoD$ = 0.9), $c^{deg} = 16.67$ EUR per DC MWh. That is $16.67/0.92736 = 17.97$ EUR per AC MWh. These inputs are an **unsourced placeholder** carried over from v0.1; they must be replaced with a cited value before results are quoted.

Calendar ageing, temperature, C-rate and depth-of-discharge dependence (rainflow counting) are not modelled. See LIMITATIONS (Stage 9).

### 2.6 State carried between horizons

A rolling backtest must start each horizon from the energy given by the **state equation applied to the committed schedule**, not from a solver's reported SoC variable. The two differ by solver tolerances.

Stage 3 measured this on the v0.1 perfect-foresight backtest for 2023–2024 (731 days):

- within a day, the two trajectories agree to $6 \times 10^{-7}$ MWh;
- the v0.1 backtest carries the solver value, so after 731 days the accountings differ by $4.8 \times 10^{-5}$ MWh.

The amount is negligible, but the method is wrong (AUDIT D23). It is fixed in Stage 6.

### 2.7 Feasibility checking

`gridflex.battery.check_schedule` re-derives §2.1–2.4 independently of any optimiser and reports violations with stable codes:

`NON_FINITE`, `BAD_INTERVAL_DURATION`, `NEGATIVE_POWER`, `CHARGE_POWER_LIMIT`, `DISCHARGE_POWER_LIMIT`, `SIMULTANEOUS_CHARGE_DISCHARGE`, `INITIAL_ENERGY_OUT_OF_BOUNDS`, `SOC_BELOW_MIN`, `SOC_ABOVE_MAX`, `TERMINAL_ENERGY`, `CYCLE_LIMIT`.

Default tolerances are $10^{-6}$ MW and $10^{-6}$ MWh, absolute; for cycles, $10^{-6}$ MWh divided by the EFC reference energy. A schedule that fails must not be settled as an optimised result (Stage 6).

### 2.8 Not modelled (physics)

- Self-discharge and standing losses (PyPSA's $\eta^{stand}$ term).
- Auxiliary or parasitic consumption.
- Power- or SoC-dependent efficiency.
- Ramp limits.
- Separate inverter and cell power ratings (only AC limits are modelled).
- Stochastic outages: availability is a deterministic derating or a supplied profile.
- Capacity fade within the study horizon.

---

## 3. Optimisation model

### 3.1 Scope and assumptions

| Aspect | Assumption |
|---|---|
| Market | One market: the DE-LU day-ahead auction, settled at price $\pi_t$ (EUR/MWh) |
| Price formation | **Price taker**: the battery's own bids do not move $\pi_t$ |
| Commitment | The optimised schedule is a fixed physical schedule. Price-dependent bid curves are not modelled (AUDIT D16) |
| Information | $\pi_t$ is whatever the caller passes in: a forecast for forecast-driven dispatch, or realised prices for the perfect-foresight upper benchmark. The optimiser itself never sees realised prices for forecast-driven runs; Stage 6 enforces this |
| Horizon | One problem per horizon (in the backtest: one local delivery day of 23, 24 or 25 h; or 92, 96 or 100 quarter-hours) |

### 3.2 Decision variables

$P^{ch}_t, P^{dis}_t \ge 0$ (MW), $E_t$ (MWh), and the binary $u_t \in \{0,1\}$ (1 = charging allowed), for $t = 0,\dots,T-1$.

### 3.3 Objective

Maximise the expected net operating value of the horizon (EUR):

$$
\max\ \underbrace{\sum_t \pi_t \left(P^{dis}_t - P^{ch}_t\right)\Delta t_t}_{\text{market revenue}}
\;-\; \underbrace{c^{deg} \sum_t q_t P^{dis}_t}_{\text{degradation}}
\;+\; \underbrace{v^{term}\left(E_{T-1} - E_{start}\right)}_{\text{only for policy } \texttt{free}}
$$

- $q_t = \Delta t_t / \eta^{dis}$ on the DC basis, or $\Delta t_t$ on the AC basis (§2.5).
- **Signs.** Discharging sells energy and earns $\pi_t P^{dis}_t \Delta t_t$. Charging buys energy and pays $\pi_t P^{ch}_t \Delta t_t$, which is income when $\pi_t < 0$. No price is clipped or floored.
- **Terminal value.** Under `free`, the *change* in stored energy is valued, so the initial energy is never free.

### 3.4 Constraints

| Name in model | Constraint |
|---|---|
| `soc_balance_t` | $E_t = E_{t-1} + \eta^{ch} P^{ch}_t \Delta t_t - P^{dis}_t \Delta t_t / \eta^{dis}$, with $E_{-1} = E_{start}$ |
| variable bounds | $\underline E \le E_t \le \overline E$; $\ 0 \le P^{ch}_t \le a_t \bar P^{ch}$; $\ 0 \le P^{dis}_t \le a_t \bar P^{dis}$ |
| `charge_mode_t` | $P^{ch}_t \le a_t \bar P^{ch} u_t$ |
| `discharge_mode_t` | $P^{dis}_t \le a_t \bar P^{dis} (1-u_t)$ |
| `cycle_limit` (if configured) | $\sum_t r_t P^{dis}_t \le N_{day} \frac{H}{24} E^{ref}$, with $r_t$ and $E^{ref}$ from the EFC basis (§2.4) |
| `terminal_energy` | $E_{T-1} = E_{start}$ (`cyclic`) or $E_{T-1} = E^{term}$ (`fixed`) |

**The binary $u_t$ is required** so that charging and discharging never happen in the same interval. Without it, the LP relaxation profits at sufficiently negative prices by running both at once and burning energy through the losses. A test demonstrates this (one interval at −100 EUR/MWh with $\eta$ = 0.9/0.8): the LP earns 28 EUR with simultaneous operation, and the independent checker rejects that schedule.

Sequential cycling at negative prices **is** physical, and it is allowed. At a flat −30 EUR/MWh the model earns 7.5 EUR per cycle by consuming energy through its losses (hand-calculated test).

Model size per horizon: $4T$ variables ($T$ binary), $3T$ + up to 2 constraints.

### 3.5 Solver and status handling

The model is built with **PuLP** and solved with **HiGHS** (MIT licence, via `highspy`, installed by `uv sync`; no system install). The options come from the `optimization` config section:

- `mip_rel_gap` = 1e-6;
- `threads` = 1, which keeps runs deterministic;
- `time_limit_s` = 60.

CBC, bundled with PuLP, is available as `solver: cbc`.

`solve_dispatch` returns a `DispatchSolution` with a `SolveReport`. The report records:

- status, success flag and raw termination text;
- solver objective, the objective recomputed from the schedule, and the dual bound;
- relative MIP gap and runtime;
- model size, the options used, and feasibility codes.

| Status | Meaning | Schedule returned |
|---|---|---|
| `optimal` | optimality proven within `mip_rel_gap` | yes |
| `time_limit_feasible` | time limit with an incumbent | only if `accept_time_limited` and gap ≤ `max_accepted_gap` (default: no) |
| `time_limit_no_solution` | time limit, no incumbent | no |
| `infeasible` / `unbounded` | as named | no |
| `invalid_solution` | the solver's answer failed the independent physics check (§2.7), or its objective disagrees with the recomputed one | no |
| `error` | solver exception or an unclassified status | no |

**Verification of every accepted schedule.** Each candidate schedule goes through two checks before it is accepted:

1. Power values within the tolerance of zero are snapped to 0, and the schedule is passed through `check_schedule` at 1e-6 MW / 1e-6 MWh.
2. The objective is recomputed from the schedule, as revenue − degradation + terminal value, and must match the solver's value (relative 1e-6, absolute 1e-4 EUR).

**PuLP caveats found in Stage 4** (AUDIT D24, D25):

- PuLP reports a HiGHS time-limit stop *that has an incumbent* as `LpStatus "Optimal"`. The incumbent tested had a 0.49 % gap.
- PuLP raises `KeyError` for HiGHS's `kSolutionLimit`.

GridFlex therefore reads HiGHS's native model status and primal-solution status, and maps exceptions to `error`.

**Numerical precision.** On real 2023–2024 daily problems, HiGHS's schedules reproduce its SoC trajectory exactly enough to carry the state with no measurable drift. CBC's solutions, as read back through PuLP, carry about 1e-7 to 1e-5 MWh of round-off. On year-long horizons that exceeds the strict 1e-6 tolerance, which is why HiGHS is the default.

### 3.6 Not modelled (optimisation)

- Multiple markets: intraday and balancing reserves (spec §12, later).
- Uncertainty: scenarios or quantiles.
- Bid curves and market impact.
- Network constraints or fees.
- Inter-day value of energy beyond the chosen terminal policy.
