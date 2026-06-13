#[derive(Debug, Clone)]
pub struct Bar {            // Initialises the interface for the bar
    pub timestamp: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

#[derive(Debug, Clone)]
pub struct Position {       // this is for the trading position
    pub id: u64,
    pub entry_price: f64,
    pub lot_size: f64,
    pub is_long: bool,
    pub entry_timestamp: i64,
    pub stop_loss: Option<f64>,
    pub take_profit: Option<f64>

}

#[derive(Debug, Clone)]
pub struct Trade {          // the actual trade 
    pub entry_price: f64,
    pub exit_price: f64,
    pub lot_size: f64,
    pub is_long: bool,
    pub pnl: f64,
    pub entry_timestamp: i64,
    pub exit_timestamp:i64
}
