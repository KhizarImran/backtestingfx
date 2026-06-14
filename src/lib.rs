pub mod broker;
pub mod data;
pub mod engine;
pub mod stats;
pub mod strategy;
pub mod types;

use pyo3::prelude::*;

#[pymodule]
fn backtestingfx(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<types::Bar>()?;
    m.add_class::<stats::Stats>()?;
    Ok(())
}
