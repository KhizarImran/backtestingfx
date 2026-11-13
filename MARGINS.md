# Margin and liquidation

Margin is optional. `margin=None` retains unlimited buying power and disables
automatic margin and insolvency liquidation. Enable checks explicitly:

```python
from backtestingfx import Backtest, MarginConfig

stats = Backtest(
	df, MyStrategy, cash=1000.0,
	margin=MarginConfig(leverage=30, maintenance_ratio=0.5),
).run()
print(stats.account_status, stats.order_rejections, stats.liquidation)
```

The immutable configuration requires finite `leverage >= 1` and finite
`0 < maintenance_ratio < 1`. The default maintenance ratio is `0.5`; leverage
is required. These are simulation parameters, not a broker or jurisdiction's
margin schedule. Invalid configuration raises `ValueError` before a run.

## Initial and maintenance margin

At a supplied midpoint price, account-currency initial margin is:

```text
gross lots = sum of all open lot sizes, both long and short
used margin = gross lots * contract_size * midpoint * quote_to_account / leverage
free margin = marked equity - used margin
maintenance requirement = used margin * maintenance_ratio
```

Both legs of a hedge consume full margin. There is no netting discount. Margin
is revalued at the current midpoint using the existing constant conversion
rate. It is a requirement on equity, not a cash debit. Closing or partially
closing a position releases margin through its remaining lot size.

Before entry, the broker evaluates the resulting book, including entry
commission and the new position's immediate bid/ask spread loss. Post-entry
equity must be positive and at least the resulting initial margin. Equality at
the initial requirement is allowed. An insufficient-margin rejection creates
no position, consumes no position ID, and charges no entry commission. If the
existing book already breaches maintenance, it is liquidated before the new
order is rejected.

Inside `next()`, `self.used_margin` and `self.free_margin` use the current bar's
closing midpoint. Direct Broker callers supply the midpoint to
`broker.used_margin(price)` and `broker.free_margin(price)`. Free margin can be
negative while equity remains above maintenance; this prevents additional
entries without requiring immediate liquidation.

For example, one lot of EURUSD at midpoint `1.2`, contract size `100000`,
conversion `1`, and leverage `30` requires `4000` account-currency units.
A `4000` account can open exactly one lot without costs. With commission `7`
per side and spread `0.0001`, prospective equity is `3983`, so that entry is
rejected. Margin admission includes the entry fee and spread, while marked
equity continues to exclude future exit commission.

## Rejected orders

Python Strategy and Broker `buy()` and `sell()` return an immutable
`OrderResult`. Check `accepted`, `position_id`, and `reason`:

```python
result = self.buy(0.1)
if not result.accepted:
	print(result.reason)
```

Accepted results contain a position ID and no rejection reason. Rejected
results contain no ID and one of:

| Reason | Meaning |
| --- | --- |
| `insufficient_margin` | The resulting equity does not cover initial margin |
| `account_liquidated` | This run has already stopped trading |
| `arithmetic_overflow` | Prospective account calculations cannot remain finite |

Rejection is an ordinary execution outcome and does not raise an exception.
Invalid numeric order inputs still raise `ValueError`. Each rejection is also
retained in `broker.order_rejections` and `stats.order_rejections`, so ignoring
a return value does not lose the evidence. Records contain `timestamp`,
`is_long`, `lot_size`, midpoint `price`, `reason`, `equity_after_entry`, and
`required_margin`. Unavailable calculation snapshots are `None`.

Signal optimization follows the same entry policy. If a desired position is
rejected, an active account retries on the next bar while the target remains
unmet. Each attempt produces its own rejection record.
The existing resize policy closes the old position before attempting the new
target size. If the new entry is rejected, the account remains flat and keeps
the old position's actual exit costs.

## Liquidation and bar ordering

With margin enabled, each execution path follows the same order:

1. Post every due overnight financing event.
2. Check equity and maintenance at the observed opening midpoint.
3. Process existing gap and intrabar stop-loss/take-profit orders.
4. Check equity and maintenance at the closing midpoint.
5. Invoke the strategy or apply its target-lot signal if the account is active.
6. Check again at the close, including changes made during the callback, and
   record equity.

The account liquidates when marked equity is nonpositive, or when an open
book's equity is at or below its maintenance requirement. An opening breach
takes precedence over stops. Otherwise stops can close positions before a
closing-price breach. Checks use observed opens and closes, not the bar's
high/low extrema or an inferred intrabar path. A threshold crossed and
recovered between observations does not cause a margin liquidation.

All positions close at that observation's midpoint with the existing closing
spread and commission. Trades retain their allocated financing. Negative
settled cash remains debt; the engine does not clamp the balance or forgive
losses. Exit commission can make settled cash lower than the triggering marked
equity. Finalization also checks for nonpositive cash after its exit fees.

Liquidation permanently stops further strategy callbacks and signal orders for
that run, including when positive cash remains. The engine processes the rest
of the input with a flat equity balance, keeping reports aligned. A fresh run
resets liquidation, rejection records, positions, cash, and IDs while retaining
the configuration. Direct entry calls on a liquidated Broker reject; they do
not restart it.

Financing inside a data gap posts before risk is assessed at the next observed
open. There is no market price at the missed rollover instant, so liquidation
uses the observation's timestamp and price. The financing ledger retains the
actual rollover times. No future charges accrue once the positions are closed.

`stats.account_status` is `unrestricted`, `active`, or `liquidated`.
`stats.liquidation` is either `None` or an immutable record containing:

- `timestamp`, midpoint `price`, and trigger `reason`;
- `equity_before` and `used_margin` at the trigger;
- `positions_closed` and settled `cash_after`.

Reasons are `maintenance_margin` and `nonpositive_equity`. A callback or final
exit can exhaust cash after closing every position, so `positions_closed` can
be zero. There is at most one liquidation record per run. Final cash change
continues to reconcile with the sum of net trade PnL, including financing.
Drawdown can exceed 100% when debt remains. Sharpe is defined as zero when any
recorded equity is nonpositive, avoiding percentage returns on zero or debt.

HTML reports show the configuration, account status, rejected-order ledger,
and liquidation evidence. A liquidated report retains the complete input
timeline and displays `LIQUIDATED`.

## Native Rust and direct engines

`Broker(...)`, `Engine(...)`, and the private column factory accept keyword-only
`margin=config` in Python. Native callers construct a checked policy and attach
it before running:

```rust
use _backtestingfx::margin::{MarginConfig, OrderOutcome};

let config = MarginConfig::new(30.0, 0.5)?;
let mut engine = Engine::new(data, 1000.0, 0.0, 0.0, 100000.0, 1.0)
	.with_margin(config);
let stats = engine.run(&mut strategy)?;

// Within a native Strategy:
match broker.buy(price, lots, timestamp, None, None).outcome {
	OrderOutcome::Filled(id) => track(id),
	OrderOutcome::Rejected(record) => inspect(record.reason),
}
```

`Broker::fresh()` preserves configuration and starts an independent account.
Native code that edits public broker fields remains responsible for valid
positions and settings. Engine-managed native, Python, and signal runs use the
same broker risk hooks. Direct Broker margin queries only value the supplied
price; they do not advance an engine or infer market observations.

Native `Engine::run` and `run_with_swap` now return
`Result<Stats, SimulationError>`. Handle the result with `?` or a match.
`SimulationError::Margin` wraps `MarginError`; `SimulationError::Swap` wraps
`SwapError`. Python raises `ValueError` for an enabled account whose observed
valuation overflows. Such a failed simulation returns no Stats; start a fresh
run after correcting the inputs. Entry arithmetic overflow instead produces a
rejected order before charging its entry fee.

Native `Broker::close_all`, `close_position`, `close_partial`, and `check_sl_tp`
now return `Result<(), MarginError>`. With margin enabled, all exit paths
preflight their settlement before changing cash, positions, or trade records.
An invalid manual close or stop batch leaves those values unchanged and marks
the Broker `failed`. Python raises `ValueError` immediately. A native strategy
that ignores the error still fails its engine run at the next risk check,
rather than returning misleading Stats. Use a fresh broker or engine run after
correcting the input. Whole-batch preflight retains linear stop processing;
disabled margin skips these extra arithmetic checks.

Run [examples/margin.py](examples/margin.py) for hand-calculated maintenance
and gap-debt examples. Tests cover admission/cost boundaries, scaling, partial
release, hedges, conversion, swap ordering, stops, debt, fees, terminal behavior,
independent runs, and agreement across the execution paths.

## Engine integration

Broker remains the sole owner of cash, positions, financing, and trade
settlement. The policy module owns validated configuration and immutable
evidence. A separate Account aggregate was considered, but it would require
moving existing public native fields and every settlement caller without
improving the strategy API. Keeping admission and liquidation in Broker also
covers direct entry calls.

One risk-state enum represents disabled checks, an active policy, terminal
liquidation evidence, or a retained arithmetic failure. The engine never
returns Stats for a failed state. Fresh construction retains the policy while
resetting the state. All execution loops call the same pre-strategy,
post-strategy, and finalization methods, rather than repeating policy decisions.

Margin is derived from positions rather than cached because native callers can
edit the public book. Enabled checks accept extra linear valuation and exit
preflight work for accounting consistency. Disabled runs skip that work.
Whole-book permanent stopout keeps settlement and report semantics
deterministic; per-position liquidation and automatic account recovery would
require a separate policy with its own execution tests.

## Verification

The focused margin tests fail on the previous implementation, which has no
margin configuration and accepts a 10-lot order on a 1000-unit account.
Overflow regressions also caught accepted infinite fills and manual closes
that produced infinite cash before the settlement preflight was added.
The completed implementation passes 92 installed-package Python tests,
including the optional reference comparison, and 55 Rust tests. Clippy with
warnings denied and both margin/native examples pass.

A release-build check alternated the previous implementation and this change
in fresh processes, with a warmup and five measured runs per version. Margin
was disabled; each run verified matching trades and full equity against
backtesting.py 0.6.6. Latest total-time medians on the Intel Core i5-8350U were
47.33/48.89 ms before/after at 50k bars and 97.89/98.21 ms at 100k bars.
These timings vary with host load and do not establish a speed improvement.
The regular ingestion benchmark reported similar peak process memory,
132.44/132.38 MiB and 151.77/151.59 MiB before/after at those sizes.

The installed package used CPython 3.14.4, Rust 1.98.1, PyO3 0.28.3,
pandas 3.1.1, and NumPy 2.5.4 on Linux x86_64. Reproduce the workload for each
version with [the ingestion benchmark](examples/benchmark_ingestion.md):

```bash
repo_dir="$PWD"
cd /tmp
python "$repo_dir/examples/benchmark_ingestion.py" --sizes 50000 100000 --repeats 5 \
	--label margin --output margin-benchmark.json
```

Run the command against the installed release package from outside the source
checkout, using the absolute path to the example. The benchmark reports setup,
simulation, total time, peak memory, and equivalence separately. It measures
the disabled compatibility path; enabled margin performs additional book
valuations and retains its rejection evidence.
