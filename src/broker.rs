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
    /// Full bid–ask width in price units; supplied prices are midpoints.
    pub spread: f64,
    pub contract_size: f64,
    pub quote_to_account: f64,
}

// Rust-internal only, not exposed to Python
impl Broker {
    fn bid(&self, midpoint: f64) -> f64 {
        midpoint - self.spread / 2.0
    }

    fn ask(&self, midpoint: f64) -> f64 {
        midpoint + self.spread / 2.0
    }

    // Closing a position, part of a position, or a whole book all do the same four things:
    // work out the fill price, work out the PnL, charge commission, record the Trade.
    // `lots` is how much of the position to close, so a partial close is the same code
    // with a smaller number.
    fn settle(&mut self, position: &Position, lots: f64, price: f64, timestamp: i64) {
        let close_price = if position.is_long {
            self.bid(price)
        } else {
            self.ask(price)
        };

        let pnl = if position.is_long {
            (close_price - position.entry_price) * lots * self.contract_size * self.quote_to_account
        } else {
            (position.entry_price - close_price) * lots * self.contract_size * self.quote_to_account
        };

        // entry commission was already taken out of cash when the position opened,
        // so cash only pays the exit leg here — but the Trade records both, because
        // a trade's PnL should be what the round trip actually cost.
        let commission = self.commission * lots;
        self.cash += pnl - commission;
        self.trade_history.push(Trade {
            entry_price: position.entry_price,
            exit_price: close_price,
            lot_size: lots,
            is_long: position.is_long,
            pnl: pnl - commission * 2.0,
            entry_timestamp: position.entry_timestamp,
            exit_timestamp: timestamp,
        });
    }

    pub fn check_sl_tp(&mut self, bar: &Bar) {
        let mut i = 0;
        while i < self.positions.len() {
            let fill = {
                let p = &self.positions[i];
                if p.is_long {
                    let sl_hit = p.stop_loss.is_some_and(|sl| bar.low <= sl);
                    let tp_hit = p.take_profit.is_some_and(|tp| bar.high >= tp);
                    if sl_hit {
                        p.stop_loss
                    } else if tp_hit {
                        p.take_profit
                    } else {
                        None
                    }
                } else {
                    let sl_hit = p.stop_loss.is_some_and(|sl| bar.high >= sl);
                    let tp_hit = p.take_profit.is_some_and(|tp| bar.low <= tp);
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
                self.settle(&position, position.lot_size, fill_price, bar.timestamp);
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
        let fill_price = self.ask(price);
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
        let fill_price = self.bid(price);
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
            self.settle(&position, position.lot_size, price, timestamp);
        }
    }

    /// Close part of a position, leaving the rest open. Scaling out of a winner.
    ///
    /// Asking for at least the full size just closes it outright, so callers don't
    /// have to check the remaining size before every call.
    pub fn close_partial(&mut self, id: u64, lot_size: f64, price: f64, timestamp: i64) {
        if lot_size <= 0.0 {
            return; // closing zero (or negative) lots would book PnL out of thin air
        }
        let Some(index) = self.positions.iter().position(|p| p.id == id) else {
            return;
        };

        if lot_size >= self.positions[index].lot_size {
            let position = self.positions.remove(index);
            self.settle(&position, position.lot_size, price, timestamp);
            return;
        }

        // Clone first: settle needs &mut self, so it can't also hold a borrow into
        // self.positions. A Position is seven numbers, the copy costs nothing.
        let position = self.positions[index].clone();
        self.positions[index].lot_size -= lot_size;
        self.settle(&position, lot_size, price, timestamp);
    }

    /// Move a position's stop loss. This is how a trailing stop works: the strategy
    /// calls it each bar as price advances, instead of closing and reopening (which
    /// would pay spread and commission all over again).
    ///
    /// No check that the stop only moves in the profitable direction — widening a stop
    /// is a legitimate thing to do, so that call belongs to the strategy. Returns false
    /// if no position has that id.
    pub fn update_sl(&mut self, id: u64, stop_loss: f64) -> bool {
        match self.positions.iter_mut().find(|p| p.id == id) {
            Some(position) => {
                position.stop_loss = Some(stop_loss);
                true
            }
            None => false,
        }
    }

    pub fn close_all(&mut self, price: f64, timestamp: i64) {
        // take() hands us the Vec by value, so settle is free to borrow self mutably.
        // drain() would still be holding a borrow on self.positions here.
        for position in std::mem::take(&mut self.positions) {
            self.settle(&position, position.lot_size, price, timestamp);
        }
    }

    pub fn equity(&self, current_price: f64) -> f64 {
        let unrealized: f64 = self
            .positions
            .iter()
            .map(|p| {
                if p.is_long {
                    (self.bid(current_price) - p.entry_price)
                        * p.lot_size
                        * self.contract_size
                        * self.quote_to_account
                } else {
                    (p.entry_price - self.ask(current_price))
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
    fn flat_round_trips_pay_one_full_spread_and_both_commissions() {
        for (is_long, commission) in [(true, 0.0), (false, 0.0), (true, 7.0), (false, 7.0)] {
            let mut broker = test_broker(commission, 0.0001);
            if is_long {
                broker.buy(1.1000, 1.0, 0, None, None);
            } else {
                broker.sell(1.1000, 1.0, 0, None, None);
            }
            let expected_entry = if is_long { 1.10005 } else { 1.09995 };
            let expected_exit = if is_long { 1.09995 } else { 1.10005 };
            assert_close(broker.positions[0].entry_price, expected_entry);
            assert_close(broker.cash, 10_000.0 - commission);
            // $10 spread + entry commission; exit commission is not yet paid.
            assert_close(broker.equity(1.1000), 9_990.0 - commission);

            broker.close_all(1.1000, 1);
            assert_close(broker.trade_history[0].exit_price, expected_exit);
            assert_close(broker.trade_history[0].pnl, -10.0 - 2.0 * commission);
            assert_close(broker.cash, 9_990.0 - 2.0 * commission);
            assert_close(broker.equity(1.1000), broker.cash);
        }
    }

    #[test]
    fn partial_and_individual_closes_reconcile_spread_and_conversion() {
        for is_long in [true, false] {
            let mut broker = Broker::new(10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
            if is_long {
                broker.buy(1.1000, 1.0, 0, None, None);
            } else {
                broker.sell(1.1000, 1.0, 0, None, None);
            }
            assert_close(broker.equity(1.1000), 9_980.3);
            broker.close_partial(0, 0.4, 1.1000, 1);
            assert_close(broker.trade_history[0].pnl, -10.68);
            broker.close_position(0, 1.1000, 2);
            assert_close(broker.trade_history[1].pnl, -16.02);
            assert_close(broker.cash, 9_973.3);
            assert_close(
                broker.cash - broker.initial_cash,
                broker.trade_history.iter().map(|trade| trade.pnl).sum(),
            );
        }
    }

    #[test]
    fn midpoint_stop_and_target_exits_use_closing_bid_or_ask() {
        for is_long in [true, false] {
            for use_stop in [true, false] {
                let mut broker = test_broker(0.0, 0.0001);
                let level = if is_long == use_stop { 1.0950 } else { 1.1050 };
                let sl = use_stop.then_some(level);
                let tp = (!use_stop).then_some(level);
                if is_long {
                    broker.buy(1.1000, 1.0, 0, sl, tp);
                } else {
                    broker.sell(1.1000, 1.0, 0, sl, tp);
                }
                broker.check_sl_tp(&Bar::new(1, level, level, level, level, 0.0));
                assert!(broker.positions.is_empty());
                let exit = if is_long {
                    level - 0.00005
                } else {
                    level + 0.00005
                };
                assert_close(broker.trade_history[0].exit_price, exit);
                assert_close(
                    broker.trade_history[0].pnl,
                    if use_stop { -510.0 } else { 490.0 },
                );
            }
        }
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
    fn close_partial_books_half_and_leaves_the_rest_open() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);

        broker.close_partial(0, 0.4, 1.1050, 1);

        // 0.4 lots booked: (1.1050 - 1.1000) * 0.4 * 100_000 = 200.0
        assert_close(broker.cash, 10_000.0 + 200.0);
        assert_eq!(broker.trade_history.len(), 1);
        assert_close(broker.trade_history[0].lot_size, 0.4);
        // 0.6 lots still riding, at the original entry
        assert_eq!(broker.positions.len(), 1);
        assert_close(broker.positions[0].lot_size, 0.6);
        assert_close(broker.positions[0].entry_price, 1.1000);
    }

    #[test]
    fn closing_partially_twice_matches_closing_once() {
        let mut scaled_out = test_broker(0.0, 0.0);
        scaled_out.buy(1.1000, 1.0, 0, None, None);
        scaled_out.close_partial(0, 0.5, 1.1050, 1);
        scaled_out.close_partial(0, 0.5, 1.1050, 1);

        let mut all_at_once = test_broker(0.0, 0.0);
        all_at_once.buy(1.1000, 1.0, 0, None, None);
        all_at_once.close_all(1.1050, 1);

        assert_close(scaled_out.cash, all_at_once.cash);
        assert_eq!(scaled_out.positions.len(), 0);
    }

    #[test]
    fn close_partial_charges_commission_only_on_the_lots_closed() {
        let mut broker = test_broker(10.0, 0.0); // $10 per lot
        broker.buy(1.1000, 1.0, 0, None, None);
        assert_close(broker.cash, 10_000.0 - 10.0); // entry: full 1.0 lot

        broker.close_partial(0, 0.25, 1.1000, 1); // flat price, so cost only
        assert_close(broker.cash, 10_000.0 - 10.0 - 2.5); // exit: 0.25 lot only
        assert_close(broker.trade_history[0].pnl, -5.0); // both legs of 0.25 lot
    }

    #[test]
    fn close_partial_beyond_the_position_size_closes_it_outright() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 0.5, 0, None, None);

        broker.close_partial(0, 99.0, 1.1050, 1);

        assert_eq!(broker.positions.len(), 0);
        assert_close(broker.trade_history[0].lot_size, 0.5); // billed for 0.5, not 99
    }

    #[test]
    fn close_partial_ignores_zero_and_unknown_ids() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);

        broker.close_partial(0, 0.0, 1.1050, 1); // zero lots
        broker.close_partial(99, 0.5, 1.1050, 1); // no such position

        assert_eq!(broker.trade_history.len(), 0);
        assert_close(broker.cash, 10_000.0);
        assert_close(broker.positions[0].lot_size, 1.0);
    }

    #[test]
    fn update_sl_moves_the_stop_and_the_new_one_is_what_fills() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, Some(1.0950), None);

        // price ran up, so trail the stop to lock in a profit
        assert!(broker.update_sl(0, 1.1020));
        assert_eq!(broker.positions[0].stop_loss, Some(1.1020));

        // this bar dips to 1.1010: through the new stop, nowhere near the old one
        broker.check_sl_tp(&Bar::new(1, 1.1050, 1.1060, 1.1010, 1.1040, 0.0));

        assert_eq!(broker.positions.len(), 0);
        assert_eq!(broker.trade_history[0].exit_price, 1.1020);
        assert_close(broker.trade_history[0].pnl, 200.0); // (1.1020 - 1.1000) * 100_000
    }

    #[test]
    fn update_sl_reports_an_unknown_id() {
        let mut broker = test_broker(0.0, 0.0);
        assert!(!broker.update_sl(42, 1.0950));
    }

    #[test]
    fn update_sl_survives_a_partial_close() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, Some(1.0950), None);
        broker.close_partial(0, 0.5, 1.1050, 1);

        // the remainder keeps the same id, so it is still addressable
        assert!(broker.update_sl(0, 1.1020));
        assert_eq!(broker.positions[0].stop_loss, Some(1.1020));
    }

    #[test]
    fn equity_includes_unrealized_pnl() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);

        // price moved up 50 pips, position still open (not closed)
        assert_close(broker.equity(1.1050), 10_000.0 + 500.0);
    }
}
