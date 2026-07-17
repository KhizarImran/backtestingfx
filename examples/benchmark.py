"""
Speed benchmark: backtesting.py vs backtestingfx, on the same SMA-cross strategy.

Three paths are timed:
  1. backtesting.py        — everything in Python
  2. backtestingfx (py)    — Rust engine + broker, but next() runs in Python each bar
  3. backtestingfx (rust)  — SMA strategy runs entirely in Rust, no per-bar Python call

The dataset is small (2000 bars), so it's tiled up to BARS to give the engines
real work. Timestamps are regenerated as a clean hourly range so backtesting.py
gets the sorted DatetimeIndex it needs.

Note: paths 1 and 2 both pay the per-bar Python cost (the strategy logic), so the
gap between them measures only the engine/broker plumbing. Path 3 shows the
engine's ceiling when nothing crosses into Python.
"""

import time

import pandas as pd
from backtesting import Backtest as BtBacktest, Strategy as BtStrategy
from backtestingfx import Backtest, Strategy
from backtestingfx import _backtestingfx as _rust

FAST = 10
SLOW = 50
LOT = 0.1
BARS = 50_000
REPEATS = 2


class SmaCrossBt(BtStrategy):
    def init(self):
        pass

    def next(self):
        if len(self.data.Close) < SLOW:
            return
        fast_sma = self.data.Close[-FAST:].mean()
        slow_sma = self.data.Close[-SLOW:].mean()
        if not self.position:
            if fast_sma > slow_sma:
                self.buy()
        elif fast_sma < slow_sma:
            self.position.close()


class SmaCrossFx(Strategy):
    def next(self):
        if self.index < SLOW:
            return
        closes = [b.close for b in self.data[-SLOW:]]
        fast_sma = sum(closes[-FAST:]) / FAST
        slow_sma = sum(closes) / SLOW
        if not self.positions:
            if fast_sma > slow_sma:
                self.buy(LOT)
        elif fast_sma < slow_sma:
            self.close_all()


def load_tiled(n):
    """Load the EURUSD CSV and tile it up to n rows with a fresh hourly index."""
    df = pd.read_csv("data/EURUSD_1H.csv")[["open", "high", "low", "close", "volume"]]
    reps = -(-n // len(df))  # ceil division
    df = pd.concat([df] * reps, ignore_index=True).iloc[:n].copy()
    df["timestamp"] = pd.date_range("2000-01-01", periods=n, freq="h")
    return df


def best_of(fn, repeats=REPEATS):
    """Run fn repeats times, return (best_seconds, result_of_first_run)."""
    best = float("inf")
    result = None
    for i in range(repeats):
        t0 = time.perf_counter()
        r = fn()
        dt = time.perf_counter() - t0
        if i == 0:
            result = r
        best = min(best, dt)
    return best, result


df = load_tiled(BARS)

# backtesting.py wants a DatetimeIndex and capitalised columns
df_bt = df.set_index("timestamp").rename(
    columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
)

# native-rust path needs Bar objects built directly
bars = [
    _rust.Bar(
        timestamp=int(ts.timestamp()),
        open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v),
    )
    for ts, o, h, l, c, v in zip(
        df["timestamp"], df["open"], df["high"], df["low"], df["close"], df["volume"]
    )
]


def run_bt():
    return BtBacktest(df_bt, SmaCrossBt, cash=10_000, commission=0).run()


def run_fx_py():
    return Backtest(df, SmaCrossFx, cash=10_000, commission=0, spread=0).run()


def run_fx_rust():
    engine = _rust.Engine(bars, 10_000.0, 0.0, 0.0, 100_000.0, 1.0)
    return engine.run_native_sma(FAST, SLOW, LOT)


print(f"Benchmarking on {BARS:,} bars, best of {REPEATS} runs...\n", flush=True)

print("  timing backtesting.py ...", flush=True)
t_bt, r_bt = best_of(run_bt)
print("  timing backtestingfx (py next) ...", flush=True)
t_py, r_py = best_of(run_fx_py)
print("  timing backtestingfx (rust) ...\n", flush=True)
t_rs, r_rs = best_of(run_fx_rust)

print("=" * 62)
print(f"{'Path':<24}{'Time (s)':>10}{'Bars/sec':>14}{'Speedup':>12}")
print("=" * 62)
for name, t in [
    ("backtesting.py", t_bt),
    ("backtestingfx (py next)", t_py),
    ("backtestingfx (rust)", t_rs),
]:
    print(f"{name:<24}{t:>10.3f}{BARS / t:>14,.0f}{t_bt / t:>11.1f}x")
print("=" * 62)

print("\nCorrectness (should roughly agree):")
print(f"{'Path':<24}{'Trades':>10}{'Return %':>12}")
print(f"{'backtesting.py':<24}{r_bt['# Trades']:>10}{r_bt['Return [%]']:>12.2f}")
print(f"{'backtestingfx (py next)':<24}{r_py.num_trades:>10}{r_py.total_return_pct:>12.2f}")
print(f"{'backtestingfx (rust)':<24}{r_rs.num_trades:>10}{r_rs.total_return_pct:>12.2f}")
print(
    "\nNote: py-next vs rust measure the same engine; the gap is the per-bar\n"
    "Python callback cost. Both use fixed lots, so Return % differs from\n"
    "backtesting.py's cash-based sizing."
)
