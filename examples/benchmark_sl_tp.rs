//! Run with `cargo run --release --example benchmark_sl_tp`.
//! Position setup is excluded; each repetition uses a fresh broker.
use _backtestingfx::broker::Broker;
use _backtestingfx::types::Bar;
use std::hint::black_box;
use std::time::Instant;

fn main() {
    const REPEATS: usize = 9;
    println!("All positions hit SL on one bar; median of {REPEATS} runs");
    for count in [1_000, 2_000, 4_000, 8_000, 16_000, 32_000] {
        let mut times = Vec::with_capacity(REPEATS);
        for _ in 0..REPEATS {
            let mut broker = Broker::new(10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
            for i in 0..count {
                broker.buy(1.1, 0.01, i as i64, Some(1.09), None);
            }
            let bar = Bar::new(count as i64, 1.1, 1.11, 1.08, 1.1, 0.0);
            let start = Instant::now();
            broker.check_sl_tp(black_box(&bar));
            times.push(start.elapsed());
            assert!(broker.positions.is_empty());
            assert_eq!(broker.trade_history.len(), count);
            for (i, trade) in broker.trade_history.iter().enumerate() {
                assert_eq!(trade.entry_timestamp, i as i64);
            }
            black_box(&broker);
        }
        times.sort_unstable();
        let median = times[REPEATS / 2];
        println!(
            "{count:>6} positions: {:>12.3} us ({:.1} ns/position)",
            median.as_secs_f64() * 1e6,
            median.as_secs_f64() * 1e9 / count as f64
        );
    }
}
