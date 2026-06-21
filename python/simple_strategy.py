import os

import pandas as pd
from backtestingfx import Backtest, Strategy
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
        self.buy(0.1)


df = pd.read_csv("data/EURUSD_1H.csv")

bt = Backtest(df, BuyEveryBar, cash=10000.0, commission=0.0, spread=0.0001)
stats = bt.run()

print(stats)
