use pyo3::prelude::*;

#[pyclass(from_py_object)]
#[derive(Debug, Clone)]
pub struct Bar {
    // Initialises the interface for the bar
    #[pyo3(get)]
    pub timestamp: i64,
    #[pyo3(get)]
    pub open: f64,
    #[pyo3(get)]
    pub high: f64,
    #[pyo3(get)]
    pub low: f64,
    #[pyo3(get)]
    pub close: f64,
    #[pyo3(get)]
    pub volume: f64,
}

#[pymethods]
impl Bar {
    #[new]
    pub fn new(timestamp: i64, open: f64, high: f64, low: f64, close: f64, volume: f64) -> Self {
        Bar {
            timestamp,
            open,
            high,
            low,
            close,
            volume,
        }
    }
}

#[pyclass]
#[derive(Debug, Clone)]
pub struct Position {
    // this is for the trading position
    #[pyo3(get)]
    pub id: u64,
    #[pyo3(get)]
    pub entry_price: f64,
    #[pyo3(get)]
    pub lot_size: f64,
    #[pyo3(get)]
    pub is_long: bool,
    #[pyo3(get)]
    pub entry_timestamp: i64,
    #[pyo3(get)]
    pub stop_loss: Option<f64>,
    #[pyo3(get)]
    pub take_profit: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct Trade {
    // the actual trade
    pub entry_price: f64,
    pub exit_price: f64,
    pub lot_size: f64,
    pub is_long: bool,
    pub pnl: f64,
    pub entry_timestamp: i64,
    pub exit_timestamp: i64,
}
