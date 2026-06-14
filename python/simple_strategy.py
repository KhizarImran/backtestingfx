import os

import pandas as pd
from backtest import Backtest, Strategy
from dotenv import load_dotenv
from lse import LSE

load_dotenv()

client = LSE(api_key=os.environ["LSE_API_KEY"])

rows = client.candles("EUR/USD", "1h", limit=2000)
df = pd.DataFrame(rows)
df.to_csv("data/EURUSD_1H.csv", index=False)


class BuyEveryBar(Strategy):
    def next(self):
        self.close_all()
        self.buy(1.0)


df = pd.read_csv("data/EURUSD_1H.csv")

bt = Backtest(df, BuyEveryBar, cash=10000.0, commission=0.0, spread=0.0001)
stats = bt.run()

print(f"Return:       {stats.total_return_pct:.2f}%")
print(f"Trades:       {stats.num_trades}")
print(f"Win Rate:     {stats.win_rate_pct:.1f}%")
print(f"Avg PnL:      {stats.avg_pnl:.5f}")
print(f"Best Trade:   {stats.best_trade:.5f}")
print(f"Worst Trade:  {stats.worst_trade:.5f}")
print(f"Max Drawdown: {stats.max_drawdown_pct:.2f}%")
print(f"Profit Factor:{stats.profit_factor:.2f}")
