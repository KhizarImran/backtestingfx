use crate::types::Bar;
use crate::broker::Broker;

pub trait Strategy {
    fn init(&mut self, _data: &[Bar]) {}
    fn next(&mut self, bar: &Bar, broker: &mut Broker);
}

