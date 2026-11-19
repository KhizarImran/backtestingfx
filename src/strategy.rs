use crate::broker::Broker;
use crate::types::Bar;

pub trait Strategy {
    fn init(&mut self, _data: &[Bar]) {}
    /// Initialization for queued execution, which can accept initial market orders.
    fn init_execution(&mut self, data: &[Bar], _broker: &mut Broker) {
        self.init(data);
    }

    /// Additional bars to skip before the first queued execution callback.
    fn execution_warmup(&self) -> usize {
        0
    }

    fn next(&mut self, bar: &Bar, broker: &mut Broker);
}
