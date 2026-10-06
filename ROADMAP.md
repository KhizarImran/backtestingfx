# FX scope and roadmap

backtestingfx is an FX research library: Python strategies and vectorized
signals run against a Rust OHLCV simulation with lot sizing, spread, commission,
and quote-to-account conversion. Priorities focus on dependable simulation,
usable research APIs, and reproducible results. Live broker connectivity,
tick/order-book simulation, and general multi-asset portfolio accounting are
outside the current roadmap.

This is a prioritized backlog, not a release-date commitment. Linked issues
contain acceptance criteria and current status; update this document when
priorities or delivered capabilities change.

## 1. Correctness foundation (P0)

- Gap-aware stop/target fills: [#8](https://github.com/KhizarImran/backtestingfx/issues/8).
- Data and timestamp validation: [#15](https://github.com/KhizarImran/backtestingfx/issues/15).
- Broker, size, and signal validation: [#16](https://github.com/KhizarImran/backtestingfx/issues/16).
- Independent native engine runs: [#18](https://github.com/KhizarImran/backtestingfx/issues/18).

## 2. Core adoption (P1)

- Explicit execution timing and intrabar assumptions: [#10](https://github.com/KhizarImran/backtestingfx/issues/10), [#11](https://github.com/KhizarImran/backtestingfx/issues/11).
- Margin, pending orders, and risk sizing: [#9](https://github.com/KhizarImran/backtestingfx/issues/9), [#24](https://github.com/KhizarImran/backtestingfx/issues/24), [#26](https://github.com/KhizarImran/backtestingfx/issues/26).
- Public strategy data, indicators, and parameters: [#19](https://github.com/KhizarImran/backtestingfx/issues/19), [#20](https://github.com/KhizarImran/backtestingfx/issues/20), [#21](https://github.com/KhizarImran/backtestingfx/issues/21).
- Result exports and accounting tests: [#32](https://github.com/KhizarImran/backtestingfx/issues/32), [#37](https://github.com/KhizarImran/backtestingfx/issues/37).
- Wheel coverage, platform CI, and API documentation: [#35](https://github.com/KhizarImran/backtestingfx/issues/35), [#36](https://github.com/KhizarImran/backtestingfx/issues/36), [#40](https://github.com/KhizarImran/backtestingfx/issues/40).

## 3. Research usability and maturity (P2)

- Optimization constraints and objectives: [#28](https://github.com/KhizarImran/backtestingfx/issues/28).
- Out-of-sample workflows and broader metrics: [#31](https://github.com/KhizarImran/backtestingfx/issues/31), [#33](https://github.com/KhizarImran/backtestingfx/issues/33).
- Fair benchmarks and practical tutorials: [#39](https://github.com/KhizarImran/backtestingfx/issues/39), [#41](https://github.com/KhizarImran/backtestingfx/issues/41).

Browse the [full issue backlog](https://github.com/KhizarImran/backtestingfx/issues)
for additional work. Discuss larger API or modeling proposals in an issue before
implementation and state their FX use case.
