# Compatibility policy

## Python and platforms

Package metadata declares Python >=3.9. CPython is the intended interpreter;
PyPy support is not currently verified. pandas and numpy are required.
HTML reporting additionally requires Plotly >=5 through the `report` extra.

| Area | Current coverage |
| --- | --- |
| Functional CI | Ubuntu, CPython 3.12; Rust checks and installed-package Python tests |
| Linux release wheel targets | x86_64 and aarch64, with manylinux selected by maturin |
| macOS release wheel targets | Intel x86_64 and Apple Silicon aarch64 |
| Windows release wheel target | x64 |
| Source distributions | Built by the release workflow; require Rust and a native compiler to install |

These wheel targets describe the configured release workflow, not a guarantee
that every Python version has a published wheel or has passed functional tests.
The extension does not currently use abi3, so wheel compatibility depends on
the interpreter and platform tags. The wheel jobs discover available
interpreters. Windows also builds Python 3.10 explicitly. Check the artifacts
for your interpreter before relying on wheel availability.

If pip falls back to a source build, install a current stable Rust toolchain
and your platform's compiler/linker, then retry installation. Contributor
setup is in [CONTRIBUTING.md](CONTRIBUTING.md). Broader wheel verification and
functional coverage are tracked in issues
[#35](https://github.com/KhizarImran/backtestingfx/issues/35) and
[#36](https://github.com/KhizarImran/backtestingfx/issues/36).

## Public API and stability

The project is pre-1.0. Public Python imports from `backtestingfx`, documented
strategy methods/properties, result fields, and documented execution assumptions
are the compatibility surface. Underscore-prefixed members and internal Rust
implementation details may change; existing examples using private strategy
members do not make those members stable.

Before 1.0, intentional incompatible API changes belong in a minor release
with a changelog entry and migration instructions. Patch releases should
preserve API signatures, but correctness fixes can change simulated fills,
PnL, equity, and statistics. Such changes must identify the previous and new
behavior and any migration required. After 1.0, intentional incompatible public
API changes require a major release.

Where practical, deprecated Python APIs emit `DeprecationWarning` and remain
available for at least one minor release before removal. Document the
replacement and planned removal version. A correctness fix that cannot retain
the old behavior safely may be made without a deprecation period, with an
explicit explanation and migration notes.

Changes to the minimum Python version or supported platform targets must be
announced in the changelog and this policy. Before 1.0 they belong in a minor
release; after 1.0 they require a major release.

## Strategy compatibility

The reference is backtesting.py **0.6.6**, commit
`9c2b8e1c99f3a860883222ba8d6d5b5cac3a932d`. Install the `test` extra to run
the comparisons against the pinned package.

For a strategy within the coverage below, change the imports to
`from backtestingfx.compat import Backtest, Strategy` and keep the strategy
class body. You can keep using helpers from your installed `backtesting.lib`,
such as `crossover`. The `backtestingfx.compat` module requires no
backtesting.py installation of its own.

### API reference

```python
from backtestingfx.compat import Backtest, Strategy

backtest = Backtest(data, MyStrategy, cash=10_000, finalize_trades=True)
result = backtest.run(window=20)
best = backtest.optimize(window=[10, 20], maximize="Equity Final [$]")
```

`MyStrategy` subclasses `Strategy`. Declare each run or grid parameter as
a class attribute. Call `self.I(fn, ...)` in `init()` and assign the
returned array to an instance attribute so `next()` sees only data through
the current bar. Non-scatter indicator warmup delays the first callback.

Use `buy(size=...)` and `sell(size=...)` with positive whole units.
Market entries and explicit closes fill on the next open. Use
`self.position.close()` or `trade.close(portion=...)` to queue exits.
`Order` exposes the submitted signed size and direction. `Position`
exposes current aggregate size and unrealized profit or loss. Each
`Trade` in `self.trades` exposes size, entry price, bar, time, and
unrealized profit or loss.

`run()` returns a pandas Series with `Equity Final [$]`, `Return [%]`,
`# Trades`, `SQN`, and `_strategy`. `optimize()` runs a parameter grid
and returns the best Series for one of those four numeric objectives.

### Coverage matrix

| Area | Implemented | Follow-up |
| --- | --- | --- |
| Classes and imports | `backtestingfx.compat.Backtest`, `Strategy`, `Order`, `Position`, `Trade` | [#21](https://github.com/KhizarImran/backtestingfx/issues/21), [#25](https://github.com/KhizarImran/backtestingfx/issues/25) |
| Data | OHLCV arrays and exact prepared pandas index | [Reference input](REFERENCE_INPUT.md), [#22](https://github.com/KhizarImran/backtestingfx/issues/22) for custom arrays |
| Indicators | Numeric arrays, callback slicing, and warmup | [#20](https://github.com/KhizarImran/backtestingfx/issues/20) for the full indicator contract |
| Orders | Funded whole-unit market entries and explicit exits, with optional finalization | [#24](https://github.com/KhizarImran/backtestingfx/issues/24) for live orders, contingent exits with `trade_on_close`, and cancellation. [#62](https://github.com/KhizarImran/backtestingfx/issues/62) for fractional buying power and the default order size. |
| Broker | Zero costs. Entry orders in one direction at a time. Use sizes that stay within buying power at execution. | [#63](https://github.com/KhizarImran/backtestingfx/issues/63) for costs, [#64](https://github.com/KhizarImran/backtestingfx/issues/64) for hedging and netting, [#65](https://github.com/KhizarImran/backtestingfx/issues/65) for buying-power checks and insolvency |
| Results and optimization | Four numeric results and a deterministic parameter grid | [#27](https://github.com/KhizarImran/backtestingfx/issues/27), [#28](https://github.com/KhizarImran/backtestingfx/issues/28), [#32](https://github.com/KhizarImran/backtestingfx/issues/32), [#33](https://github.com/KhizarImran/backtestingfx/issues/33) |
| Plotting and helpers | Strategy code can use helpers from an installed backtesting.py package | [#29](https://github.com/KhizarImran/backtestingfx/issues/29), [#67](https://github.com/KhizarImran/backtestingfx/issues/67), [#68](https://github.com/KhizarImran/backtestingfx/issues/68), [#69](https://github.com/KhizarImran/backtestingfx/issues/69) |

The top-level `backtestingfx` API provides FX lot sizing, quote conversion,
swaps, and configurable maintenance margin. Its `data_mode="reference"`
option changes how it reads data. Use `backtestingfx.compat` for the
strategy API described above.

## Reproducibility

Pin the library and dependency versions for reproducible research. Record data
source, instrument, timeframe, timezone, sizing, spread, commission, and
conversion assumptions. Read [CHANGELOG.md](CHANGELOG.md) before upgrading:
unchanged strategy code can produce different results after a modeling fix.
The full bid–ask spread correction in Unreleased is one such example.

With optional swaps enabled, also record signed long/short rates, the rollover
timezone and time, weekday weights, and date overrides. Timezone rules come
from the `chrono-tz` database embedded in the build. Pin the package and native
dependency versions when reproducing historical rollover results. See
[SWAPS.md](SWAPS.md) for calendar behavior.

This policy describes project expectations; the current tests and published
artifacts establish the coverage actually verified for each release.
