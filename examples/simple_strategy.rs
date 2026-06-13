use backtestingfx::types::Bar;
use backtestingfx::broker::Broker;
use backtestingfx::strategy::Strategy;
use backtestingfx::engine::Engine;
use backtestingfx::data::load_csv;


struct BuyEveryBar;

impl Strategy for BuyEveryBar {
    fn next (&mut self, bar: &Bar, broker: &mut Broker) {
        broker.close_all(bar.close, bar.timestamp); //closes any open positions
        broker.buy(bar.close, 1.0, bar.timestamp); // can be more complicated with buy, sells, close position, close all etc.
    }
}

fn main() {
    let data = load_csv("examples/data/eurusd_lse_1h.csv");
    let mut engine = Engine::new(data, 10_000.0, 0.0, 0.00010);
    let mut strategy = BuyEveryBar;

    let stats = engine.run(&mut strategy);
    println!("{}", stats);
}



