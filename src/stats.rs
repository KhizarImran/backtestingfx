use crate::broker::Broker;

  pub struct Stats {
      pub initial_cash: f64,
      pub final_cash: f64,
      pub total_return_pct: f64,
      pub num_trades: usize,
      pub num_wins: usize,
      pub win_rate_pct: f64,
      pub avg_pnl: f64,
      pub best_trade: f64,
      pub worst_trade: f64,
      pub profit_factor: f64,
  }

  impl Stats {
      pub fn compute(broker: &Broker, initial_cash: f64) -> Self {
          let num_trades = broker.trade_history.len();
          let final_cash = broker.cash;
          let total_return_pct = (final_cash - initial_cash) / initial_cash * 100.0;

          let num_wins = broker.trade_history.iter().filter(|t| t.pnl > 0.0).count();
          let win_rate_pct = if num_trades > 0 {
              num_wins as f64 / num_trades as f64 * 100.0
          } else { 0.0 };

          let avg_pnl = if num_trades > 0 {
              broker.trade_history.iter().map(|t| t.pnl).sum::<f64>() / num_trades as f64
          } else { 0.0 };

          let best_trade = broker.trade_history.iter().map(|t| t.pnl)
              .fold(f64::NEG_INFINITY, f64::max);
          let worst_trade = broker.trade_history.iter().map(|t| t.pnl)
              .fold(f64::INFINITY, f64::min);

          let gross_profit: f64 = broker.trade_history.iter()
              .filter(|t| t.pnl > 0.0).map(|t| t.pnl).sum();
          let gross_loss: f64 = broker.trade_history.iter()
              .filter(|t| t.pnl < 0.0).map(|t| t.pnl.abs()).sum();
          let profit_factor = if gross_loss > 0.0 { gross_profit / gross_loss } else {
  f64::INFINITY };

          Stats {
              initial_cash,
              final_cash,
              total_return_pct,
              num_trades,
              num_wins,
              win_rate_pct,
              avg_pnl,
              best_trade: if num_trades > 0 { best_trade } else { 0.0 },
              worst_trade: if num_trades > 0 { worst_trade } else { 0.0 },
              profit_factor,
          }
      }
  }

  impl std::fmt::Display for Stats {
      fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
          write!(f,
              "--- Backtest Results ---\n\
               Initial Cash:   {:.2}\n\
               Final Cash:     {:.2}\n\
               Total Return:   {:.2}%\n\
               Trades:         {}\n\
               Win Rate:       {:.1}%\n\
               Avg PnL:        {:.5}\n\
               Best Trade:     {:.5}\n\
               Worst Trade:    {:.5}\n\
               Profit Factor:  {:.2}",
              self.initial_cash, self.final_cash, self.total_return_pct,
              self.num_trades, self.win_rate_pct, self.avg_pnl,
              self.best_trade, self.worst_trade, self.profit_factor
          )
      }
  }