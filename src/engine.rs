use crate::types::Bar;
use crate::broker::Broker;
use crate::strategy::Strategy;

pub struct Engine {
    pub data: Vec<Bar>,
    pub broker: Broker
}

impl Engine {
    pub fn new(data: Vec<Bar>, initial_cash: f64) -> Self {
        Engine {
            data,
            broker : Broker::new(initial_cash) // it takes initial cash and not Broker as Engine is responsible for broker not the user 
        }
    }

    pub fn run (&mut self, strategy: &mut dyn Strategy) { //&mut dyn allows class inheritence 
        for bar in &self.data {
            strategy.next(bar, &mut self.broker);
        }
    }
}