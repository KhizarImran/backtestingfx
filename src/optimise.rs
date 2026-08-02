//! Parameter optimisation: run many backtests in parallel, natively.
//!
//! The strategy callback can't be used here — it runs in Python, so every bar would
//! need the GIL and the threads below would just queue up behind each other. Instead
//! Python sends one *signal array* per parameter combination: the target lot size for
//! each bar (+0.1 long, -0.1 short, 0.0 flat). Producing those is cheap and vectorised
//! in Python; consuming them is pure Rust, so the GIL can be released and the whole
//! grid runs on real threads.

use crate::broker::Broker;
use crate::engine::Engine;
use crate::stats::Stats;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// One simulation, driven by target lots per bar.
///
/// Takes `&Engine`, never `&mut`. That's the whole trick: with a shared reference it
/// *has* to build its own Broker, which is what makes it safe to run many of these
/// across threads on one Engine. (It also means, unlike `Engine::run`, that calling
/// it twice gives the same answer twice.)
fn simulate(engine: &Engine, target: &[f64]) -> Stats {
    let mut broker = Broker::new(
        engine.broker.initial_cash,
        engine.broker.commission,
        engine.broker.spread,
        engine.broker.contract_size,
        engine.broker.quote_to_account,
    );
    let mut equity_curve = Vec::with_capacity(engine.data.len() + 1);
    equity_curve.push(broker.initial_cash);

    for (bar, &want) in engine.data.iter().zip(target) {
        broker.check_sl_tp(bar);

        // signed exposure: longs count positive, shorts negative
        let have: f64 = broker
            .positions
            .iter()
            .map(|p| if p.is_long { p.lot_size } else { -p.lot_size })
            .sum();

        // ponytail: any change in target flattens and reopens, so a resize pays
        // commission on the full size. Adjust the existing position instead if that
        // drag starts distorting the grid.
        if (want - have).abs() > 1e-9 {
            broker.close_all(bar.close, bar.timestamp);
            if want > 0.0 {
                broker.buy(bar.close, want, bar.timestamp, None, None);
            } else if want < 0.0 {
                broker.sell(bar.close, -want, bar.timestamp, None, None);
            }
        }

        equity_curve.push(broker.equity(bar.close));
    }

    // match Engine::run: liquidate at the last bar and let that land in the curve
    if let Some(last_bar) = engine.data.last() {
        broker.close_all(last_bar.close, last_bar.timestamp);
        *equity_curve.last_mut().unwrap() = broker.cash;
    }

    Stats::compute(&broker, &equity_curve)
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

    let engine: &Engine = &engine;
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
                        slice.iter().map(|s| simulate(engine, s)).collect::<Vec<_>>()
                    })
                })
                .collect();

            handles
                .into_iter()
                .flat_map(|h| h.join().unwrap())
                .collect()
        })
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Bar;

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
        let stats = simulate(&engine, &[1.0, 1.0]);

        // bought 1 lot at 1.1000, held through bar 2, liquidated at 1.2000
        // (1.2 - 1.1) * 1.0 * 100_000 = 10_000
        assert_eq!(stats.num_trades, 1);
        assert!((stats.final_cash - 20_000.0).abs() < 1e-6);
    }

    #[test]
    fn flat_signal_never_trades() {
        let stats = simulate(&test_engine(), &[0.0, 0.0]);

        assert_eq!(stats.num_trades, 0);
        assert_eq!(stats.final_cash, 10_000.0);
    }

    #[test]
    fn short_signal_loses_when_price_rises() {
        let stats = simulate(&test_engine(), &[-1.0, -1.0]);

        assert!((stats.final_cash - 0.0).abs() < 1e-6); // lost the whole 10_000
    }

    #[test]
    fn each_run_gets_a_fresh_broker() {
        let engine = test_engine();

        // the bug this design rules out: state leaking from one run into the next
        let first = simulate(&engine, &[1.0, 1.0]);
        let second = simulate(&engine, &[1.0, 1.0]);

        assert_eq!(first.final_cash, second.final_cash);
        assert_eq!(second.num_trades, 1);
    }
}
