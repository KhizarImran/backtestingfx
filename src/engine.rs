use crate::broker::Broker;
use crate::stats::Stats;
use crate::strategy::Strategy;
use crate::types::Bar;
use pyo3::prelude::*;

#[pyclass]
pub struct Engine {
    pub data: Vec<Bar>,
    pub broker: Broker,
    pub equity_curve: Vec<f64>,
}

impl Engine {
    /// Run an independent simulation using the broker's current configuration.
    /// Strategies with their own mutable state should reset it in `init`.
    pub fn run(&mut self, strategy: &mut dyn Strategy) -> Stats {
        self.broker = Broker::new(
            self.broker.initial_cash,
            self.broker.commission,
            self.broker.spread,
            self.broker.contract_size,
            self.broker.quote_to_account,
        );
        self.equity_curve.clear();
        self.equity_curve.push(self.broker.initial_cash);

        strategy.init(&self.data);
        for bar in &self.data {
            self.broker.check_sl_tp(bar);
            strategy.next(bar, &mut self.broker);
            self.equity_curve.push(self.broker.equity(bar.close));
        }
        if let Some(last_bar) = self.data.last() {
            self.broker.close_all(last_bar.close, last_bar.timestamp);
            *self.equity_curve.last_mut().unwrap() = self.broker.cash;
        }
        Stats::compute(&self.broker, &self.equity_curve)
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
            broker.close_all(bar.close, bar.timestamp);
        }
    }
}

#[pymethods]
impl Engine {
    #[new]
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
        }
    }

    #[pyo3(name = "run")]
    pub fn run_py(&mut self, py: Python<'_>, strategy: Py<PyAny>) -> PyResult<Stats> {
        self.equity_curve.clear();
        self.equity_curve.push(self.broker.initial_cash);

        strategy
            .bind(py)
            .call_method1("init", (self.data.clone(),))?;

        let broker_py = Py::new(
            py,
            Broker::new(
                self.broker.initial_cash,
                self.broker.commission,
                self.broker.spread,
                self.broker.contract_size,
                self.broker.quote_to_account,
            ),
        )?;

        for bar in &self.data {
            {
                let mut b = broker_py.borrow_mut(py);
                b.check_sl_tp(bar);
            }
            strategy
                .bind(py)
                .call_method("next", (bar.clone(), broker_py.clone_ref(py)), None)?;
            let equity = broker_py.borrow(py).equity(bar.close);
            self.equity_curve.push(equity);
        }

        if let Some(last_bar) = self.data.last() {
            let mut b = broker_py.borrow_mut(py);
            b.close_all(last_bar.close, last_bar.timestamp);
            *self.equity_curve.last_mut().unwrap() = b.cash;
        }

        let b = broker_py.borrow(py);
        Ok(Stats::compute(&b, &self.equity_curve))
    }

    // ponytail: benchmark hook, runs the SMA strategy entirely in Rust via the native run() path.
    pub fn run_native_sma(&mut self, fast: usize, slow: usize, lot_size: f64) -> Stats {
        let mut strat = SmaCross {
            fast,
            slow,
            lot_size,
            closes: Vec::new(),
        };
        self.run(&mut strat)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct BuyAndHold;

    impl Strategy for BuyAndHold {
        fn next(&mut self, bar: &Bar, broker: &mut Broker) {
            broker.buy(bar.close, 1.0, bar.timestamp, None, None);
        }
    }

    #[test]
    fn equity_curve_includes_initial_cash_and_final_liquidation() {
        let data = vec![Bar::new(0, 1.1000, 1.1000, 1.1000, 1.1000, 0.0)];
        let mut engine = Engine::new(data, 10_000.0, 7.0, 0.0, 100_000.0, 1.0);

        let stats = engine.run(&mut BuyAndHold);

        assert_eq!(engine.equity_curve, vec![10_000.0, 9_986.0]);
        assert_eq!(stats.final_cash, 9_986.0);
        assert_eq!(engine.equity_curve.last(), Some(&stats.final_cash));
    }

    #[test]
    fn repeated_native_runs_reset_cash_trades_and_position_ids() {
        let data = vec![Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0)];
        let mut engine = Engine::new(data, 10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
        let mut strategy = BuyAndHold;
        let first = engine.run(&mut strategy);
        let second = engine.run(&mut strategy);

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
        engine.run(&mut Probe);
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
        reused.run(&mut BuyAndHold);
        let actual = reused.run(&mut SellEveryBar);
        let expected = fresh.run(&mut SellEveryBar);

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
        engine.broker.close_all(1.2, 1);
        engine.broker.buy(1.2, 1.0, 1, None, None);
        engine.equity_curve.push(123.0);

        let stats = engine.run(&mut BuyAndHold);

        assert_eq!(stats.final_cash, 10_000.0);
        assert_eq!(stats.num_trades, 0);
        assert_eq!(stats.equity_curve, vec![10_000.0]);
        assert!(engine.broker.positions.is_empty());
        assert!(engine.broker.trade_history.is_empty());
    }
}
