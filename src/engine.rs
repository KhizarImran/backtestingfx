use crate::types::Bar;
use crate::broker::Broker;
use crate::strategy::Strategy;
use crate::stats::Stats;

pub struct Engine {
    pub data: Vec<Bar>,
    pub broker: Broker,
    pub equity_curve: Vec<f64>
}

impl Engine {
    pub fn new(data: Vec<Bar>, initial_cash: f64, commission: f64, spread: f64) -> Self {
        Engine {
            data,
            broker : Broker::new(initial_cash, commission, spread), // it takes initial cash and not Broker as Engine is responsible for broker not the user 
            equity_curve: Vec::new()
        }
    }

    pub fn run (&mut self, strategy: &mut dyn Strategy) -> Stats {
        strategy.init(&self.data);
        for bar in &self.data {
            self.broker.check_sl_tp(bar);
            strategy.next(bar, &mut self.broker);
            self.equity_curve.push(self.broker.equity(bar.close));
        }
        if let Some(last_bar) = self.data.last() {
            self.broker.close_all(last_bar.close, last_bar.timestamp);
        }
        Stats::compute(&self.broker, &self.equity_curve)
    }
}