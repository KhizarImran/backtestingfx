//! Validated margin policy and immutable execution evidence.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarginError {
    Leverage,
    MaintenanceRatio,
    ArithmeticOverflow,
}

impl fmt::Display for MarginError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Leverage => "leverage must be finite and at least one",
            Self::MaintenanceRatio => {
                "maintenance_ratio must be finite and strictly between zero and one"
            }
            Self::ArithmeticOverflow => {
                "margin account calculation must remain finite (arithmetic overflow)"
            }
        })
    }
}

impl std::error::Error for MarginError {}

impl From<MarginError> for PyErr {
    fn from(error: MarginError) -> Self {
        PyValueError::new_err(error.to_string())
    }
}

#[pyclass(frozen, from_py_object)]
#[derive(Debug, Clone)]
pub struct MarginConfig {
    #[pyo3(get)]
    leverage: f64,
    #[pyo3(get)]
    maintenance_ratio: f64,
}

impl MarginConfig {
    pub fn new(leverage: f64, maintenance_ratio: f64) -> Result<Self, MarginError> {
        if !leverage.is_finite() || leverage < 1.0 {
            return Err(MarginError::Leverage);
        }
        if !maintenance_ratio.is_finite() || maintenance_ratio <= 0.0 || maintenance_ratio >= 1.0 {
            return Err(MarginError::MaintenanceRatio);
        }
        Ok(Self {
            leverage,
            maintenance_ratio,
        })
    }

    pub(crate) fn required(&self, lots: f64, price: f64, contract: f64, conversion: f64) -> f64 {
        lots * contract * price * conversion / self.leverage
    }

    pub(crate) fn maintenance(&self, used_margin: f64) -> f64 {
        self.maintenance_ratio * used_margin
    }
}

#[pymethods]
impl MarginConfig {
    #[new]
    #[pyo3(signature = (leverage, maintenance_ratio=0.5))]
    fn py_new(leverage: f64, maintenance_ratio: f64) -> PyResult<Self> {
        Ok(Self::new(leverage, maintenance_ratio)?)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RejectionReason {
    InsufficientMargin,
    AccountLiquidated,
    ArithmeticOverflow,
}

impl RejectionReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InsufficientMargin => "insufficient_margin",
            Self::AccountLiquidated => "account_liquidated",
            Self::ArithmeticOverflow => "arithmetic_overflow",
        }
    }
}

#[pyclass(frozen, from_py_object)]
#[derive(Debug, Clone)]
pub struct OrderRejection {
    #[pyo3(get)]
    pub timestamp: i64,
    #[pyo3(get)]
    pub is_long: bool,
    #[pyo3(get)]
    pub lot_size: f64,
    #[pyo3(get)]
    pub price: f64,
    pub reason: RejectionReason,
    #[pyo3(get)]
    pub equity_after_entry: Option<f64>,
    #[pyo3(get)]
    pub required_margin: Option<f64>,
}

#[pymethods]
impl OrderRejection {
    #[getter(reason)]
    fn py_reason(&self) -> &'static str {
        self.reason.as_str()
    }
}

#[derive(Debug, Clone)]
pub enum OrderOutcome {
    Filled(u64),
    Rejected(OrderRejection),
}

#[pyclass(frozen, from_py_object)]
#[derive(Debug, Clone)]
pub struct OrderResult {
    pub outcome: OrderOutcome,
}

#[pymethods]
impl OrderResult {
    #[getter]
    fn accepted(&self) -> bool {
        matches!(self.outcome, OrderOutcome::Filled(_))
    }

    #[getter]
    fn position_id(&self) -> Option<u64> {
        match &self.outcome {
            OrderOutcome::Filled(id) => Some(*id),
            OrderOutcome::Rejected(_) => None,
        }
    }

    #[getter]
    fn reason(&self) -> Option<&'static str> {
        match &self.outcome {
            OrderOutcome::Filled(_) => None,
            OrderOutcome::Rejected(rejection) => Some(rejection.reason.as_str()),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LiquidationReason {
    MaintenanceMargin,
    NonpositiveEquity,
}

impl LiquidationReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::MaintenanceMargin => "maintenance_margin",
            Self::NonpositiveEquity => "nonpositive_equity",
        }
    }
}

#[pyclass(frozen, from_py_object)]
#[derive(Debug, Clone)]
pub struct Liquidation {
    #[pyo3(get)]
    pub timestamp: i64,
    #[pyo3(get)]
    pub price: f64,
    pub reason: LiquidationReason,
    #[pyo3(get)]
    pub equity_before: f64,
    #[pyo3(get)]
    pub used_margin: f64,
    #[pyo3(get)]
    pub positions_closed: usize,
    #[pyo3(get)]
    pub cash_after: f64,
}

#[pymethods]
impl Liquidation {
    #[getter(reason)]
    fn py_reason(&self) -> &'static str {
        self.reason.as_str()
    }
}

#[derive(Debug, Clone, Default)]
pub(crate) enum RiskState {
    #[default]
    Disabled,
    Active(MarginConfig),
    Liquidated {
        config: MarginConfig,
        event: Liquidation,
    },
    Failed {
        config: MarginConfig,
        error: MarginError,
    },
}

impl RiskState {
    pub(crate) fn config(&self) -> Option<MarginConfig> {
        match self {
            Self::Disabled => None,
            Self::Active(config)
            | Self::Liquidated { config, .. }
            | Self::Failed { config, .. } => Some(config.clone()),
        }
    }

    pub(crate) fn liquidation(&self) -> Option<&Liquidation> {
        match self {
            Self::Disabled | Self::Active(_) | Self::Failed { .. } => None,
            Self::Liquidated { event, .. } => Some(event),
        }
    }

    pub(crate) fn status(&self) -> &'static str {
        match self {
            Self::Disabled => "unrestricted",
            Self::Active(_) => "active",
            Self::Liquidated { .. } => "liquidated",
            Self::Failed { .. } => "failed",
        }
    }
}
