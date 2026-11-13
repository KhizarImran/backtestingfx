use _backtestingfx::broker::Broker;
use _backtestingfx::data::load_csv;
use _backtestingfx::engine::Engine;
use _backtestingfx::strategy::Strategy;
use _backtestingfx::types::Bar;

struct BuyEveryBar;

impl Strategy for BuyEveryBar {
    fn next(&mut self, bar: &Bar, broker: &mut Broker) {
        // Engine::run propagates any retained settlement error after this callback.
        let _ = broker.close_all(bar.close, bar.timestamp);
        broker.buy(bar.close, 1.0, bar.timestamp, None, None); // can be more complicated with buy, sells, close position, close all etc.
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let data = load_csv("data/EURUSD_1H.csv")?;
    let mut engine = Engine::new(data, 10_000.0, 0.0, 0.00010, 1.0, 1.0);
    let mut strategy = BuyEveryBar;

    let stats = engine.run(&mut strategy)?;
    println!("{}", stats);
    Ok(())
}
