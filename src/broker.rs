use crate::types::{Bar, Position, Trade};
use pyo3::prelude::*;

#[pyclass]
pub struct Broker {
    #[pyo3(get)]
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
                let commission = self.commission * position.lot_size;
                self.cash += pnl - commission;
                self.trade_history.push(Trade {
                    entry_price: position.entry_price,
                    exit_price: close_price,
                    lot_size: position.lot_size,
                    is_long: position.is_long,
                    pnl: pnl - commission * 2.0,
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

    pub fn positions(&self) -> Vec<Position> {
        self.positions.clone()
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

            let commission = self.commission * position.lot_size;
            self.cash += pnl - commission;
            self.trade_history.push(Trade {
                entry_price: position.entry_price,
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl: pnl - commission * 2.0,
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
            let commission = self.commission * position.lot_size;
            self.cash += pnl - commission;
            self.trade_history.push(Trade {
                entry_price: position.entry_price,
                lot_size: position.lot_size,
                is_long: position.is_long,
                pnl: pnl - commission * 2.0,
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

#[cfg(test)]
mod tests {
    use super::*;

    fn test_broker(commission: f64, spread: f64) -> Broker {
        Broker::new(10_000.0, commission, spread, 100_000.0, 1.0)
    }

    // f64 arithmetic isn't exact, so compare with a tolerance instead of assert_eq!
    fn assert_close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-6, "expected {b}, got {a}");
    }

    #[test]
    fn buy_then_close_all_computes_pnl() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);
        broker.close_all(1.1050, 1);

        // (exit - entry) * lot_size * contract_size = (1.1050 - 1.1000) * 1.0 * 100_000 = 500.0
        assert_close(broker.cash, 10_000.0 + 500.0);
        assert_eq!(broker.trade_history.len(), 1);
        assert_close(broker.trade_history[0].pnl, 500.0);
    }

    #[test]
    fn sell_then_close_all_computes_pnl() {
        let mut broker = test_broker(0.0, 0.0);
        broker.sell(1.1000, 1.0, 0, None, None);
        broker.close_all(1.0950, 1);

        // short profits when price falls: (entry - exit) * lot_size * contract_size = 500.0
        assert_close(broker.cash, 10_000.0 + 500.0);
    }

    #[test]
    fn commission_deducted_on_open_and_close() {
        let mut broker = test_broker(7.0, 0.0); // $7 per lot
        broker.buy(1.1000, 1.0, 0, None, None);
        assert_eq!(broker.cash, 10_000.0 - 7.0); // charged immediately on open

        broker.close_all(1.1000, 1); // same price as entry, so zero price PnL
        assert_eq!(broker.cash, 10_000.0 - 7.0 - 7.0); // commission charged again on close
        assert_eq!(broker.trade_history[0].pnl, -14.0);
        assert_close(
            broker.cash - broker.initial_cash,
            broker.trade_history.iter().map(|trade| trade.pnl).sum(),
        );
    }

    #[test]
    fn long_position_closes_at_stop_loss_not_bar_close() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, Some(1.0950), None);

        // bar's low dips through the stop, but closes well above it
        let bar = Bar::new(1, 1.1100, 1.1100, 1.0900, 1.1080, 0.0);
        broker.check_sl_tp(&bar);

        assert_eq!(broker.positions.len(), 0);
        assert_eq!(broker.trade_history[0].exit_price, 1.0950); // filled at SL, not bar.close
    }

    #[test]
    fn long_position_stays_open_when_sl_tp_not_hit() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, Some(1.0950), Some(1.1200));

        let bar = Bar::new(1, 1.1020, 1.1050, 1.1010, 1.1030, 0.0); // stays inside range
        broker.check_sl_tp(&bar);

        assert_eq!(broker.positions.len(), 1);
    }

    #[test]
    fn short_position_closes_at_stop_loss() {
        let mut broker = test_broker(0.0, 0.0);
        broker.sell(1.1000, 1.0, 0, Some(1.1050), None);

        // stop loss sits above entry for a short; bar's high pokes through it
        let bar = Bar::new(1, 1.1020, 1.1080, 1.1010, 1.1030, 0.0);
        broker.check_sl_tp(&bar);

        assert_eq!(broker.positions.len(), 0);
        assert_eq!(broker.trade_history[0].exit_price, 1.1050);
    }

    #[test]
    fn close_position_closes_only_the_matching_id() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None); // id 0
        broker.buy(1.2000, 1.0, 0, None, None); // id 1

        broker.close_position(0, 1.1050, 1);

        assert_eq!(broker.positions.len(), 1);
        assert_eq!(broker.positions[0].id, 1); // id 1 left untouched
        assert_eq!(broker.trade_history.len(), 1);
        assert_close(broker.trade_history[0].pnl, 500.0); // (1.1050 - 1.1000) * 1.0 * 100_000
    }

    #[test]
    fn equity_includes_unrealized_pnl() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);

        // price moved up 50 pips, position still open (not closed)
        assert_close(broker.equity(1.1050), 10_000.0 + 500.0);
    }
}
