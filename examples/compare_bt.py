"""
Compares our SMA crossover logic against backtesting.py using the same data,
same SMA periods, and zero commission/spread so only entry/exit logic is tested.

Trade count and win rate should match between both libraries.
Dollar PnL will differ because the sizing models are different:
  - backtesting.py: buys fractional units based on available cash
  - backtestingfx:  fixed lot size (0.1 lots = 10,000 units)
"""

import pandas as pd
from backtesting import Backtest as BtBacktest, Strategy as BtStrategy
from backtestingfx import Backtest, Strategy

FAST = 10
SLOW = 50


# ── backtesting.py ────────────────────────────────────────────────────────────

class SmaCrossBt(BtStrategy):
    fast = FAST
    slow = SLOW

    def init(self):
        pass

    def next(self):
        if len(self.data.Close) < self.slow:
            return
        fast_sma = self.data.Close[-self.fast:].mean()
        slow_sma = self.data.Close[-self.slow:].mean()

        if not self.position:
            if fast_sma > slow_sma:
                self.buy()
        else:
            if fast_sma < slow_sma:
                self.position.close()


# ── backtestingfx ─────────────────────────────────────────────────────────────

class SmaCrossFx(Strategy):
    fast = FAST
    slow = SLOW

    def next(self):
        if self.index < self.slow:
            return
        closes = [b.close for b in self.data[-self.slow:]]
        fast_sma = sum(closes[-self.fast:]) / self.fast
        slow_sma = sum(closes) / self.slow

        if not self.positions:
            if fast_sma > slow_sma:
                self.buy(0.1)
        else:
            if fast_sma < slow_sma:
                self.close_all()


# ── run both ──────────────────────────────────────────────────────────────────

df_raw = pd.read_csv("data/EURUSD_1H.csv")

# backtesting.py needs a DatetimeIndex and capitalised column names
df_bt = df_raw.copy()
df_bt["timestamp"] = pd.to_datetime(df_bt["timestamp"], utc=True)
df_bt = df_bt.set_index("timestamp")
df_bt.index = df_bt.index.tz_localize(None)
df_bt = df_bt.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})

bt_result = BtBacktest(df_bt, SmaCrossBt, cash=10_000, commission=0).run()

fx_result = Backtest(df_raw, SmaCrossFx, cash=10_000, commission=0, spread=0).run()

# ── compare ───────────────────────────────────────────────────────────────────

print("=" * 45)
print(f"{'Metric':<20} {'backtesting.py':>12} {'backtestingfx':>12}")
print("=" * 45)
print(f"{'Trades':<20} {bt_result['# Trades']:>12} {fx_result.num_trades:>12}")
print(f"{'Win Rate %':<20} {bt_result['Win Rate [%]']:>12.1f} {fx_result.win_rate_pct:>12.1f}")
print(f"{'Return %':<20} {bt_result['Return [%]']:>12.2f} {fx_result.total_return_pct:>12.2f}")
print(f"{'Max Drawdown %':<20} {bt_result['Max. Drawdown [%]']:>12.2f} {fx_result.max_drawdown_pct:>12.2f}")
print("=" * 45)
print()
print("Note: Return % differs because backtesting.py sizes by available cash,")
print("      backtestingfx uses fixed 0.1 lots. Trade count + win rate should match.")
