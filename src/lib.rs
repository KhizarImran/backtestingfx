pub mod broker;
pub mod data;
pub mod engine;
pub mod stats;
pub mod strategy;
pub mod types;

use pyo3::prelude::*;

#[pymodule]
#[pyo3(name = "_backtestingfx")]
fn backtestingfx(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<types::Bar>()?;
    m.add_class::<stats::Stats>()?;
    m.add_class::<broker::Broker>()?;
    m.add_class::<engine::Engine>()?;
    m.add_class::<types::Position>()?;
    Ok(())
}
