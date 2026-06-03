use crate::types::Bar;
use crate::broker::Broker;

pub trait Strategy {
    fn next(&mut self, bar: &Bar, broker: &mut Broker);
}

