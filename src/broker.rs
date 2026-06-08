
use crate::types::{Position, Trade};

pub struct Broker{
    pub cash: f64,
    pub initial_cash: f64,
    pub positions: Vec<Position>,
    pub trade_history: Vec<Trade>,
    pub commission: f64,
    pub spread: f64
}

impl Broker {
    pub fn new(initial_cash: f64, commission: f64, spread: f64) -> Self {     // does not need &mut because it initialises something new 
        Broker {
            cash: initial_cash,
            initial_cash,
            positions: Vec::new(),
            trade_history: Vec::new(),
            commission,
            spread
        }
    }

    pub fn buy(&mut self, price: f64, lot_size: f64, timestamp: i64) {      // needs to modify the broker with new position. (.push works with the Vec::)
        let fill_price = price + self.spread; // buy at ask 
        self.cash -= self.commission * lot_size; // pay commission

        self.positions.push(Position {
            id: self.positions.len() as u64,
            entry_price: fill_price,
            lot_size,
            is_long: true,
            entry_timestamp: timestamp,
        });
    }

    pub fn sell(&mut self, price: f64, lot_size: f64, timestamp: i64) {  
        let fill_price = price - self.spread; // buy at ask 
        self.cash -= self.commission * lot_size; // pay commission   // needs to modify the broker with new position. (.push works with the Vec::)
        self.positions.push(Position {
            id : self.positions.len() as u64,
            entry_price: fill_price,
            lot_size,
            is_long: false,
            entry_timestamp: timestamp
        });
    }

    pub fn close_position (&mut self, id: u64, price:f64, timestamp:i64) {
        if let Some(index) = self.positions.iter().position(|p| p.id == id) {
            let position = self.positions.remove(index);

            let pnl = if position.is_long {
                (price - position.entry_price) * position.lot_size
            } else {
                (position.entry_price - price) * position.lot_size
            };

            self.cash += pnl;
            self.trade_history.push(Trade {
                entry_price: position.entry_price,
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl,
                entry_timestamp: position.entry_timestamp,
                exit_timestamp: timestamp,
                exit_price: price
            });            
        }
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
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl,
                entry_timestamp: position.entry_timestamp,
                exit_timestamp: timestamp,
                exit_price: price
            });
        }
    }

    pub fn equity(&self, current_price: f64) -> f64 { // computes unrealised positions from the opened positions
        let unrealized: f64 = self.positions.iter().map(|p| {
            if p.is_long {
                (current_price - p.entry_price) * p.lot_size
            } else {
                (p.entry_price - current_price) * p.lot_size
            }
        }).sum();

        self.cash + unrealized
    }
    
}
