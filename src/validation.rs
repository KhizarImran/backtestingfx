//! Numeric checks shared by the public Python entry points.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

pub(crate) fn finite(name: &str, value: f64) -> PyResult<()> {
    if !value.is_finite() {
        return Err(PyValueError::new_err(format!("{name} must be finite")));
    }
    Ok(())
}

pub(crate) fn positive(name: &str, value: f64) -> PyResult<()> {
    finite(name, value)?;
    if value <= 0.0 {
        return Err(PyValueError::new_err(format!("{name} must be greater than zero")));
    }
    Ok(())
}

fn nonnegative(name: &str, value: f64) -> PyResult<()> {
    finite(name, value)?;
    if value < 0.0 {
        return Err(PyValueError::new_err(format!("{name} must be nonnegative")));
    }
    Ok(())
}

#[pyfunction(name = "_validate_config")]
pub(crate) fn config(
    cash: f64,
    commission: f64,
    spread: f64,
    contract_size: f64,
    quote_to_account: f64,
) -> PyResult<()> {
    positive("cash", cash)?;
    nonnegative("commission", commission)?;
    nonnegative("spread", spread)?;
    positive("contract_size", contract_size)?;
    positive("quote_to_account", quote_to_account)
}

pub(crate) fn order(
    price: f64,
    lot_size: f64,
    stop_loss: Option<f64>,
    take_profit: Option<f64>,
) -> PyResult<()> {
    positive("price", price)?;
    positive("lot_size", lot_size)?;
    if let Some(stop) = stop_loss {
        positive("stop_loss", stop)?;
    }
    if let Some(target) = take_profit {
        positive("take_profit", target)?;
    }
    Ok(())
}
