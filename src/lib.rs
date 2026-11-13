pub mod broker;
pub mod data;
pub mod margin;
pub mod engine;
pub mod optimise;
pub mod stats;
pub mod strategy;
pub mod swap;
pub mod types;
mod validation;

use pyo3::prelude::*;

#[pymodule]
#[pyo3(name = "_backtestingfx")]
fn backtestingfx(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<types::Bar>()?;
    m.add_class::<stats::Stats>()?;
    m.add_class::<broker::Broker>()?;
    m.add_class::<engine::Engine>()?;
    m.add_class::<types::Position>()?;
    m.add_class::<types::Trade>()?;
    m.add_class::<swap::SwapConfig>()?;
    m.add_class::<types::SwapCharge>()?;
    m.add_class::<margin::MarginConfig>()?;
    m.add_class::<margin::OrderResult>()?;
    m.add_class::<margin::OrderRejection>()?;
    m.add_class::<margin::Liquidation>()?;
    m.add_function(wrap_pyfunction!(optimise::run_grid, m)?)?;
    m.add_function(wrap_pyfunction!(validation::config, m)?)?;
    m.add_function(wrap_pyfunction!(data::bars_from_columns, m)?)?;
    Ok(())
}
