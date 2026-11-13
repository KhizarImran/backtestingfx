use crate::broker::Broker;
use crate::data::{self, BarColumns, DataError};
use crate::margin::{MarginConfig, MarginError};
use crate::stats::Stats;
use crate::strategy::Strategy;
use crate::swap::{SwapConfig, SwapError, SwapPlan};
use crate::types::Bar;
use crate::validation;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;
use std::fmt;

#[derive(Debug)]
pub enum SimulationError {
    Swap(SwapError),
    Margin(MarginError),
}

impl fmt::Display for SimulationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Swap(error) => error.fmt(f),
            Self::Margin(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for SimulationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Swap(error) => Some(error),
            Self::Margin(error) => Some(error),
        }
    }
}

impl From<SwapError> for SimulationError {
    fn from(error: SwapError) -> Self {
        Self::Swap(error)
    }
}

impl From<MarginError> for SimulationError {
    fn from(error: MarginError) -> Self {
        Self::Margin(error)
    }
}

impl From<SimulationError> for PyErr {
    fn from(error: SimulationError) -> Self {
        match error {
            SimulationError::Swap(error) => error.into(),
            SimulationError::Margin(error) => error.into(),
        }
    }
}

#[pyclass]
pub struct Engine {
    pub data: Vec<Bar>,
    pub broker: Broker,
    pub equity_curve: Vec<f64>,
    pub(crate) swap: Option<SwapConfig>,
}

impl Engine {
    /// Run an independent simulation using the broker's current configuration.
    /// Strategies with their own mutable state should reset it in `init`.
    pub fn run(&mut self, strategy: &mut dyn Strategy) -> Result<Stats, SimulationError> {
        self.run_loop(strategy, None)
    }

    /// Run with validated financing, preparing the calendar from the current data.
    pub fn run_with_swap(
        &mut self,
        strategy: &mut dyn Strategy,
        swap: &SwapConfig,
    ) -> Result<Stats, SimulationError> {
        let plan = swap.plan(&self.data)?;
        self.run_loop(strategy, Some(&plan))
    }

    fn run_configured(&mut self, strategy: &mut dyn Strategy) -> Result<Stats, SimulationError> {
        let plan = self.swap_plan()?;
        self.run_loop(strategy, plan.as_ref())
    }

    pub(crate) fn swap_plan(&self) -> Result<Option<SwapPlan>, SwapError> {
        self.swap
            .as_ref()
            .map(|swap| swap.plan(&self.data))
            .transpose()
    }

    fn run_loop(
        &mut self,
        strategy: &mut dyn Strategy,
        plan: Option<&SwapPlan>,
    ) -> Result<Stats, SimulationError> {
        self.broker = self.broker.fresh();
        let mut rollover = plan.map(SwapPlan::cursor);
        self.equity_curve.clear();
        self.equity_curve.push(self.broker.initial_cash);

        strategy.init(&self.data);
        for bar in &self.data {
            if let Some(cursor) = &mut rollover {
                cursor.apply_through(bar.timestamp, &mut self.broker)?;
            }
            self.broker.begin_bar(bar)?;
            if self.broker.is_active() {
                strategy.next(bar, &mut self.broker);
            }
            self.equity_curve.push(self.broker.end_bar(bar)?);
        }
        if let Some(last_bar) = self.data.last() {
            self.broker.finalize(last_bar)?;
            if let Some(last) = self.equity_curve.last_mut() {
                *last = self.broker.cash;
            }
        }
        Ok(Stats::compute(&self.broker, &self.equity_curve))
    }
}

// ponytail: benchmark-only native strategy, mirrors examples/compare_bt.py SmaCrossFx.
// Exists so we can time the pure-Rust engine path (no per-bar Python call) against run_py.
struct SmaCross {
    fast: usize,
    slow: usize,
    lot_size: f64,
    closes: Vec<f64>,
}

impl Strategy for SmaCross {
    fn next(&mut self, bar: &Bar, broker: &mut Broker) {
        self.closes.push(bar.close);
        if self.closes.len() <= self.slow {
            return;
        }
        let n = self.closes.len();
        let fast_sma: f64 = self.closes[n - self.fast..].iter().sum::<f64>() / self.fast as f64;
        let slow_sma: f64 = self.closes[n - self.slow..].iter().sum::<f64>() / self.slow as f64;

        if broker.positions.is_empty() {
            if fast_sma > slow_sma {
                broker.buy(bar.close, self.lot_size, bar.timestamp, None, None);
            }
        } else if fast_sma < slow_sma {
            // The broker retains an exit error; end_bar propagates it from run.
            let _ = broker.close_all(bar.close, bar.timestamp);
        }
    }
}

struct Noop;

impl Strategy for Noop {
    fn next(&mut self, _bar: &Bar, _broker: &mut Broker) {}
}

impl Engine {
    pub fn with_margin(mut self, config: MarginConfig) -> Self {
        self.broker = self.broker.with_margin(config);
        self
    }

    /// Construct an engine from checked market data; native configuration stays caller-owned.
    pub fn try_new(
        data: Vec<Bar>,
        initial_cash: f64,
        commission: f64,
        spread: f64,
        contract_size: f64,
        quote_to_account: f64,
    ) -> Result<Self, DataError> {
        data::validate_bars(&data)?;
        Ok(Self::new(
            data,
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        ))
    }

    pub fn new(
        data: Vec<Bar>,
        initial_cash: f64,
        commission: f64,
        spread: f64,
        contract_size: f64,
        quote_to_account: f64,
    ) -> Self {
        Engine {
            data,
            broker: Broker::new(
                initial_cash,
                commission,
                spread,
                contract_size,
                quote_to_account,
            ),
            equity_curve: Vec::new(),
            swap: None,
        }
    }
}

#[pymethods]
impl Engine {
    #[staticmethod]
    #[expect(
        clippy::too_many_arguments,
        reason = "preserve positional constructor arguments and add keyword-only account policies"
    )]
    #[pyo3(signature = (columns, initial_cash, commission, spread, contract_size, quote_to_account, *, swap=None, margin=None))]
    fn _from_columns(
        py: Python<'_>,
        columns: BarColumns,
        initial_cash: f64,
        commission: f64,
        spread: f64,
        contract_size: f64,
        quote_to_account: f64,
        swap: Option<SwapConfig>,
        margin: Option<MarginConfig>,
    ) -> PyResult<Self> {
        validation::config(
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        )?;
        let data = data::bars_from_columns(py, columns)?;
        let mut engine = Self::new(
            data,
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        );
        engine.swap = swap;
        if let Some(config) = margin {
            engine.broker = engine.broker.with_margin(config);
        }
        Ok(engine)
    }

    #[new]
    #[expect(
        clippy::too_many_arguments,
        reason = "preserve positional constructor arguments and add keyword-only account policies"
    )]
    #[pyo3(signature = (data, initial_cash, commission, spread, contract_size, quote_to_account, *, swap=None, margin=None))]
    fn py_new(
        data: Vec<Bar>,
        initial_cash: f64,
        commission: f64,
        spread: f64,
        contract_size: f64,
        quote_to_account: f64,
        swap: Option<SwapConfig>,
        margin: Option<MarginConfig>,
    ) -> PyResult<Self> {
        validation::config(
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        )?;
        data::validate_bars(&data)?;
        let mut engine = Self::new(
            data,
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        );
        engine.swap = swap;
        if let Some(config) = margin {
            engine.broker = engine.broker.with_margin(config);
        }
        Ok(engine)
    }

    #[pyo3(name = "run")]
    pub fn run_py(&mut self, py: Python<'_>, strategy: Py<PyAny>) -> PyResult<Stats> {
        let plan = self.swap_plan()?;
        let mut rollover = plan.as_ref().map(SwapPlan::cursor);
        self.equity_curve.clear();
        self.equity_curve.push(self.broker.initial_cash);

        let history = PyList::new(py, self.data.iter().cloned())?;
        strategy.bind(py).call_method1("init", (history,))?;

        let broker_py = Py::new(py, self.broker.fresh())?;

        let next_name = pyo3::intern!(py, "next");
        for bar in &self.data {
            let active = {
                let mut b = broker_py.borrow_mut(py);
                if let Some(cursor) = &mut rollover {
                    cursor.apply_through(bar.timestamp, &mut b)?;
                }
                b.begin_bar(bar)?;
                b.is_active()
            };
            if active {
                strategy
                    .bind(py)
                    .call_method1(next_name, (bar.clone(), broker_py.clone_ref(py)))?;
            }
            let equity = broker_py.borrow_mut(py).end_bar(bar)?;
            self.equity_curve.push(equity);
        }

        if let Some(last_bar) = self.data.last() {
            let mut b = broker_py.borrow_mut(py);
            b.finalize(last_bar)?;
            if let Some(last) = self.equity_curve.last_mut() {
                *last = b.cash;
            }
        }

        let b = broker_py.borrow(py);
        Ok(Stats::compute(&b, &self.equity_curve))
    }

    // ponytail: benchmark hook, runs the SMA strategy entirely in Rust via the native run() path.
    pub fn run_native_sma(&mut self, fast: usize, slow: usize, lot_size: f64) -> PyResult<Stats> {
        validation::positive("lot_size", lot_size)?;
        if fast == 0 || slow == 0 || fast > slow {
            return Err(PyValueError::new_err(
                "SMA windows must satisfy 1 <= fast <= slow",
            ));
        }
        let mut strat = SmaCross {
            fast,
            slow,
            lot_size,
            closes: Vec::new(),
        };
        Ok(self.run_configured(&mut strat)?)
    }

    // Benchmark baseline with the same broker, equity, statistics, and finalization paths.
    fn _run_native_noop(&mut self) -> PyResult<Stats> {
        Ok(self.run_configured(&mut Noop)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ignored_native_exit_errors_still_fail_the_simulation_without_mutation() {
        struct BadClose;
        impl Strategy for BadClose {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                broker.buy(bar.close, 1.0, bar.timestamp, None, None);
                let _ = broker.close_all(f64::MAX, bar.timestamp);
            }
        }
        let data = vec![Bar::new(0, 1.0, 1.0, 1.0, 1.0, 0.0)];
        let mut engine = Engine::new(data, 1e308, 0.0, 0.0, 1e308, 1.0)
            .with_margin(MarginConfig::new(1e308, 0.5).unwrap());
        assert!(matches!(
            engine.run(&mut BadClose),
            Err(SimulationError::Margin(MarginError::ArithmeticOverflow))
        ));
        assert_eq!(engine.broker.cash, 1e308);
        assert_eq!(engine.broker.positions.len(), 1);
        assert!(engine.broker.trade_history.is_empty());
    }

    #[test]
    fn margin_stopout_preserves_debt_halts_callbacks_and_resets_on_reuse() {
        struct Hold {
            calls: usize,
        }
        impl Strategy for Hold {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                self.calls += 1;
                if bar.timestamp == 0 {
                    broker.buy(bar.close, 0.2, 0, None, None);
                }
            }
        }
        let data = [1.1, 1.0, 1.3]
            .into_iter()
            .zip(0..)
            .map(|(price, timestamp)| Bar::new(timestamp, price, price, price, price, 0.0))
            .collect();
        let mut engine = Engine::new(data, 1000.0, 0.0, 0.0, 100_000.0, 1.0)
            .with_margin(MarginConfig::new(30.0, 0.5).unwrap());
        let mut hold = Hold { calls: 0 };
        for _ in 0..2 {
            let stats = engine.run(&mut hold).unwrap();
            assert!((stats.final_cash + 1000.0).abs() < 1e-8);
            assert_eq!(stats.account_status(), "liquidated");
            assert_eq!(stats.liquidation().unwrap().timestamp, 1);
            assert_eq!(stats.equity_curve.len(), 4);
            assert_eq!(stats.equity_curve[2], stats.equity_curve[3]);
            assert!(
                (stats.final_cash - 1000.0 - stats.trades.iter().map(|t| t.pnl).sum::<f64>()).abs()
                    < 1e-8
            );
            assert_eq!(stats.sharpe_ratio, 0.0);
        }
        assert_eq!(hold.calls, 2);
    }

    #[test]
    fn enabled_margin_returns_typed_error_for_overflowing_marks() {
        struct Hold;
        impl Strategy for Hold {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                broker.buy(bar.close, 0.2, bar.timestamp, None, None);
            }
        }
        let data = vec![
            Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0),
            Bar::new(1, f64::MAX, f64::MAX, f64::MAX, f64::MAX, 0.0),
        ];
        let mut engine = Engine::new(data, 1000.0, 0.0, 0.0, 100_000.0, 1.0)
            .with_margin(MarginConfig::new(30.0, 0.5).unwrap());
        assert!(matches!(
            engine.run(&mut Hold),
            Err(SimulationError::Margin(MarginError::ArithmeticOverflow))
        ));
        assert!(engine.broker.cash.is_finite());
        assert!(engine.broker.trade_history.is_empty());
    }

    struct BuyAndHold;

    impl Strategy for BuyAndHold {
        fn next(&mut self, bar: &Bar, broker: &mut Broker) {
            broker.buy(bar.close, 1.0, bar.timestamp, None, None);
        }
    }

    #[test]
    fn native_gap_fills_settle_before_strategy_and_reconcile_final_equity() {
        struct EnterOnce(bool);
        impl Strategy for EnterOnce {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                assert!(broker.positions.is_empty());
                if bar.timestamp == 0 {
                    if self.0 {
                        broker.buy(bar.close, 0.4, 0, Some(1.09), Some(1.11));
                    } else {
                        broker.sell(bar.close, 0.4, 0, Some(1.11), Some(1.09));
                    }
                } else {
                    assert_eq!(broker.trade_history.len(), 1);
                    assert_eq!(broker.trade_history[0].exit_timestamp, bar.timestamp);
                }
            }
        }
        for is_long in [true, false] {
            for open in [1.08, 1.12] {
                let data = vec![
                    Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0),
                    Bar::new(1, open, 1.12, 1.08, 1.1, 0.0),
                ];
                let mut engine = Engine::new(data, 10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
                let stats = engine.run(&mut EnterOnce(is_long)).unwrap();
                let favorable = is_long == (open > 1.1);
                let pnl = (if favorable { 1990.0 } else { -2010.0 }) * 0.4 * 1.27 - 5.6;
                assert_eq!(stats.num_trades, 1);
                let expected_exit = open + if is_long { -0.00005 } else { 0.00005 };
                assert!((stats.trades[0].exit_price - expected_exit).abs() < 1e-8);
                assert!((stats.trades[0].pnl - pnl).abs() < 1e-8);
                assert!((stats.final_cash - 10_000.0 - pnl).abs() < 1e-8);
                assert_eq!(stats.equity_curve.len(), 3);
                assert_eq!(stats.equity_curve.last(), Some(&stats.final_cash));
            }
        }
    }

    #[test]
    fn equity_curve_includes_initial_cash_and_final_liquidation() {
        let data = vec![Bar::new(0, 1.1000, 1.1000, 1.1000, 1.1000, 0.0)];
        let mut engine = Engine::new(data, 10_000.0, 7.0, 0.0, 100_000.0, 1.0);

        let stats = engine.run(&mut BuyAndHold).unwrap();

        assert_eq!(engine.equity_curve, vec![10_000.0, 9_986.0]);
        assert_eq!(stats.final_cash, 9_986.0);
        assert_eq!(engine.equity_curve.last(), Some(&stats.final_cash));
    }

    #[test]
    fn repeated_native_runs_reset_cash_trades_and_position_ids() {
        let data = vec![Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0)];
        let mut engine = Engine::new(data, 10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
        let mut strategy = BuyAndHold;
        let first = engine.run(&mut strategy).unwrap();
        let second = engine.run(&mut strategy).unwrap();

        assert_eq!(second.final_cash, first.final_cash);
        assert_eq!(second.equity_curve, first.equity_curve);
        assert_eq!(second.num_trades, 1);
        assert_eq!(engine.broker.trade_history.len(), 1);
        assert_eq!(second.trades[0].pnl, first.trades[0].pnl);
        assert!((second.final_cash - 9_973.3).abs() < 1e-8);
        assert!(engine.broker.positions.is_empty());

        // Probe the next run from inside the strategy, before final liquidation.
        struct Probe;
        impl Strategy for Probe {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                assert_eq!(broker.cash, 10_000.0);
                assert!(broker.positions.is_empty());
                assert!(broker.trade_history.is_empty());
                broker.buy(bar.close, 1.0, bar.timestamp, None, None);
                assert_eq!(broker.positions[0].id, 0);
            }
        }
        engine.run(&mut Probe).unwrap();
    }

    #[test]
    fn a_different_strategy_matches_a_fresh_engine() {
        struct SellEveryBar;
        impl Strategy for SellEveryBar {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                broker.sell(bar.close, 0.5, bar.timestamp, None, None);
            }
        }
        let data = vec![
            Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0),
            Bar::new(1, 1.2, 1.2, 1.2, 1.2, 0.0),
        ];
        let mut reused = Engine::new(data.clone(), 10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
        let mut fresh = Engine::new(data, 10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
        reused.run(&mut BuyAndHold).unwrap();
        let actual = reused.run(&mut SellEveryBar).unwrap();
        let expected = fresh.run(&mut SellEveryBar).unwrap();

        assert_eq!(actual.final_cash, expected.final_cash);
        assert_eq!(actual.equity_curve, expected.equity_curve);
        assert_eq!(actual.num_trades, expected.num_trades);
        for (actual, expected) in actual.trades.iter().zip(&expected.trades) {
            assert_eq!(actual.pnl, expected.pnl);
            assert_eq!(actual.entry_price, expected.entry_price);
            assert_eq!(actual.exit_price, expected.exit_price);
            assert!(!actual.is_long);
        }
    }

    #[test]
    fn empty_run_discards_existing_positions_and_history() {
        let mut engine = Engine::new(Vec::new(), 10_000.0, 7.0, 0.0001, 100_000.0, 1.0);
        engine.broker.buy(1.1, 1.0, 0, None, None);
        engine.broker.close_all(1.2, 1).unwrap();
        engine.broker.buy(1.2, 1.0, 1, None, None);
        engine.equity_curve.push(123.0);

        let stats = engine.run(&mut BuyAndHold).unwrap();

        assert_eq!(stats.final_cash, 10_000.0);
        assert_eq!(stats.num_trades, 0);
        assert_eq!(stats.equity_curve, vec![10_000.0]);
        assert!(engine.broker.positions.is_empty());
        assert!(engine.broker.trade_history.is_empty());
    }
}
