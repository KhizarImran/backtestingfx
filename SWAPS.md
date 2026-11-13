# Optional swaps and overnight financing

Pass a `SwapConfig` to charge or credit positions held across rollover.
`swap=None` is the default and disables financing, calendar preparation, and
the additional rollover-calendar checks. Base [market-data validation](MARKET_DATA.md)
still applies. Existing fills and accounting stay the same.

```python
from backtestingfx import Backtest, SwapConfig

swap = SwapConfig(
	long=-7.0,
	short=2.0,
	timezone="America/New_York",
	rollover_time="17:00",
	weekday_multipliers=(1, 1, 3, 1, 1, 0, 0),
	date_multipliers={"2026-12-25": 0, "2026-12-24": 2},
)
stats = Backtest(df, MyStrategy, swap=swap).run()
print(stats.total_swap)
```

These values illustrate configuration. Supply your broker's rates and calendar.
The library does not download rates or holiday schedules.

## Rate units and configuration

`long` and `short` are signed amounts in **account currency per lot per rollover
unit**. Negative values debit cash; positive values credit it. A posting is
`rate * open_lots * multiplier`. Inputs are not percentages, swap points, or
annual interest rates. Contract size and `quote_to_account` are not applied.
Convert broker quotes to these units before configuring a backtest.

| Setting | Default | Meaning |
| --- | --- | --- |
| `long`, `short` | `0.0` | Finite signed cash rates for each direction |
| `timezone` | `"UTC"` | IANA timezone used for the rollover calendar |
| `rollover_time` | `"00:00"` | Local cutoff in exact `HH:MM` format |
| `weekday_multipliers` | `(1, 1, 3, 1, 1, 0, 0)` | Seven finite, nonnegative weights, Monday through Sunday |
| `date_multipliers` | `{}` | Local `YYYY-MM-DD` dates whose weights replace the weekday setting |

The default weights provide a Wednesday triple-rollover preset with no weekend
postings. They do not define a universal broker policy. To charge every calendar
day once, use `(1, 1, 1, 1, 1, 1, 1)`. Zero rates produce no postings but still
enable calendar validation; use `None` to disable the feature entirely.

A holiday override of zero suppresses that date. To move charges to another
date, set that date's multiplier explicitly too. There is no automatic business
day adjustment. A positive date override can enable a weekend posting.
Configuration is immutable; input and returned calendar dictionaries are copied.

## Rollover ordering and timestamps

The engine uses the bars' Unix timestamps in seconds. It does not infer candle
close times or an intrabar path. Numeric input requires explicit units; naive
datetimes mean UTC, and fractional input floors to whole seconds. The input
timezone and `SwapConfig.timezone` are separate: the latter defines the local
rollover calendar. See [timestamp rules](MARKET_DATA.md#timestamp-sources-and-ordering).
At each observed bar it:

1. Posts all scheduled rollovers since the previous observation, including any
   cutoff exactly at the current timestamp.
2. Processes existing positions' stop-loss and take-profit exits.
3. Calls the strategy or applies the target-lot signal.
4. Records equity, including financing already posted to cash.

Only positions entered **strictly before** a cutoff pay it. An entry exactly at
rollover waits until a later cutoff. An existing position closed at the cutoff,
including by a gap stop, pays before closing. Each event is posted at most once.
Checked input requires unique, strictly increasing whole-second timestamps.
No charges precede the first observation.

Missing bars do not skip calendar events. Positions are treated as held until
the next observed bar, and the ledger retains each actual rollover timestamp.
Equity reflects those charges at the next observation. Final liquidation adds
no charges beyond the last bar timestamp.

Each local date is converted independently using the timezone database embedded
in `chrono-tz`. A cutoff keeps its local time across DST instead of adding
24 UTC hours. A repeated local time uses its earlier occurrence once. An active
cutoff that does not exist during a DST gap raises `ValueError` before strategy
initialization. Choose another time or override that date with multiplier zero.
Missing cutoffs outside the observed interval do not cause an error.

Enabled swaps additionally require timestamps within Chrono's supported date
range, including the timezone offset. Duplicate timestamps are rejected by
market-data validation even when swaps are disabled. Invalid
configuration, unsupported dates, and backwards timestamps raise errors.
An overflowing posting raises an error before changing any position, cash
balance, or financing ledger for that rollover.

## Cash, partial closes, and results

The broker posts financing to cash once at rollover. Open equity includes that
cash change. Closing includes allocated financing in net trade PnL without
paying it into cash again.

`Position.swap` holds financing accrued on its remaining lots. Closing 40% of a
position carrying `-10` allocates `-4` to `Trade.swap` and leaves `-6` on the
survivor. Later rollovers charge the surviving lots. The final exit takes the
exact remaining balance.

Results expose:

- `stats.total_swap`: signed financing posted over the run.
- `stats.swap_charges`: `SwapCharge` records containing `position_id`, Unix
  `timestamp`, `lot_size`, `is_long`, `multiplier`, and signed `amount`.
- `trade.swap`: financing allocated to that exit, included in `trade.pnl`.
- Live `broker.total_swap` and `position.swap` during callbacks.

After final liquidation, within floating-point tolerance:

```text
final_cash - initial_cash == sum(trade.pnl)
total_swap == sum(trade.swap) == sum(charge.amount)
```

While positions are open, closed-trade swap plus remaining-position swap equals
the total posted financing. HTML reports show financing totals, rate/calendar
assumptions, a swap column beside net PnL, and a dated financing ledger.
See [examples/swaps.py](examples/swaps.py) for a hand-calculated partial close.

## Native execution and optimization

Python `Engine(..., swap=config)` supports Python strategies and the native SMA
hook. `Backtest.optimize` uses the same rules, preparing one immutable calendar
and giving each simulation its own cursor and broker. Repeated runs reset all
financing balances and ledger entries.

Native Rust `Engine::new` and `Engine::run` remain unchanged and disabled.
Enable swaps with the additive fallible method:

```rust
use _backtestingfx::swap::SwapConfig;

let swap = SwapConfig::new(-7.0, 2.0)?
	.with_rollover("America/New_York", "17:00")?;
let stats = engine.run_with_swap(&mut strategy, &swap)?;
```

Schedules use current data at run entry, avoiding stale calendars if native
callers edit `Engine.data`. Stand-alone Broker operations do not run a calendar.
Schedule storage grows with calendar days; ledger storage grows with charged
position/rollover pairs. The new native dependency is `chrono-tz` 0.10.

## Implementation rationale and verification

`src/swap.rs` owns validated configuration and deterministic calendars. Broker
owns cash posting and partial-close allocation. All execution paths share the
same cursor and settlement methods. Precomputed events avoid timezone conversion
on every bar and repeated preparation in optimization workers. A broker-owned
clock was considered but would repeat calendar work and spread fallible time
handling through existing order methods.

The design applies Microsoft's guidance on [validated types and library
resilience](https://microsoft.github.io/rust-guidelines/guidelines/libs/resilience/)
and [work in hot paths](https://microsoft.github.io/rust-guidelines/guidelines/performance/),
with [explicit Result errors](https://google.github.io/comprehensive-rust/error-handling/result.html)
for configured native execution. The existing disabled API stays infallible.
No unsafe code or runtime timezone service is added.

Tests cover debits and credits, boundary entries/exits, gap stops, multi-day
holds, calendar overrides, DST gaps/folds, partial-close reconciliation, overflow,
independent runs, and agreement between native, Python, and signal simulations.
The existing reference comparisons verify unchanged results with swaps disabled.
On the issue #53 benchmark machine, five-sample median disabled totals were
0.0413 seconds at 50k bars and 0.0875 seconds at 100k bars. Matching trades and
full equity passed against backtesting.py. These timings confirm the ingestion
speedup is retained; they do not measure a swap performance improvement.
