use crate::types::{Position, Trade};

pub struct Broker{
    pub cash: f64,
    pub positions: Vec<Position>,
    pub trade_history: Vec<Trade>
}

impl Broker {
    pub fn new(initial_cash: f64) -> self {     // does not need &mut because it initialises something new 
        Broker {
            cash: initial_cash,
            positions: Vec::new(),
            trade_history: Vec::new()
        }
    }

    pub fn buy(&mut self, price: f64, lot_size: f64, timestamp: i64) {      // needs to modify the broker with new position. (.push works with the Vec::)
        self.positions.push(Position {
            entry_price: price,
            lot_size,
            is_long: true,
            entry_timestamp: timestamp
        });
    }

    pub fn sell(&mut self, price: f64, lot_size: f64, timestamp: i64) {     // needs to modify the broker with new position. (.push works with the Vec::)
        self.positions.push(Position {
            entry_price: price,
            lot_size,
            is_long: false,
            entry_timestamp: timestamp
        });
    }

    pub fn close_all (&mut self, price:f64, timestamp: i64) {       // Instead of .push it uses drain to calculate the close all positions
        for position in self.positions.drain(..) {
            let pnl = if position.is_long {
                (price - position.entry_price) * position.lot_size
            } else {
                (position.entry_price - price) * position.lot_size
            };
            self.cash += pnl;
            self.trade_history.push(Trade {
                entry_price: position.entry_price,
                exit_price: position.exit_price,
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl,
                entry_timestamp: position.entry_timestamp,
                exit_timestamp: timestamp
            });
        }
    }

    
}
