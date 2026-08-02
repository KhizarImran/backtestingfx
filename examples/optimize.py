"""
Grid-search an SMA crossover over fast/slow periods.

The strategy is written as a *signal function* rather than a Strategy subclass:
it returns the target lot size for every bar in one vectorised pass. That runs
once per parameter combination, so the per-bar work happens entirely in Rust
and the whole grid runs on parallel threads.
"""

import time

import numpy as np
import pandas as pd

from backtestingfx import Backtest

LOT = 0.1


def sma_cross(df, fast, slow):
    """Long LOT lots while the fast SMA is above the slow one, otherwise flat."""
    fast_sma = df["close"].rolling(fast).mean()
    slow_sma = df["close"].rolling(slow).mean()
    # NaN during warmup compares False, so the untradeable head comes out flat
    return np.where(fast_sma > slow_sma, LOT, 0.0)


df = pd.read_csv("data/EURUSD_1H.csv")

backtest = Backtest(df, cash=10_000, commission=3.5, spread=0.00002)

fast_range = range(5, 26)
slow_range = range(30, 101, 5)
combos = len(fast_range) * len(slow_range)

start = time.perf_counter()
results = backtest.optimize(
    sma_cross,
    maximize="total_return_pct",
    fast=fast_range,
    slow=slow_range,
)
elapsed = time.perf_counter() - start

print(f"{combos} combinations over {len(df):,} bars in {elapsed:.2f}s "
      f"({combos * len(df) / elapsed / 1e6:.1f}M bar-sims/sec)\n")

print(f"{'fast':>6}{'slow':>6}{'return %':>12}{'trades':>9}{'win %':>8}{'max dd %':>10}")
print("-" * 51)
for params, stats in results[:10]:
    print(
        f"{params['fast']:>6}{params['slow']:>6}{stats.total_return_pct:>12.2f}"
        f"{stats.num_trades:>9}{stats.win_rate_pct:>8.1f}{stats.max_drawdown_pct:>10.2f}"
    )

best_params, best_stats = results[0]
print(f"\nBest: {best_params}")
print(best_stats)
