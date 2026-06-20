use crate::broker::Broker;
use crate::stats::Stats;
use crate::strategy::Strategy;
use crate::types::Bar;
use pyo3::prelude::*;

#[pyclass]
pub struct Engine {
    pub data: Vec<Bar>,
    pub broker: Broker,
    pub equity_curve: Vec<f64>,
}

impl Engine {
    pub fn run(&mut self, strategy: &mut dyn Strategy) -> Stats {
        strategy.init(&self.data);
        for bar in &self.data {
            self.broker.check_sl_tp(bar);
            strategy.next(bar, &mut self.broker);
            self.equity_curve.push(self.broker.equity(bar.close));
        }
        if let Some(last_bar) = self.data.last() {
            self.broker.close_all(last_bar.close, last_bar.timestamp);
        }
        Stats::compute(&self.broker, &self.equity_curve)
    }
}

#[pymethods]
impl Engine {
    #[new]
    pub fn new(data: Vec<Bar>, initial_cash: f64, commission: f64, spread: f64) -> Self {
        Engine {
            data,
            broker: Broker::new(initial_cash, commission, spread),
            equity_curve: Vec::new(),
        }
    }

    #[pyo3(name = "run")]
    pub fn run_py(&mut self, py: Python<'_>, strategy: Py<PyAny>) -> PyResult<Stats> {
        self.equity_curve.clear();

        let init_result = strategy.bind(py).call_method1("init", (self.data.clone(),));
        if let Err(e) = init_result {
            if !e.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) {
                return Err(e);
            }
        }

        let broker_py = Py::new(
            py,
            Broker::new(
                self.broker.initial_cash,
                self.broker.commission,
                self.broker.spread,
            ),
        )?;

        for bar in &self.data {
            {
                let mut b = broker_py.borrow_mut(py);
                b.check_sl_tp(bar);
            }
            strategy
                .bind(py)
                .call_method("next", (bar.clone(), broker_py.clone_ref(py)), None)?;
            let equity = broker_py.borrow(py).equity(bar.close);
            self.equity_curve.push(equity);
        }

        if let Some(last_bar) = self.data.last() {
            let mut b = broker_py.borrow_mut(py);
            b.close_all(last_bar.close, last_bar.timestamp);
        }

        let b = broker_py.borrow(py);
        Ok(Stats::compute(&b, &self.equity_curve))
    }
}
