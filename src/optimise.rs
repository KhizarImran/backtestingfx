//! Parameter optimisation: run many backtests in parallel, natively.
//!
//! The strategy callback can't be used here — it runs in Python, so every bar would
//! need the GIL and the threads below would just queue up behind each other. Instead
//! Python sends one *signal array* per parameter combination: the target lot size for
//! each bar (+0.1 long, -0.1 short, 0.0 flat). Producing those is cheap and vectorised
//! in Python; consuming them is pure Rust, so the GIL can be released and the whole
//! grid runs on real threads.

#[cfg(test)]
use crate::broker::Broker;
use crate::engine::{Engine, SimulationError};
use crate::stats::Stats;
use crate::swap::SwapPlan;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// One simulation, driven by target lots per bar.
///
/// Takes `&Engine`, never `&mut`. That's the whole trick: with a shared reference it
/// *has* to build its own Broker, which is what makes it safe to run many of these
/// across threads on one Engine. (It also means, unlike `Engine::run`, that calling
/// it twice gives the same answer twice.)
fn simulate(
    engine: &Engine,
    target: &[f64],
    plan: Option<&SwapPlan>,
) -> Result<Stats, SimulationError> {
    let mut broker = engine.broker.fresh();
    let mut equity_curve = Vec::with_capacity(engine.data.len() + 1);
    equity_curve.push(broker.initial_cash);
    let mut rollover = plan.map(SwapPlan::cursor);

    let start = usize::from(engine.execution.is_some());
    if engine.execution.is_some() {
        broker.queue_market_orders();
        equity_curve.resize(start.min(engine.data.len()) + 1, broker.initial_cash);
    }
    for (index, (bar, &want)) in engine.data.iter().zip(target).enumerate().skip(start) {
        if let Some(cursor) = &mut rollover {
            cursor.apply_through(bar.timestamp, &mut broker)?;
        }
        Engine::begin_step(engine.execution, &engine.data, &mut broker, index)?;

        // signed exposure: longs count positive, shorts negative
        let have: f64 = broker
            .positions
            .iter()
            .map(|p| if p.is_long { p.lot_size } else { -p.lot_size })
            .sum();

        // ponytail: any change in target flattens and reopens, so a resize pays
        // commission on the full size. Adjust the existing position instead if that
        // drag starts distorting the grid.
        if broker.is_active() && (want - have).abs() > 1e-9 {
            broker.close_all(bar.close, bar.timestamp)?;
            if want > 0.0 {
                broker.buy(bar.close, want, bar.timestamp, None, None);
            } else if want < 0.0 {
                broker.sell(bar.close, -want, bar.timestamp, None, None);
            }
        }

        equity_curve.push(broker.end_bar(bar)?);
    }

    Engine::finish_run(
        engine.execution,
        &engine.data,
        &mut broker,
        start,
        &mut equity_curve,
    )?;

    Ok(engine.run_stats(&broker, &equity_curve))
}

/// Run one simulation per signal array, in parallel, and return the Stats in order.
///
/// A free function rather than a method because PyO3 allows only one `#[pymethods]`
/// block per class without the `multiple-pymethods` feature, and Engine already has one.
#[pyfunction]
pub fn run_grid(
    py: Python<'_>,
    engine: PyRef<'_, Engine>,
    signals: Vec<Vec<f64>>,
) -> PyResult<Vec<Stats>> {
    let bars = engine.data.len();
    if let Some(bad) = signals.iter().position(|s| s.len() != bars) {
        return Err(PyValueError::new_err(format!(
            "signal {} has {} values, expected {} (one per bar)",
            bad,
            signals[bad].len(),
            bars
        )));
    }
    for (signal_index, signal) in signals.iter().enumerate() {
        if let Some(bar_index) = signal.iter().position(|value| !value.is_finite()) {
            return Err(PyValueError::new_err(format!(
                "signal {signal_index} at bar {bar_index} must be finite"
            )));
        }
    }

    let engine: &Engine = &engine;
    let plan = engine.swap_plan()?;
    let plan = plan.as_ref();
    let threads = std::thread::available_parallelism().map_or(1, |n| n.get());
    let chunk = signals.len().div_ceil(threads).max(1);

    // detach drops the GIL for the duration (it was `allow_threads` before PyO3 0.28).
    // Without it the threads below spawn and immediately block, and the whole exercise
    // buys nothing.
    Ok(py.detach(|| {
        // scope lets the threads borrow `engine` and `signals` directly: it guarantees
        // they finish before it returns, so no Arc and no cloning the bars.
        std::thread::scope(|scope| {
            let handles: Vec<_> = signals
                .chunks(chunk)
                .map(|slice| {
                    scope.spawn(move || {
                        slice
                            .iter()
                            .map(|s| simulate(engine, s, plan))
                            .collect::<Vec<_>>()
                    })
                })
                .collect();

            handles
                .into_iter()
                .flat_map(|h| h.join().unwrap())
                .collect::<Result<Vec<_>, SimulationError>>()
        })
    })?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Bar;

    #[test]
    fn native_and_signal_paths_agree_on_margin_rejections_and_stopout() {
        use crate::margin::MarginConfig;
        use crate::strategy::Strategy;
        struct Hold(f64);
        impl Strategy for Hold {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                if broker.positions.is_empty() {
                    broker.buy(bar.close, self.0, bar.timestamp, None, None);
                }
            }
        }
        for lots in [0.2, 10.0] {
            let data = [1.1, 1.0, 1.3]
                .into_iter()
                .zip(0..)
                .map(|(price, timestamp)| Bar::new(timestamp, price, price, price, price, 0.0))
                .collect();
            let mut engine = Engine::new(data, 1000.0, 0.0, 0.0, 100_000.0, 1.0)
                .with_margin(MarginConfig::new(30.0, 0.5).unwrap());
            let signal = simulate(&engine, &[lots; 3], None).unwrap();
            let native = engine.run(&mut Hold(lots)).unwrap();
            assert_eq!(native.equity_curve, signal.equity_curve);
            assert_eq!(native.account_status(), signal.account_status());
            assert_eq!(native.order_rejections.len(), signal.order_rejections.len());
            assert_eq!(native.num_trades, signal.num_trades);
            assert_eq!(native.final_cash, signal.final_cash);
        }
    }

    #[test]
    fn queued_native_and_signal_runs_share_fill_and_finalization() {
        use crate::execution::{ExecutionTiming, MarketFill};
        use crate::strategy::Strategy;

        struct Hold;
        impl Strategy for Hold {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                if bar.timestamp == 1 {
                    broker.buy(bar.close, 1.0, bar.timestamp, None, None);
                }
            }
        }

        for (market_fill, entry, exit) in [
            (MarketFill::NextOpen, (2, 16.0), (3, 20.0)),
            (MarketFill::PreviousClose, (1, 13.0), (2, 17.0)),
        ] {
            for finalize_trades in [false, true] {
                let data = [(10.0, 11.0), (12.0, 13.0), (16.0, 17.0), (20.0, 21.0)]
                    .into_iter()
                    .zip(0..)
                    .map(|((open, close), time)| Bar::new(time, open, close, open, close, 0.0))
                    .collect();
                let mut engine = Engine::new(data, 100.0, 0.0, 0.0, 1.0, 1.0)
                    .with_execution_timing(ExecutionTiming {
                        market_fill,
                        finalize_trades,
                    });
                let signal = simulate(&engine, &[0.0, 1.0, 1.0, 1.0], None).unwrap();
                for _ in 0..2 {
                    let native = engine.run(&mut Hold).unwrap();
                    assert_eq!(native.equity_curve, signal.equity_curve);
                    assert_eq!(native.num_trades, signal.num_trades);
                    assert_eq!(native.open_positions.len(), signal.open_positions.len());
                    if finalize_trades {
                        let trade = &native.trades[0];
                        assert_eq!((trade.entry_timestamp, trade.entry_price), entry);
                        assert_eq!((trade.exit_timestamp, trade.exit_price), exit);
                    } else {
                        let position = &native.open_positions[0];
                        assert_eq!((position.entry_timestamp, position.entry_price), entry);
                        assert_eq!(native.final_cash, 100.0);
                        assert_eq!(native.final_equity, 121.0 - entry.1);
                    }
                }
            }
        }
    }

    // two bars, price rises 1.1000 -> 1.2000, no costs
    fn test_engine() -> Engine {
        let data = vec![
            Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0),
            Bar::new(3600, 1.2, 1.2, 1.2, 1.2, 0.0),
        ];
        Engine::new(data, 10_000.0, 0.0, 0.0, 100_000.0, 1.0)
    }

    #[test]
    fn signal_opens_holds_and_liquidates() {
        let engine = test_engine();
        let stats = simulate(&engine, &[1.0, 1.0], None).unwrap();

        // bought 1 lot at 1.1000, held through bar 2, liquidated at 1.2000
        // (1.2 - 1.1) * 1.0 * 100_000 = 10_000
        assert_eq!(stats.num_trades, 1);
        assert!((stats.final_cash - 20_000.0).abs() < 1e-6);
    }

    #[test]
    fn flat_signal_never_trades() {
        let stats = simulate(&test_engine(), &[0.0, 0.0], None).unwrap();

        assert_eq!(stats.num_trades, 0);
        assert_eq!(stats.final_cash, 10_000.0);
    }

    #[test]
    fn short_signal_loses_when_price_rises() {
        let stats = simulate(&test_engine(), &[-1.0, -1.0], None).unwrap();

        assert!((stats.final_cash - 0.0).abs() < 1e-6); // lost the whole 10_000
    }

    #[test]
    fn each_run_gets_a_fresh_broker() {
        let engine = test_engine();

        // the bug this design rules out: state leaking from one run into the next
        let first = simulate(&engine, &[1.0, 1.0], None).unwrap();
        let second = simulate(&engine, &[1.0, 1.0], None).unwrap();

        assert_eq!(first.final_cash, second.final_cash);
        assert_eq!(second.num_trades, 1);
    }

    #[test]
    fn native_and_signal_financing_agree_and_workers_have_fresh_cursors() {
        use crate::strategy::Strategy;
        use crate::swap::SwapConfig;
        struct Hold;
        impl Strategy for Hold {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                if broker.positions.is_empty() {
                    broker.buy(bar.close, 0.4, bar.timestamp, None, None);
                }
            }
        }
        let data = [0, 86400, 172800]
            .map(|timestamp| Bar::new(timestamp, 1.1, 1.1, 1.1, 1.1, 0.0))
            .to_vec();
        let mut engine = Engine::new(data, 10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
        let config = SwapConfig::new(-10.0, 2.0)
            .unwrap()
            .with_weekday_multipliers([1.0; 7])
            .unwrap();
        let plan = config.plan(&engine.data).unwrap();
        let native = engine.run_with_swap(&mut Hold, &config).unwrap();
        for _ in 0..2 {
            let signal = simulate(&engine, &[0.4; 3], Some(&plan)).unwrap();
            assert_eq!(signal.equity_curve, native.equity_curve);
            assert_eq!(signal.final_cash, native.final_cash);
            assert_eq!(signal.total_swap, -8.0);
            assert_eq!(signal.trades[0].swap, -8.0);
            assert_eq!(signal.swap_charges.len(), 2);
        }
    }
}
