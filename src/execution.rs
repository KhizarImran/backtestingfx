use crate::broker::Broker;
use crate::margin::MarginError;
use crate::types::Bar;

#[derive(Clone, Copy)]
pub enum MarketFill {
    NextOpen,
    PreviousClose,
}

/// Opt in to the reference market-order timeline with FX sizing and costs.
#[derive(Clone, Copy)]
pub struct ExecutionTiming {
    pub market_fill: MarketFill,
    pub finalize_trades: bool,
}

impl ExecutionTiming {
    pub(crate) fn begin(
        self,
        broker: &mut Broker,
        previous: &Bar,
        current: &Bar,
    ) -> Result<(), MarginError> {
        let fill = match self.market_fill {
            MarketFill::NextOpen => (current.open, current.timestamp),
            MarketFill::PreviousClose => (previous.close, previous.timestamp),
        };
        broker.process_market_orders(current, fill)
    }

    pub(crate) fn finish(
        self,
        broker: &mut Broker,
        data: &[Bar],
        start: usize,
        equity: &mut [f64],
    ) -> Result<(), MarginError> {
        if start < data.len() {
            let last_index = data.len() - 1;
            let last = &data[last_index];
            if self.finalize_trades && broker.is_active() {
                broker.queue_finalization();
                self.begin(broker, &data[last_index - 1], last)?;
                equity[last_index + 1] = broker.end_bar(last)?;
            }
            // The reference backfills bars skipped before the first broker tick.
            let first_mark = equity[start + 1];
            equity[1..=start].fill(first_mark);
        }
        broker.discard_market_orders();
        Ok(())
    }
}
