use crate::types::{Bar, Position, Trade};
use pyo3::prelude::*;

#[pyclass]
pub struct Broker {
    pub cash: f64,
    pub initial_cash: f64,
    next_id: u64,
    pub positions: Vec<Position>,
    pub trade_history: Vec<Trade>,
    pub commission: f64,
    pub spread: f64,
    pub contract_size: f64,
    pub quote_to_account: f64,
}

// Rust-internal only, not exposed to Python
impl Broker {
    pub fn check_sl_tp(&mut self, bar: &Bar) {
        let mut i = 0;
        while i < self.positions.len() {
            let fill = {
                let p = &self.positions[i];
                if p.is_long {
                    let sl_hit = p.stop_loss.map_or(false, |sl| bar.low <= sl);
                    let tp_hit = p.take_profit.map_or(false, |tp| bar.high >= tp);
                    if sl_hit {
                        p.stop_loss
                    } else if tp_hit {
                        p.take_profit
                    } else {
                        None
                    }
                } else {
                    let sl_hit = p.stop_loss.map_or(false, |sl| bar.high >= sl);
                    let tp_hit = p.take_profit.map_or(false, |tp| bar.low <= tp);
                    if sl_hit {
                        p.stop_loss
                    } else if tp_hit {
                        p.take_profit
                    } else {
                        None
                    }
                }
            };

            if let Some(fill_price) = fill {
                let position = self.positions.remove(i);
                let close_price = if position.is_long {
                    fill_price - self.spread
                } else {
                    fill_price + self.spread
                };

                let pnl = if position.is_long {
                    (close_price - position.entry_price)
                        * position.lot_size
                        * self.contract_size
                        * self.quote_to_account
                } else {
                    (position.entry_price - close_price)
                        * position.lot_size
                        * self.contract_size
                        * self.quote_to_account
                };
                self.cash += pnl - self.commission * position.lot_size;
                self.trade_history.push(Trade {
                    entry_price: position.entry_price,
                    exit_price: close_price,
                    lot_size: position.lot_size,
                    is_long: position.is_long,
                    pnl,
                    entry_timestamp: position.entry_timestamp,
                    exit_timestamp: bar.timestamp,
                });
            } else {
                i += 1;
            }
        }
    }
}

#[pymethods]
impl Broker {
    #[new]
    pub fn new(
        initial_cash: f64,
        commission: f64,
        spread: f64,
        contract_size: f64,
        quote_to_account: f64,
    ) -> Self {
        Broker {
            cash: initial_cash,
            initial_cash,
            next_id: 0,
            positions: Vec::new(),
            trade_history: Vec::new(),
            commission,
            spread,
            contract_size,
            quote_to_account,
        }
    }

    pub fn buy(
        &mut self,
        price: f64,
        lot_size: f64,
        timestamp: i64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
    ) {
        let fill_price = price + self.spread;
        self.cash -= self.commission * lot_size;
        let id = self.next_id;
        self.next_id += 1;
        self.positions.push(Position {
            id,
            entry_price: fill_price,
            lot_size,
            is_long: true,
            entry_timestamp: timestamp,
            stop_loss,
            take_profit,
        });
    }

    pub fn sell(
        &mut self,
        price: f64,
        lot_size: f64,
        timestamp: i64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
    ) {
        let fill_price = price - self.spread;
        self.cash -= self.commission * lot_size;
        let id = self.next_id;
        self.next_id += 1;
        self.positions.push(Position {
            id,
            entry_price: fill_price,
            lot_size,
            is_long: false,
            entry_timestamp: timestamp,
            stop_loss,
            take_profit,
        });
    }

    pub fn close_position(&mut self, id: u64, price: f64, timestamp: i64) {
        if let Some(index) = self.positions.iter().position(|p| p.id == id) {
            let position = self.positions.remove(index);

            let close_price = if position.is_long {
                price - self.spread
            } else {
                price + self.spread
            };

            let pnl = if position.is_long {
                (close_price - position.entry_price)
                    * position.lot_size
                    * self.contract_size
                    * self.quote_to_account
            } else {
                (position.entry_price - close_price)
                    * position.lot_size
                    * self.contract_size
                    * self.quote_to_account
            };

            self.cash += pnl - self.commission * position.lot_size;
            self.trade_history.push(Trade {
                entry_price: position.entry_price,
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl,
                entry_timestamp: position.entry_timestamp,
                exit_timestamp: timestamp,
                exit_price: close_price,
            });
        }
    }

    pub fn close_all(&mut self, price: f64, timestamp: i64) {
        for position in self.positions.drain(..) {
            let close_price = if position.is_long {
                price - self.spread
            } else {
                price + self.spread
            };

            let pnl = if position.is_long {
                (close_price - position.entry_price)
                    * position.lot_size
                    * self.contract_size
                    * self.quote_to_account
            } else {
                (position.entry_price - close_price)
                    * position.lot_size
                    * self.contract_size
                    * self.quote_to_account
            };
            self.cash += pnl - self.commission * position.lot_size;
            self.trade_history.push(Trade {
                entry_price: position.entry_price,
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl,
                entry_timestamp: position.entry_timestamp,
                exit_timestamp: timestamp,
                exit_price: close_price,
            });
        }
    }

    pub fn equity(&self, current_price: f64) -> f64 {
        let unrealized: f64 = self
            .positions
            .iter()
            .map(|p| {
                if p.is_long {
                    (current_price - p.entry_price)
                        * p.lot_size
                        * self.contract_size
                        * self.quote_to_account
                } else {
                    (p.entry_price - current_price)
                        * p.lot_size
                        * self.contract_size
                        * self.quote_to_account
                }
            })
            .sum();

        self.cash + unrealized
    }
}
