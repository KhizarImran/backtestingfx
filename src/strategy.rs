use crate::broker::Broker;
use crate::types::Bar;

pub trait Strategy {
    fn init(&mut self, _data: &[Bar]) {}
    fn next(&mut self, bar: &Bar, broker: &mut Broker);
}
