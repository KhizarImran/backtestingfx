//! Optional account-currency financing on a configured local calendar.

use crate::broker::Broker;
use crate::types::Bar;
use chrono::{
    DateTime, Datelike, Duration, NaiveDate, NaiveDateTime, NaiveTime, Offset, TimeZone, Utc,
};
use chrono_tz::Tz;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::BTreeMap;
use std::fmt;

/// Invalid financing configuration or timestamps for an enabled swap schedule.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SwapError {
    InvalidRate,
    InvalidTimezone(String),
    InvalidTime,
    InvalidMultiplier,
    InvalidDate(String),
    InvalidTimestamp(usize),
    UnorderedTimestamp(usize),
    MissingRollover(NaiveDate),
    CalendarRange,
    ArithmeticOverflow,
}

impl fmt::Display for SwapError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRate => write!(f, "swap long and short rates must be finite"),
            Self::InvalidTimezone(name) => write!(f, "unknown swap timezone: {name}"),
            Self::InvalidTime => write!(
                f,
                "swap rollover_time must be HH:MM in the range 00:00..23:59"
            ),
            Self::InvalidMultiplier => write!(f, "swap multipliers must be finite and nonnegative"),
            Self::InvalidDate(date) => write!(f, "swap date must use YYYY-MM-DD: {date}"),
            Self::InvalidTimestamp(index) => {
                write!(f, "swap requires a supported Unix timestamp at bar {index}")
            }
            Self::UnorderedTimestamp(index) => write!(
                f,
                "swap requires nondecreasing timestamps; bar {index} is out of order"
            ),
            Self::MissingRollover(date) => write!(
                f,
                "swap rollover time does not exist on {date}; choose another time or override this date with multiplier zero"
            ),
            Self::CalendarRange => write!(f, "swap calendar is outside the supported date range"),
            Self::ArithmeticOverflow => write!(
                f,
                "swap posting would produce a nonfinite amount or balance"
            ),
        }
    }
}

impl std::error::Error for SwapError {}

impl From<SwapError> for PyErr {
    fn from(error: SwapError) -> Self {
        PyValueError::new_err(error.to_string())
    }
}

/// Signed account-currency cash per lot, with local rollover calendar settings.
#[pyclass(frozen, from_py_object)]
#[derive(Debug, Clone)]
pub struct SwapConfig {
    #[pyo3(get)]
    long: f64,
    #[pyo3(get)]
    short: f64,
    timezone: Tz,
    time: NaiveTime,
    weekdays: [f64; 7],
    dates: BTreeMap<NaiveDate, f64>,
}

impl SwapConfig {
    /// Construct rates with UTC midnight and Monday-first weights [1,1,3,1,1,0,0].
    pub fn new(long: f64, short: f64) -> Result<Self, SwapError> {
        if !long.is_finite() || !short.is_finite() {
            return Err(SwapError::InvalidRate);
        }
        Ok(Self {
            long,
            short,
            timezone: chrono_tz::UTC,
            time: NaiveTime::MIN,
            weekdays: [1.0, 1.0, 3.0, 1.0, 1.0, 0.0, 0.0],
            dates: BTreeMap::new(),
        })
    }

    pub fn with_rollover(mut self, timezone: &str, time: &str) -> Result<Self, SwapError> {
        self.timezone = timezone
            .parse()
            .map_err(|_| SwapError::InvalidTimezone(timezone.to_owned()))?;
        self.time = NaiveTime::parse_from_str(time, "%H:%M").map_err(|_| SwapError::InvalidTime)?;
        if self.time.format("%H:%M").to_string() != time {
            return Err(SwapError::InvalidTime);
        }
        Ok(self)
    }

    pub fn with_weekday_multipliers(mut self, weights: [f64; 7]) -> Result<Self, SwapError> {
        for weight in weights {
            check_multiplier(weight)?;
        }
        self.weekdays = weights;
        Ok(self)
    }

    pub fn with_date_multipliers(
        mut self,
        overrides: impl IntoIterator<Item = (String, f64)>,
    ) -> Result<Self, SwapError> {
        for (date, weight) in overrides {
            check_multiplier(weight)?;
            let parsed = NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                .map_err(|_| SwapError::InvalidDate(date.clone()))?;
            if parsed.to_string() != date {
                return Err(SwapError::InvalidDate(date));
            }
            self.dates.insert(parsed, weight);
        }
        Ok(self)
    }

    pub(crate) fn plan(&self, data: &[Bar]) -> Result<SwapPlan, SwapError> {
        let mut plan = SwapPlan {
            long: self.long,
            short: self.short,
            events: Vec::new(),
        };
        let Some(first) = data.first() else {
            return Ok(plan);
        };
        let mut previous = first.timestamp;
        for (index, bar) in data.iter().enumerate() {
            if DateTime::from_timestamp(bar.timestamp, 0).is_none() {
                return Err(SwapError::InvalidTimestamp(index));
            }
            if bar.timestamp < previous {
                return Err(SwapError::UnorderedTimestamp(index));
            }
            previous = bar.timestamp;
        }
        let start = DateTime::<Utc>::from_timestamp(first.timestamp, 0)
            .ok_or(SwapError::InvalidTimestamp(0))?;
        let end = DateTime::<Utc>::from_timestamp(previous, 0).ok_or(SwapError::CalendarRange)?;
        let local_start = local_datetime(start, self.timezone)?;
        let local_end = local_datetime(end, self.timezone)?;
        let mut date = local_start.date();
        let last = local_end.date();
        while date <= last {
            let weekday = usize::try_from(date.weekday().num_days_from_monday())
                .map_err(|_| SwapError::CalendarRange)?;
            let multiplier = self
                .dates
                .get(&date)
                .copied()
                .unwrap_or(self.weekdays[weekday]);
            if multiplier != 0.0 {
                // Charge once at the earlier occurrence of a repeated local time.
                let local_cutoff = date.and_time(self.time);
                if let Some(cutoff) = self.timezone.from_local_datetime(&local_cutoff).earliest() {
                    let timestamp = cutoff.timestamp();
                    if timestamp > first.timestamp && timestamp <= previous {
                        plan.events.push(Rollover {
                            timestamp,
                            multiplier,
                        });
                    }
                } else if local_cutoff > local_start && local_cutoff <= local_end {
                    return Err(SwapError::MissingRollover(date));
                }
            }
            if date == last {
                break;
            }
            date = date.succ_opt().ok_or(SwapError::CalendarRange)?;
        }
        plan.events.sort_by_key(|event| event.timestamp);
        Ok(plan)
    }
}

fn check_multiplier(value: f64) -> Result<(), SwapError> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(SwapError::InvalidMultiplier)
    }
}

fn local_datetime(time: DateTime<Utc>, timezone: Tz) -> Result<NaiveDateTime, SwapError> {
    let offset = timezone
        .offset_from_utc_datetime(&time.naive_utc())
        .fix()
        .local_minus_utc();
    time.naive_utc()
        .checked_add_signed(Duration::seconds(i64::from(offset)))
        .ok_or(SwapError::CalendarRange)
}

#[pymethods]
impl SwapConfig {
    #[new]
    #[pyo3(signature = (long=0.0, short=0.0, *, timezone="UTC", rollover_time="00:00", weekday_multipliers=None, date_multipliers=None))]
    fn py_new(
        long: f64,
        short: f64,
        timezone: &str,
        rollover_time: &str,
        weekday_multipliers: Option<[f64; 7]>,
        date_multipliers: Option<BTreeMap<String, f64>>,
    ) -> PyResult<Self> {
        let mut config = Self::new(long, short)?.with_rollover(timezone, rollover_time)?;
        if let Some(weights) = weekday_multipliers {
            config = config.with_weekday_multipliers(weights)?;
        }
        if let Some(dates) = date_multipliers {
            config = config.with_date_multipliers(dates)?;
        }
        Ok(config)
    }

    #[getter]
    fn timezone(&self) -> &str {
        self.timezone.name()
    }

    #[getter]
    fn rollover_time(&self) -> String {
        self.time.format("%H:%M").to_string()
    }

    #[getter]
    fn weekday_multipliers(&self) -> [f64; 7] {
        self.weekdays
    }

    #[getter]
    fn date_multipliers(&self) -> BTreeMap<String, f64> {
        self.dates
            .iter()
            .map(|(date, weight)| (date.to_string(), *weight))
            .collect()
    }
}

#[derive(Debug)]
struct Rollover {
    timestamp: i64,
    multiplier: f64,
}

#[derive(Debug)]
pub(crate) struct SwapPlan {
    long: f64,
    short: f64,
    events: Vec<Rollover>,
}

impl SwapPlan {
    pub(crate) fn cursor(&self) -> SwapCursor<'_> {
        SwapCursor {
            plan: self,
            next: 0,
        }
    }
}

pub(crate) struct SwapCursor<'a> {
    plan: &'a SwapPlan,
    next: usize,
}

impl SwapCursor<'_> {
    pub(crate) fn apply_through(
        &mut self,
        timestamp: i64,
        broker: &mut Broker,
    ) -> Result<(), SwapError> {
        while let Some(event) = self.plan.events.get(self.next) {
            if event.timestamp > timestamp {
                break;
            }
            broker.apply_swap(
                event.timestamp,
                event.multiplier,
                self.plan.long,
                self.plan.short,
            )?;
            self.next += 1;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Engine;
    use crate::strategy::Strategy;

    fn timestamp(text: &str) -> i64 {
        DateTime::parse_from_rfc3339(text).unwrap().timestamp()
    }

    fn bars(times: &[&str]) -> Vec<Bar> {
        times
            .iter()
            .map(|time| Bar::new(timestamp(time), 1.1, 1.1, 1.1, 1.1, 0.0))
            .collect()
    }

    #[test]
    fn calendar_weights_and_date_overrides_replace_weekdays() {
        let data = bars(&["2026-01-06T00:00:00Z", "2026-01-08T00:00:00Z"]);
        let config = SwapConfig::new(-10.0, 2.0).unwrap();
        let plan = config.plan(&data).unwrap();
        assert_eq!(plan.events.len(), 2);
        assert_eq!(plan.events[0].multiplier, 3.0);
        assert_eq!(plan.events[1].multiplier, 1.0);
        let config = config
            .with_date_multipliers([
                ("2026-01-07".to_owned(), 0.0),
                ("2026-01-08".to_owned(), 4.0),
            ])
            .unwrap();
        let plan = config.plan(&data).unwrap();
        assert_eq!(plan.events.len(), 1);
        assert_eq!(plan.events[0].multiplier, 4.0);
    }

    #[test]
    fn local_rollover_tracks_dst_instead_of_adding_24_hours() {
        let config = SwapConfig::new(-10.0, 2.0)
            .unwrap()
            .with_rollover("America/New_York", "17:00")
            .unwrap()
            .with_weekday_multipliers([1.0; 7])
            .unwrap();
        let data = bars(&["2026-03-06T22:00:00Z", "2026-03-09T21:00:00Z"]);
        let plan = config.plan(&data).unwrap();
        assert_eq!(
            plan.events
                .iter()
                .map(|event| event.timestamp)
                .collect::<Vec<_>>(),
            [
                timestamp("2026-03-07T22:00:00Z"),
                timestamp("2026-03-08T21:00:00Z"),
                timestamp("2026-03-09T21:00:00Z")
            ]
        );
        let plan = config
            .plan(&bars(&["2026-10-31T21:00:00Z", "2026-11-02T22:00:00Z"]))
            .unwrap();
        assert_eq!(plan.events[0].timestamp, timestamp("2026-11-01T22:00:00Z"));
        assert_eq!(plan.events[1].timestamp, timestamp("2026-11-02T22:00:00Z"));
    }

    #[test]
    fn ambiguous_rollover_is_once_and_missing_rollover_is_actionable() {
        let config = SwapConfig::new(-1.0, 0.0)
            .unwrap()
            .with_weekday_multipliers([1.0; 7])
            .unwrap()
            .with_rollover("America/New_York", "01:30")
            .unwrap();
        let plan = config
            .plan(&bars(&["2026-11-01T00:00:00Z", "2026-11-01T07:00:00Z"]))
            .unwrap();
        assert_eq!(plan.events.len(), 1);
        assert_eq!(plan.events[0].timestamp, timestamp("2026-11-01T05:30:00Z"));
        let plan = config
            .plan(&bars(&["2026-11-01T05:15:00Z", "2026-11-01T06:15:00Z"]))
            .unwrap();
        assert_eq!(plan.events.len(), 1);
        assert_eq!(plan.events[0].timestamp, timestamp("2026-11-01T05:30:00Z"));
        let config = config.with_rollover("America/New_York", "02:30").unwrap();
        let data = bars(&["2026-03-08T00:00:00Z", "2026-03-08T10:00:00Z"]);
        assert!(matches!(
            config.plan(&data),
            Err(SwapError::MissingRollover(_))
        ));
        for data in [
            bars(&["2026-03-08T08:00:00Z", "2026-03-08T09:00:00Z"]),
            bars(&["2026-03-08T05:00:00Z", "2026-03-08T06:00:00Z"]),
            bars(&["2026-03-08T08:00:00Z"]),
        ] {
            assert!(config.plan(&data).unwrap().events.is_empty());
        }
        let config = config
            .with_date_multipliers([("2026-03-08".to_owned(), 0.0)])
            .unwrap();
        assert!(config.plan(&data).unwrap().events.is_empty());
    }

    #[test]
    fn partial_closes_allocate_financing_without_charging_cash_twice() {
        let mut broker = Broker::new(10_000.0, 7.0, 0.0, 100_000.0, 1.27);
        broker.buy(1.1, 1.0, 0, None, None);
        broker.apply_swap(1, 1.0, -10.0, 2.0).unwrap();
        assert_eq!(broker.cash, 9983.0);
        broker.close_partial(0, 0.4, 1.1, 2);
        assert!((broker.cash - 9980.2).abs() < 1e-8);
        assert_eq!(broker.trade_history[0].swap, -4.0);
        assert!((broker.trade_history[0].pnl + 9.6).abs() < 1e-8);
        assert_eq!(broker.positions[0].swap, -6.0);
        broker.apply_swap(3, 1.0, -10.0, 2.0).unwrap();
        broker.close_all(1.1, 4);
        assert!((broker.cash - 9970.0).abs() < 1e-8);
        assert_eq!(broker.trade_history[1].swap, -12.0);
        assert_eq!(broker.total_swap, -16.0);
        assert!(
            (broker
                .trade_history
                .iter()
                .map(|trade| trade.pnl)
                .sum::<f64>()
                + 30.0)
                .abs()
                < 1e-8
        );
        assert_eq!(
            broker
                .swap_charges
                .iter()
                .map(|charge| charge.amount)
                .sum::<f64>(),
            -16.0
        );
    }

    #[test]
    fn native_runs_rebuild_calendar_and_reset_financing() {
        struct Hold;
        impl Strategy for Hold {
            fn next(&mut self, bar: &Bar, broker: &mut Broker) {
                if broker.positions.is_empty() {
                    broker.buy(bar.close, 1.0, bar.timestamp, None, None);
                }
            }
        }
        let config = SwapConfig::new(-10.0, 2.0).unwrap();
        let mut engine = Engine::new(
            bars(&["2026-01-05T23:00:00Z", "2026-01-06T00:00:00Z"]),
            10_000.0,
            0.0,
            0.0,
            100_000.0,
            1.0,
        );
        for _ in 0..2 {
            let stats = engine.run_with_swap(&mut Hold, &config).unwrap();
            assert_eq!(stats.final_cash, 9990.0);
            assert_eq!(stats.total_swap, -10.0);
            assert_eq!(stats.swap_charges.len(), 1);
        }
        engine.data = bars(&["2026-01-06T23:00:00Z", "2026-01-07T00:00:00Z"]);
        let stats = engine.run_with_swap(&mut Hold, &config).unwrap();
        assert_eq!(stats.total_swap, -30.0);
        let disabled = engine.run(&mut Hold);
        assert_eq!(disabled.final_cash, 10_000.0);
        assert_eq!(disabled.total_swap, 0.0);
        assert!(disabled.swap_charges.is_empty());
    }

    #[test]
    fn invalid_configuration_and_enabled_time_order_return_typed_errors() {
        assert!(matches!(
            SwapConfig::new(f64::NAN, 0.0),
            Err(SwapError::InvalidRate)
        ));
        let config = SwapConfig::new(-1.0, 2.0).unwrap();
        assert!(matches!(
            config.clone().with_rollover("bad-zone", "00:00"),
            Err(SwapError::InvalidTimezone(_))
        ));
        assert!(matches!(
            config.clone().with_rollover("UTC", "25:00"),
            Err(SwapError::InvalidTime)
        ));
        assert!(matches!(
            config.clone().with_weekday_multipliers([-1.0; 7]),
            Err(SwapError::InvalidMultiplier)
        ));
        assert!(matches!(
            config
                .clone()
                .with_date_multipliers([("2026-02-30".to_owned(), 1.0)]),
            Err(SwapError::InvalidDate(_))
        ));
        assert!(matches!(
            config.plan(&[Bar::new(i64::MAX, 1.0, 1.0, 1.0, 1.0, 0.0)]),
            Err(SwapError::InvalidTimestamp(0))
        ));
        assert!(matches!(
            config.plan(&bars(&["2026-01-06T00:00:00Z", "2026-01-05T00:00:00Z"])),
            Err(SwapError::UnorderedTimestamp(1))
        ));
        let config = config.with_rollover("Pacific/Kiritimati", "00:00").unwrap();
        assert!(matches!(
            config.plan(&[Bar::new(
                DateTime::<Utc>::MAX_UTC.timestamp(),
                1.0,
                1.0,
                1.0,
                1.0,
                0.0
            )]),
            Err(SwapError::CalendarRange)
        ));
    }

    #[test]
    fn overflowing_posting_is_rejected_before_any_position_or_cash_changes() {
        let mut broker = Broker::new(10_000.0, 0.0, 0.0, 100_000.0, 1.0);
        broker.buy(1.1, 0.01, 0, None, None);
        broker.apply_swap(1, 3.0, 1e308, 0.0).unwrap();
        assert!(broker.total_swap.is_finite());
        assert!((broker.total_swap / 3e306 - 1.0).abs() < 1e-12);
        let mut broker = Broker::new(10_000.0, 0.0, 0.0, 100_000.0, 1.0);
        broker.buy(1.1, 0.01, 0, None, None);
        broker.buy(1.1, 10.0, 0, None, None);
        assert_eq!(
            broker.apply_swap(1, 1.0, 1e308, 0.0),
            Err(SwapError::ArithmeticOverflow)
        );
        assert_eq!(broker.cash, 10_000.0);
        assert_eq!(broker.total_swap, 0.0);
        assert!(broker.positions.iter().all(|position| position.swap == 0.0));
        assert!(broker.swap_charges.is_empty());
    }
}
