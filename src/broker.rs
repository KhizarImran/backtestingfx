use crate::margin::{
    Liquidation, LiquidationReason, MarginConfig, MarginError, OrderOutcome, OrderRejection,
    OrderResult, RejectionReason, RiskState,
};
use crate::swap::SwapError;
use crate::types::{Bar, Position, SwapCharge, Trade};
use crate::validation;
use pyo3::prelude::*;

struct Entry {
    is_long: bool,
    price: f64,
    lot_size: f64,
    timestamp: i64,
    stop_loss: Option<f64>,
    take_profit: Option<f64>,
}

struct Settlement {
    close_price: f64,
    price_pnl: f64,
    commission: f64,
    swap: f64,
}

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
    #[pyo3(get)]
    pub total_swap: f64,
    pub swap_charges: Vec<SwapCharge>,
    pub(crate) risk: RiskState,
    pub(crate) order_rejections: Vec<OrderRejection>,
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
    fn settlement(&self, position: &Position, lots: f64, price: f64) -> Settlement {
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
        // Financing is already in cash. Allocate it only to the trade's result.
        let swap = if lots == position.lot_size {
            position.swap
        } else {
            position.swap * (lots / position.lot_size)
        };
        Settlement {
            close_price,
            price_pnl: pnl,
            commission,
            swap,
        }
    }

    fn settle(&mut self, position: &Position, lots: f64, price: f64, timestamp: i64) {
        let Settlement {
            close_price,
            price_pnl: pnl,
            commission,
            swap,
        } = self.settlement(position, lots, price);
        self.cash += pnl - commission;
        self.trade_history.push(Trade {
            entry_price: position.entry_price,
            exit_price: close_price,
            lot_size: lots,
            is_long: position.is_long,
            pnl: pnl - commission * 2.0 + swap,
            entry_timestamp: position.entry_timestamp,
            exit_timestamp: timestamp,
            swap,
        });
    }

    pub(crate) fn apply_swap(
        &mut self,
        timestamp: i64,
        multiplier: f64,
        long: f64,
        short: f64,
    ) -> Result<(), SwapError> {
        let mut amounts = Vec::with_capacity(self.positions.len());
        let mut cash = self.cash;
        let mut total_swap = self.total_swap;
        for position in &self.positions {
            let rate = if position.is_long { long } else { short };
            let amount = if position.entry_timestamp < timestamp {
                rate * position.lot_size * multiplier
            } else {
                0.0
            };
            cash += amount;
            total_swap += amount;
            if !amount.is_finite()
                || !(position.swap + amount).is_finite()
                || !cash.is_finite()
                || !total_swap.is_finite()
            {
                return Err(SwapError::ArithmeticOverflow);
            }
            amounts.push(amount);
        }
        self.cash = cash;
        self.total_swap = total_swap;
        for (position, amount) in self.positions.iter_mut().zip(amounts) {
            if amount == 0.0 {
                continue;
            }
            position.swap += amount;
            self.swap_charges.push(SwapCharge {
                position_id: position.id,
                timestamp,
                lot_size: position.lot_size,
                is_long: position.is_long,
                multiplier,
                amount,
            });
        }
        Ok(())
    }

    /// Inclusive midpoint triggers: process the open first, filling crossed stops
    /// and targets there. Otherwise use the level, with stop-first range ordering.
    /// The engine calls this before the strategy, so new entries wait until the next bar.
    pub fn check_sl_tp(&mut self, bar: &Bar) -> Result<(), MarginError> {
        if let RiskState::Failed { error, .. } = self.risk {
            return Err(error);
        }
        if self.risk.config().is_some() {
            let mut cash = self.cash;
            for p in &self.positions {
                if let Some(price) = Self::sl_tp_fill(p, bar) {
                    cash = match self.checked_settlement_cash(p, p.lot_size, price, cash) {
                        Ok(cash) => cash,
                        Err(error) => return Err(self.fail(error)),
                    };
                }
            }
        }
        // Move the Vec out so settlement can borrow self while retain compacts
        // survivors in place. This preserves capacity and both observable orders,
        // without shifting the entire suffix after every triggered position.
        let mut positions = std::mem::take(&mut self.positions);
        positions.retain(|p| {
            if let Some(fill_price) = Self::sl_tp_fill(p, bar) {
                self.settle(p, p.lot_size, fill_price, bar.timestamp);
                false
            } else {
                true
            }
        });
        self.positions = positions;
        Ok(())
    }

    fn sl_tp_fill(p: &Position, bar: &Bar) -> Option<f64> {
        // The open is observed before the unknown intrabar path. A stop gap
        // takes the adverse opening price; a target gap gets price improvement,
        // even if the range later reaches the other level. settle applies spread.
        let stop_at_open = p.stop_loss.is_some_and(|sl| {
            if p.is_long {
                bar.open <= sl
            } else {
                bar.open >= sl
            }
        });
        let target_at_open = p.take_profit.is_some_and(|tp| {
            if p.is_long {
                bar.open >= tp
            } else {
                bar.open <= tp
            }
        });
        {
            if stop_at_open || target_at_open {
                Some(bar.open)
            } else if p.is_long {
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
        }
    }
}

impl Broker {
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
            total_swap: 0.0,
            swap_charges: Vec::new(),
            risk: RiskState::Disabled,
            order_rejections: Vec::new(),
        }
    }

    pub fn positions(&self) -> Vec<Position> {
        self.positions.clone()
    }

    pub fn with_margin(mut self, config: MarginConfig) -> Self {
        self.risk = RiskState::Active(config);
        self
    }

    /// Retain configuration and discard all previous run state.
    pub fn fresh(&self) -> Self {
        let mut broker = Self::new(
            self.initial_cash,
            self.commission,
            self.spread,
            self.contract_size,
            self.quote_to_account,
        );
        if let Some(config) = self.risk.config() {
            broker.risk = RiskState::Active(config);
        }
        broker
    }

    pub fn buy(
        &mut self,
        price: f64,
        lot_size: f64,
        timestamp: i64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
    ) -> OrderResult {
        self.open(Entry {
            is_long: true,
            price,
            lot_size,
            timestamp,
            stop_loss,
            take_profit,
        })
    }

    pub fn sell(
        &mut self,
        price: f64,
        lot_size: f64,
        timestamp: i64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
    ) -> OrderResult {
        self.open(Entry {
            is_long: false,
            price,
            lot_size,
            timestamp,
            stop_loss,
            take_profit,
        })
    }

    fn open(&mut self, entry: Entry) -> OrderResult {
        if let Some(result) = self.admit(&entry) {
            return result;
        }
        let Entry {
            is_long,
            price,
            lot_size,
            timestamp,
            stop_loss,
            take_profit,
        } = entry;
        let fill_price = if is_long {
            self.ask(price)
        } else {
            self.bid(price)
        };
        self.cash -= self.commission * lot_size;
        let id = self.next_id;
        self.next_id += 1;
        self.positions.push(Position {
            id,
            entry_price: fill_price,
            lot_size,
            is_long,
            entry_timestamp: timestamp,
            stop_loss,
            take_profit,
            swap: 0.0,
        });
        OrderResult {
            outcome: OrderOutcome::Filled(id),
        }
    }

    pub fn close_position(
        &mut self,
        id: u64,
        price: f64,
        timestamp: i64,
    ) -> Result<(), MarginError> {
        if let Some(index) = self.positions.iter().position(|p| p.id == id) {
            let p = &self.positions[index];
            if let Err(error) = self.checked_settlement_cash(p, p.lot_size, price, self.cash) {
                return Err(self.fail(error));
            }
            let position = self.positions.remove(index);
            self.settle(&position, position.lot_size, price, timestamp);
        }
        Ok(())
    }

    /// Close part of a position, leaving the rest open. Scaling out of a winner.
    ///
    /// Asking for at least the full size just closes it outright, so callers don't
    /// have to check the remaining size before every call.
    pub fn close_partial(
        &mut self,
        id: u64,
        lot_size: f64,
        price: f64,
        timestamp: i64,
    ) -> Result<(), MarginError> {
        if lot_size <= 0.0 {
            return Ok(()); // closing zero (or negative) lots would book PnL out of thin air
        }
        let Some(index) = self.positions.iter().position(|p| p.id == id) else {
            return Ok(());
        };

        if lot_size >= self.positions[index].lot_size {
            return self.close_position(id, price, timestamp);
        }

        if let Err(error) =
            self.checked_settlement_cash(&self.positions[index], lot_size, price, self.cash)
        {
            return Err(self.fail(error));
        }

        // Keep the original lots and financing for settlement before reducing
        // the survivor. Settlement also needs a separate borrow of self.
        let position = self.positions[index].clone();
        self.positions[index].lot_size -= lot_size;
        self.positions[index].swap -= position.swap * (lot_size / position.lot_size);
        self.settle(&position, lot_size, price, timestamp);
        Ok(())
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

    pub fn close_all(&mut self, price: f64, timestamp: i64) -> Result<(), MarginError> {
        if let Err(error) = self.checked_close_cash(price) {
            return Err(self.fail(error));
        }
        // take() hands us the Vec by value, so settle is free to borrow self mutably.
        // drain() would still be holding a borrow on self.positions here.
        for position in std::mem::take(&mut self.positions) {
            self.settle(&position, position.lot_size, price, timestamp);
        }
        Ok(())
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

impl Broker {
    pub fn is_active(&self) -> bool {
        matches!(self.risk, RiskState::Disabled | RiskState::Active(_))
    }

    fn valuation(&self, price: f64) -> Result<(f64, f64), MarginError> {
        let equity = self.equity(price);
        let lots: f64 = self.positions.iter().map(|p| p.lot_size).sum();
        let used = self.risk.config().map_or(0.0, |config| {
            config.required(lots, price, self.contract_size, self.quote_to_account)
        });
        if !equity.is_finite() || !used.is_finite() {
            return Err(MarginError::ArithmeticOverflow);
        }
        Ok((equity, used))
    }

    pub fn used_margin(&self, price: f64) -> Result<f64, MarginError> {
        Ok(self.valuation(price)?.1)
    }

    pub fn free_margin(&self, price: f64) -> Result<f64, MarginError> {
        let (equity, used) = self.valuation(price)?;
        Ok(equity - used)
    }

    fn reject(&mut self, rejection: OrderRejection) -> OrderResult {
        self.order_rejections.push(rejection.clone());
        OrderResult {
            outcome: OrderOutcome::Rejected(rejection),
        }
    }

    fn admit(&mut self, entry: &Entry) -> Option<OrderResult> {
        self.risk.config()?;
        let mut rejection = OrderRejection {
            timestamp: entry.timestamp,
            is_long: entry.is_long,
            lot_size: entry.lot_size,
            price: entry.price,
            reason: RejectionReason::ArithmeticOverflow,
            equity_after_entry: None,
            required_margin: None,
        };
        if self.enforce_margin(entry.price, entry.timestamp).is_err() {
            return Some(self.reject(rejection));
        }
        if !self.is_active() {
            rejection.reason = RejectionReason::AccountLiquidated;
            return Some(self.reject(rejection));
        }
        let config = self.risk.config()?;
        let (equity, _) = match self.valuation(entry.price) {
            Ok(mark) => mark,
            Err(_) => return Some(self.reject(rejection)),
        };
        let bid = self.bid(entry.price);
        let ask = self.ask(entry.price);
        let after = equity - self.commission * entry.lot_size
            + (bid - ask) * entry.lot_size * self.contract_size * self.quote_to_account;
        let resulting_lots =
            self.positions.iter().map(|p| p.lot_size).sum::<f64>() + entry.lot_size;
        let required = config.required(
            resulting_lots,
            entry.price,
            self.contract_size,
            self.quote_to_account,
        );
        rejection.equity_after_entry = after.is_finite().then_some(after);
        rejection.required_margin = required.is_finite().then_some(required);
        if !bid.is_finite()
            || !ask.is_finite()
            || !resulting_lots.is_finite()
            || !after.is_finite()
            || !required.is_finite()
        {
            return Some(self.reject(rejection));
        }
        if after <= 0.0 || after < required {
            rejection.reason = RejectionReason::InsufficientMargin;
            return Some(self.reject(rejection));
        }
        None
    }

    fn fail(&mut self, error: MarginError) -> MarginError {
        if let Some(config) = self.risk.config() {
            self.risk = RiskState::Failed { config, error };
        }
        error
    }

    fn checked_settlement_cash(
        &self,
        p: &Position,
        lots: f64,
        price: f64,
        cash: f64,
    ) -> Result<f64, MarginError> {
        if let RiskState::Failed { error, .. } = self.risk {
            return Err(error);
        }
        if self.risk.config().is_none() {
            return Ok(cash);
        }
        let result = self.settlement(p, lots, price);
        let after = cash + (result.price_pnl - result.commission);
        if !result.close_price.is_finite()
            || !after.is_finite()
            || !(result.price_pnl - 2.0 * result.commission + result.swap).is_finite()
        {
            return Err(MarginError::ArithmeticOverflow);
        }
        Ok(after)
    }

    /// Preflight the whole batch before any position, fee, or ledger is changed.
    fn checked_close_cash(&self, price: f64) -> Result<(), MarginError> {
        if let RiskState::Failed { error, .. } = self.risk {
            return Err(error);
        }
        if self.risk.config().is_none() {
            return Ok(());
        }
        let mut cash = self.cash;
        for p in &self.positions {
            cash = self.checked_settlement_cash(p, p.lot_size, price, cash)?;
        }
        Ok(())
    }

    #[inline]
    fn enforce_margin(&mut self, price: f64, timestamp: i64) -> Result<(), MarginError> {
        match self.risk {
            RiskState::Disabled | RiskState::Liquidated { .. } => Ok(()),
            RiskState::Active(config) => self.enforce_active_margin(config, price, timestamp),
            RiskState::Failed { error, .. } => Err(error),
        }
    }

    fn enforce_active_margin(
        &mut self,
        config: MarginConfig,
        price: f64,
        timestamp: i64,
    ) -> Result<(), MarginError> {
        let (equity, used) = self.valuation(price)?;
        let reason = if equity <= 0.0 {
            LiquidationReason::NonpositiveEquity
        } else if !self.positions.is_empty() && equity <= config.maintenance(used) {
            LiquidationReason::MaintenanceMargin
        } else {
            return Ok(());
        };
        let count = self.positions.len();
        self.close_all(price, timestamp)?;
        self.risk = RiskState::Liquidated {
            config,
            event: Liquidation {
                timestamp,
                price,
                reason,
                equity_before: equity,
                used_margin: used,
                positions_closed: count,
                cash_after: self.cash,
            },
        };
        Ok(())
    }

    #[inline]
    pub(crate) fn begin_bar(&mut self, bar: &Bar) -> Result<(), MarginError> {
        self.enforce_margin(bar.open, bar.timestamp)?;
        self.check_sl_tp(bar)?;
        self.enforce_margin(bar.close, bar.timestamp)
    }

    #[inline]
    pub(crate) fn end_bar(&mut self, bar: &Bar) -> Result<f64, MarginError> {
        self.enforce_margin(bar.close, bar.timestamp)?;
        Ok(self.equity(bar.close))
    }

    pub(crate) fn finalize(&mut self, bar: &Bar) -> Result<(), MarginError> {
        self.close_all(bar.close, bar.timestamp)?;
        self.enforce_margin(bar.close, bar.timestamp)
    }
}

// Python inputs are checked before the native methods can mutate account state.
#[pymethods]
impl Broker {
    #[new]
    #[pyo3(signature = (initial_cash, commission, spread, contract_size, quote_to_account, *, margin=None))]
    fn py_new(
        initial_cash: f64,
        commission: f64,
        spread: f64,
        contract_size: f64,
        quote_to_account: f64,
        margin: Option<MarginConfig>,
    ) -> PyResult<Self> {
        validation::config(
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        )?;
        let mut broker = Self::new(
            initial_cash,
            commission,
            spread,
            contract_size,
            quote_to_account,
        );
        if let Some(config) = margin {
            broker = broker.with_margin(config);
        }
        Ok(broker)
    }

    #[pyo3(name = "positions")]
    fn py_positions(&self) -> Vec<Position> {
        self.positions()
    }

    #[getter]
    fn position_count(&self) -> usize {
        self.positions.len()
    }

    #[getter]
    fn has_positions(&self) -> bool {
        !self.positions.is_empty()
    }

    #[getter]
    pub fn margin(&self) -> Option<MarginConfig> {
        self.risk.config()
    }

    #[getter]
    pub fn account_status(&self) -> &'static str {
        self.risk.status()
    }

    #[getter]
    pub fn liquidation(&self) -> Option<Liquidation> {
        self.risk.liquidation().cloned()
    }

    #[getter]
    fn order_rejections(&self) -> Vec<OrderRejection> {
        self.order_rejections.clone()
    }

    #[pyo3(name = "used_margin")]
    fn py_used_margin(&self, price: f64) -> PyResult<f64> {
        validation::positive("price", price)?;
        Ok(self.used_margin(price)?)
    }

    #[pyo3(name = "free_margin")]
    fn py_free_margin(&self, price: f64) -> PyResult<f64> {
        validation::positive("price", price)?;
        Ok(self.free_margin(price)?)
    }

    #[pyo3(name = "buy")]
    fn py_buy(
        &mut self,
        price: f64,
        lot_size: f64,
        timestamp: i64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
    ) -> PyResult<OrderResult> {
        validation::order(price, lot_size, stop_loss, take_profit)?;
        Ok(self.buy(price, lot_size, timestamp, stop_loss, take_profit))
    }

    #[pyo3(name = "sell")]
    fn py_sell(
        &mut self,
        price: f64,
        lot_size: f64,
        timestamp: i64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
    ) -> PyResult<OrderResult> {
        validation::order(price, lot_size, stop_loss, take_profit)?;
        Ok(self.sell(price, lot_size, timestamp, stop_loss, take_profit))
    }

    #[pyo3(name = "close_position")]
    fn py_close_position(&mut self, id: u64, price: f64, timestamp: i64) -> PyResult<()> {
        validation::positive("price", price)?;
        self.close_position(id, price, timestamp)?;
        Ok(())
    }

    #[pyo3(name = "close_partial")]
    fn py_close_partial(
        &mut self,
        id: u64,
        lot_size: f64,
        price: f64,
        timestamp: i64,
    ) -> PyResult<()> {
        validation::finite("lot_size", lot_size)?;
        validation::positive("price", price)?;
        self.close_partial(id, lot_size, price, timestamp)?;
        Ok(())
    }

    #[pyo3(name = "update_sl")]
    fn py_update_sl(&mut self, id: u64, stop_loss: f64) -> PyResult<bool> {
        validation::positive("stop_loss", stop_loss)?;
        Ok(self.update_sl(id, stop_loss))
    }

    #[pyo3(name = "close_all")]
    fn py_close_all(&mut self, price: f64, timestamp: i64) -> PyResult<()> {
        validation::positive("price", price)?;
        self.close_all(price, timestamp)?;
        Ok(())
    }

    #[pyo3(name = "equity")]
    fn py_equity(&self, current_price: f64) -> PyResult<f64> {
        validation::positive("price", current_price)?;
        Ok(self.equity(current_price))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_batch_overflow_preserves_every_position_and_cash() {
        let mut broker = Broker::new(1e308, 0.0, 0.0, 1e308, 1.0)
            .with_margin(MarginConfig::new(1e308, 0.5).unwrap());
        broker.buy(1.0, 0.1, 0, None, Some(1.1));
        broker.buy(1.0, 0.1, 0, None, Some(1e308));
        let bar = Bar::new(1, 1.0, f64::MAX, 1.0, 1.0, 0.0);
        assert_eq!(
            broker.check_sl_tp(&bar),
            Err(MarginError::ArithmeticOverflow)
        );
        assert_eq!(broker.cash, 1e308);
        assert_eq!(broker.positions.len(), 2);
        assert!(broker.trade_history.is_empty());
        assert_eq!(broker.account_status(), "failed");
    }

    fn test_broker(commission: f64, spread: f64) -> Broker {
        Broker::new(10_000.0, commission, spread, 100_000.0, 1.0)
    }

    // f64 arithmetic isn't exact, so compare with a tolerance instead of assert_eq!
    fn assert_close(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-6, "expected {b}, got {a}");
    }

    // 0 = survivor, 1 = stop only, 2 = target only, 3 = both touched.
    // Unique timestamps and sizes identify settlement order independently of IDs.
    fn check_batch_exits(kinds: &[u8]) {
        for long_first in [true, false] {
            let mut broker = Broker::new(10_000.0, 7.0, 0.0001, 100_000.0, 1.27);
            let mut survivors = Vec::new();
            let mut expected_trades = Vec::new();
            let mut expected_cash = 10_000.0;
            for (i, &kind) in kinds.iter().enumerate() {
                let is_long = if i % 2 == 0 { long_first } else { !long_first };
                let lots = (i + 1) as f64 * 0.01;
                let stop = if is_long { 1.09 } else { 1.11 };
                let target = if is_long { 1.11 } else { 1.09 };
                let sl = if kind == 1 || kind == 3 {
                    Some(stop)
                } else if kind == 0 && i % 2 == 0 {
                    Some(if is_long { 1.08 } else { 1.12 })
                } else {
                    None
                };
                let tp = if kind == 2 || kind == 3 {
                    Some(target)
                } else if kind == 0 && i % 2 == 0 {
                    Some(if is_long { 1.12 } else { 1.08 })
                } else {
                    None
                };
                if is_long {
                    broker.buy(1.1, lots, i as i64, sl, tp);
                } else {
                    broker.sell(1.1, lots, i as i64, sl, tp);
                }
                expected_cash -= 7.0 * lots;
                if kind == 0 {
                    survivors.push(broker.positions.last().unwrap().clone());
                } else {
                    let fill = if kind == 2 { target } else { stop };
                    let exit = fill + if is_long { -0.00005 } else { 0.00005 };
                    let gross_pnl =
                        (if kind == 2 { 0.0099 } else { -0.0101 }) * lots * 100_000.0 * 1.27;
                    expected_cash += gross_pnl - 7.0 * lots;
                    expected_trades.push((i as i64, is_long, lots, exit, gross_pnl - 14.0 * lots));
                }
            }
            let capacity = broker.positions.capacity();
            let bar = Bar::new(100, 1.1, 1.11, 1.09, 1.1, 0.0);
            broker.check_sl_tp(&bar).unwrap();
            assert_eq!(broker.positions.capacity(), capacity);
            assert_eq!(broker.positions.len(), survivors.len());
            for (actual, expected) in broker.positions.iter().zip(&survivors) {
                assert_eq!(actual.id, expected.id);
                assert_eq!(actual.entry_timestamp, expected.entry_timestamp);
                assert_eq!(actual.is_long, expected.is_long);
                assert_eq!(actual.entry_price, expected.entry_price);
                assert_eq!(actual.lot_size, expected.lot_size);
                assert_eq!(actual.stop_loss, expected.stop_loss);
                assert_eq!(actual.take_profit, expected.take_profit);
            }
            assert_eq!(broker.trade_history.len(), expected_trades.len());
            for (trade, &(timestamp, is_long, lots, exit, pnl)) in
                broker.trade_history.iter().zip(&expected_trades)
            {
                assert_eq!(trade.entry_timestamp, timestamp);
                assert_eq!(trade.exit_timestamp, 100);
                assert_eq!(trade.is_long, is_long);
                assert_close(trade.lot_size, lots);
                assert_close(trade.exit_price, exit);
                assert_close(trade.pnl, pnl);
            }
            assert_close(broker.cash, expected_cash);
            // A second scan must not settle a closed position twice.
            broker.check_sl_tp(&bar).unwrap();
            assert_eq!(broker.trade_history.len(), expected_trades.len());
            assert_close(broker.cash, expected_cash);
            broker.close_all(1.1, 101).unwrap();
            assert_close(
                broker.cash - broker.initial_cash,
                broker.trade_history.iter().map(|trade| trade.pnl).sum(),
            );
        }
    }

    #[test]
    fn batch_sl_tp_all_triggered_preserves_settlement_and_stop_precedence() {
        check_batch_exits(&[1, 2, 3, 1, 2, 3]);
    }

    #[test]
    fn batch_sl_tp_none_triggered_preserves_positions() {
        check_batch_exits(&[0, 0, 0, 0]);
        check_batch_exits(&[]);
    }

    #[test]
    fn batch_sl_tp_interleaved_preserves_survivors_and_settlement_order() {
        check_batch_exits(&[0, 1, 0, 2, 3, 0, 1, 0, 2, 0]);
    }

    #[test]
    fn buy_then_close_all_computes_pnl() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);
        broker.close_all(1.1050, 1).unwrap();

        // (exit - entry) * lot_size * contract_size = (1.1050 - 1.1000) * 1.0 * 100_000 = 500.0
        assert_close(broker.cash, 10_000.0 + 500.0);
        assert_eq!(broker.trade_history.len(), 1);
        assert_close(broker.trade_history[0].pnl, 500.0);
    }

    #[test]
    fn sell_then_close_all_computes_pnl() {
        let mut broker = test_broker(0.0, 0.0);
        broker.sell(1.1000, 1.0, 0, None, None);
        broker.close_all(1.0950, 1).unwrap();

        // short profits when price falls: (entry - exit) * lot_size * contract_size = 500.0
        assert_close(broker.cash, 10_000.0 + 500.0);
    }

    #[test]
    fn commission_deducted_on_open_and_close() {
        let mut broker = test_broker(7.0, 0.0); // $7 per lot
        broker.buy(1.1000, 1.0, 0, None, None);
        assert_eq!(broker.cash, 10_000.0 - 7.0); // charged immediately on open

        broker.close_all(1.1000, 1).unwrap(); // same price as entry, so zero price PnL
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

            broker.close_all(1.1000, 1).unwrap();
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
            broker.close_partial(0, 0.4, 1.1000, 1).unwrap();
            assert_close(broker.trade_history[0].pnl, -10.68);
            broker.close_position(0, 1.1000, 2).unwrap();
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
                broker
                    .check_sl_tp(&Bar::new(1, level, level, level, level, 0.0))
                    .unwrap();
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
        broker.check_sl_tp(&bar).unwrap();

        assert_eq!(broker.positions.len(), 0);
        assert_eq!(broker.trade_history[0].exit_price, 1.0950); // filled at SL, not bar.close
    }

    #[test]
    fn long_position_stays_open_when_sl_tp_not_hit() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, Some(1.0950), Some(1.1200));

        let bar = Bar::new(1, 1.1020, 1.1050, 1.1010, 1.1030, 0.0); // stays inside range
        broker.check_sl_tp(&bar).unwrap();

        assert_eq!(broker.positions.len(), 1);
    }

    #[test]
    fn short_position_closes_at_stop_loss() {
        let mut broker = test_broker(0.0, 0.0);
        broker.sell(1.1000, 1.0, 0, Some(1.1050), None);

        // stop loss sits above entry for a short; bar's high pokes through it
        let bar = Bar::new(1, 1.1020, 1.1080, 1.1010, 1.1030, 0.0);
        broker.check_sl_tp(&bar).unwrap();

        assert_eq!(broker.positions.len(), 0);
        assert_eq!(broker.trade_history[0].exit_price, 1.1050);
    }

    #[test]
    fn close_position_closes_only_the_matching_id() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None); // id 0
        broker.buy(1.2000, 1.0, 0, None, None); // id 1

        broker.close_position(0, 1.1050, 1).unwrap();

        assert_eq!(broker.positions.len(), 1);
        assert_eq!(broker.positions[0].id, 1); // id 1 left untouched
        assert_eq!(broker.trade_history.len(), 1);
        assert_close(broker.trade_history[0].pnl, 500.0); // (1.1050 - 1.1000) * 1.0 * 100_000
    }

    #[test]
    fn close_partial_books_half_and_leaves_the_rest_open() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);

        broker.close_partial(0, 0.4, 1.1050, 1).unwrap();

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
        scaled_out.close_partial(0, 0.5, 1.1050, 1).unwrap();
        scaled_out.close_partial(0, 0.5, 1.1050, 1).unwrap();

        let mut all_at_once = test_broker(0.0, 0.0);
        all_at_once.buy(1.1000, 1.0, 0, None, None);
        all_at_once.close_all(1.1050, 1).unwrap();

        assert_close(scaled_out.cash, all_at_once.cash);
        assert_eq!(scaled_out.positions.len(), 0);
    }

    #[test]
    fn close_partial_charges_commission_only_on_the_lots_closed() {
        let mut broker = test_broker(10.0, 0.0); // $10 per lot
        broker.buy(1.1000, 1.0, 0, None, None);
        assert_close(broker.cash, 10_000.0 - 10.0); // entry: full 1.0 lot

        broker.close_partial(0, 0.25, 1.1000, 1).unwrap(); // flat price, so cost only
        assert_close(broker.cash, 10_000.0 - 10.0 - 2.5); // exit: 0.25 lot only
        assert_close(broker.trade_history[0].pnl, -5.0); // both legs of 0.25 lot
    }

    #[test]
    fn close_partial_beyond_the_position_size_closes_it_outright() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 0.5, 0, None, None);

        broker.close_partial(0, 99.0, 1.1050, 1).unwrap();

        assert_eq!(broker.positions.len(), 0);
        assert_close(broker.trade_history[0].lot_size, 0.5); // billed for 0.5, not 99
    }

    #[test]
    fn close_partial_ignores_zero_and_unknown_ids() {
        let mut broker = test_broker(0.0, 0.0);
        broker.buy(1.1000, 1.0, 0, None, None);

        broker.close_partial(0, 0.0, 1.1050, 1).unwrap(); // zero lots
        broker.close_partial(99, 0.5, 1.1050, 1).unwrap(); // no such position

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
        broker
            .check_sl_tp(&Bar::new(1, 1.1050, 1.1060, 1.1010, 1.1040, 0.0))
            .unwrap();

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
        broker.close_partial(0, 0.5, 1.1050, 1).unwrap();

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

    #[test]
    fn ambiguous_bars_choose_stop_for_longs_and_shorts_including_exact_touches() {
        for is_long in [true, false] {
            for exact_touch in [true, false] {
                let mut broker = test_broker(7.0, 0.0001);
                let (stop, target) = if is_long { (1.09, 1.11) } else { (1.11, 1.09) };
                if is_long {
                    broker.buy(1.1, 1.0, 0, Some(stop), Some(target));
                } else {
                    broker.sell(1.1, 1.0, 0, Some(stop), Some(target));
                }
                let (high, low) = if exact_touch {
                    (1.11, 1.09)
                } else {
                    (1.12, 1.08)
                };
                broker
                    .check_sl_tp(&Bar::new(1, 1.1, high, low, 1.1, 0.0))
                    .unwrap();

                assert!(broker.positions.is_empty());
                assert_eq!(broker.trade_history.len(), 1);
                let trade = &broker.trade_history[0];
                assert_close(
                    trade.exit_price,
                    if is_long {
                        stop - 0.00005
                    } else {
                        stop + 0.00005
                    },
                );
                assert_close(trade.pnl, -1024.0);
                assert_close(broker.cash, 8976.0);
                assert_eq!(trade.exit_timestamp, 1);
            }
        }
    }

    #[test]
    fn stops_at_or_beyond_open_fill_at_open_even_if_price_recovers() {
        for is_long in [true, false] {
            for exact_touch in [true, false] {
                for recovers in [true, false] {
                    for spread in [0.0, 0.0001] {
                        let mut broker = Broker::new(10_000.0, 7.0, spread, 100_000.0, 1.27);
                        let (stop, target, gap) = if is_long {
                            (1.09, 1.11, 1.08)
                        } else {
                            (1.11, 1.09, 1.12)
                        };
                        let open = if exact_touch { stop } else { gap };
                        if is_long {
                            broker.buy(1.1, 1.0, 0, Some(stop), Some(target));
                        } else {
                            broker.sell(1.1, 1.0, 0, Some(stop), Some(target));
                        }
                        // Recovery can touch the target later, after the opening stop fill.
                        let bar = if recovers {
                            Bar::new(1, open, 1.12, 1.08, 1.1, 0.0)
                        } else {
                            Bar::new(1, open, open, open, open, 0.0)
                        };
                        broker.check_sl_tp(&bar).unwrap();
                        assert!(broker.positions.is_empty());
                        assert_eq!(broker.trade_history.len(), 1);
                        let trade = &broker.trade_history[0];
                        let exit = open + if is_long { -spread / 2.0 } else { spread / 2.0 };
                        let loss = if exact_touch { 1000.0 } else { 2000.0 };
                        assert_close(trade.exit_price, exit);
                        assert_close(trade.pnl, (-loss - spread * 100_000.0) * 1.27 - 14.0);
                        assert_close(broker.cash - 10_000.0, trade.pnl);
                        assert_eq!(trade.exit_timestamp, 1);
                    }
                }
            }
        }
    }

    #[test]
    fn targets_at_or_beyond_open_fill_before_a_later_stop() {
        for is_long in [true, false] {
            for exact_touch in [true, false] {
                for reverses in [true, false] {
                    for spread in [0.0, 0.0001] {
                        let mut broker = Broker::new(10_000.0, 7.0, spread, 100_000.0, 1.27);
                        let (stop, target, gap) = if is_long {
                            (1.09, 1.11, 1.12)
                        } else {
                            (1.11, 1.09, 1.08)
                        };
                        let open = if exact_touch { target } else { gap };
                        if is_long {
                            broker.buy(1.1, 1.0, 0, Some(stop), Some(target));
                        } else {
                            broker.sell(1.1, 1.0, 0, Some(stop), Some(target));
                        }
                        let bar = if reverses {
                            Bar::new(1, open, 1.12, 1.08, 1.1, 0.0)
                        } else {
                            Bar::new(1, open, open, open, open, 0.0)
                        };
                        broker.check_sl_tp(&bar).unwrap();
                        assert!(broker.positions.is_empty());
                        assert_eq!(broker.trade_history.len(), 1);
                        let trade = &broker.trade_history[0];
                        let exit = open + if is_long { -spread / 2.0 } else { spread / 2.0 };
                        let profit = if exact_touch { 1000.0 } else { 2000.0 };
                        assert_close(trade.exit_price, exit);
                        assert_close(trade.pnl, (profit - spread * 100_000.0) * 1.27 - 14.0);
                        assert_close(broker.cash - 10_000.0, trade.pnl);
                        assert_eq!(trade.exit_timestamp, 1);
                    }
                }
            }
        }
    }

    #[test]
    fn opening_bid_or_ask_crossing_a_level_does_not_trigger_a_midpoint_exit() {
        for is_long in [true, false] {
            for use_stop in [true, false] {
                let mut broker = test_broker(7.0, 0.0001);
                let level = if is_long == use_stop { 1.09 } else { 1.11 };
                // A bid/ask crosses the level while the midpoint remains inside it.
                let open = level
                    + if is_long == use_stop {
                        0.00001
                    } else {
                        -0.00001
                    };
                let sl = use_stop.then_some(level);
                let tp = (!use_stop).then_some(level);
                if is_long {
                    broker.buy(1.1, 1.0, 0, sl, tp);
                } else {
                    broker.sell(1.1, 1.0, 0, sl, tp);
                }
                broker
                    .check_sl_tp(&Bar::new(1, open, open, open, open, 0.0))
                    .unwrap();
                assert_eq!(broker.positions.len(), 1);
                assert!(broker.trade_history.is_empty());
                assert_close(broker.cash, 9993.0);
            }
        }
    }
}
