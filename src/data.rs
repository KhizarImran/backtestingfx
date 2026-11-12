//! Checked market-data parsing shared by Python buffers and native CSV input.

use crate::types::Bar;
use chrono::DateTime;
use pyo3::buffer::{Element, PyBuffer, ReadOnlyCell};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::fmt;
use std::fs::File;
use std::io::Read;

#[derive(Debug)]
pub enum DataError {
    Price {
        index: usize,
        field: &'static str,
    },
    Volume {
        index: usize,
    },
    Range {
        index: usize,
    },
    TimestampOrder {
        index: usize,
    },
    MissingColumn(&'static str),
    DuplicateColumn(String),
    Number {
        index: usize,
        field: &'static str,
        value: String,
    },
    Timestamp {
        index: usize,
        value: String,
    },
    Io(std::io::Error),
    Csv(csv::Error),
}

impl fmt::Display for DataError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Price { index, field } => {
                write!(f, "bar {index}: {field} must be positive and finite")
            }
            Self::Volume { index } => {
                write!(f, "bar {index}: volume must be nonnegative and finite")
            }
            Self::Range { index } => write!(
                f,
                "bar {index}: OHLC must satisfy low <= open/close <= high"
            ),
            Self::TimestampOrder { index } => write!(
                f,
                "bar {index}: timestamps must be strictly increasing in whole seconds; duplicate or out-of-order timestamp"
            ),
            Self::MissingColumn(field) => write!(f, "CSV missing required column: {field}"),
            Self::DuplicateColumn(field) => {
                write!(f, "CSV has duplicate columns after normalization: {field}")
            }
            Self::Number {
                index,
                field,
                value,
            } => write!(
                f,
                "bar {index}: {field} must be a real number, got {value:?}"
            ),
            Self::Timestamp { index, value } => write!(
                f,
                "bar {index}: timestamp must be an RFC 3339 datetime, got {value:?}"
            ),
            Self::Io(error) => write!(f, "could not read market data: {error}"),
            Self::Csv(error) => write!(f, "could not parse market-data CSV: {error}"),
        }
    }
}

impl std::error::Error for DataError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Csv(error) => Some(error),
            Self::Price { .. }
            | Self::Volume { .. }
            | Self::Range { .. }
            | Self::TimestampOrder { .. }
            | Self::MissingColumn(_)
            | Self::DuplicateColumn(_)
            | Self::Number { .. }
            | Self::Timestamp { .. } => None,
        }
    }
}

impl From<std::io::Error> for DataError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<csv::Error> for DataError {
    fn from(error: csv::Error) -> Self {
        Self::Csv(error)
    }
}

impl From<DataError> for PyErr {
    fn from(error: DataError) -> Self {
        PyValueError::new_err(error.to_string())
    }
}

pub(crate) fn validate_bar(bar: &Bar, index: usize) -> Result<(), DataError> {
    for (field, value) in [
        ("open", bar.open),
        ("high", bar.high),
        ("low", bar.low),
        ("close", bar.close),
    ] {
        if !value.is_finite() || value <= 0.0 {
            return Err(DataError::Price { index, field });
        }
    }
    if !bar.volume.is_finite() || bar.volume < 0.0 {
        return Err(DataError::Volume { index });
    }
    if bar.low > bar.high
        || bar.open < bar.low
        || bar.open > bar.high
        || bar.close < bar.low
        || bar.close > bar.high
    {
        return Err(DataError::Range { index });
    }
    Ok(())
}

#[derive(Default)]
struct Validator {
    previous: Option<i64>,
    index: usize,
}

impl Validator {
    fn check(&mut self, bar: &Bar) -> Result<(), DataError> {
        validate_bar(bar, self.index)?;
        if self
            .previous
            .is_some_and(|timestamp| bar.timestamp <= timestamp)
        {
            return Err(DataError::TimestampOrder { index: self.index });
        }
        self.previous = Some(bar.timestamp);
        self.index += 1;
        Ok(())
    }
}

pub fn validate_bars(data: &[Bar]) -> Result<(), DataError> {
    let mut validator = Validator::default();
    for bar in data {
        validator.check(bar)?;
    }
    Ok(())
}

pub(crate) type BarColumns = (PyBuffer<i64>, [PyBuffer<f64>; 5]);

fn column_slice<'a, T: Element>(
    py: Python<'a>,
    buffer: &'a PyBuffer<T>,
    count: usize,
) -> PyResult<&'a [ReadOnlyCell<T>]> {
    // PyO3 0.28.3 accepts a foreign-endian prefix on some platforms.
    let foreign_endian = match buffer.format().to_bytes().first() {
        Some(b'>' | b'!') => cfg!(target_endian = "little"),
        Some(b'<') => cfg!(target_endian = "big"),
        _ => false,
    };
    if foreign_endian {
        return Err(PyValueError::new_err(
            "bar columns must use native byte order",
        ));
    }
    if buffer.dimensions() != 1 || buffer.item_count() != count {
        return Err(PyValueError::new_err(
            "bar columns must be one-dimensional and have equal lengths",
        ));
    }
    buffer
        .as_slice(py)
        .ok_or_else(|| PyValueError::new_err("bar columns must be C-contiguous"))
}

#[pyfunction(name = "_bars_from_columns")]
pub(crate) fn bars_from_columns(py: Python<'_>, columns: BarColumns) -> PyResult<Vec<Bar>> {
    let (timestamps, values) = columns;
    let count = timestamps.item_count();
    let timestamps = column_slice(py, &timestamps, count)?;
    let [open, high, low, close, volume] = &values;
    let open = column_slice(py, open, count)?;
    let high = column_slice(py, high, count)?;
    let low = column_slice(py, low, count)?;
    let close = column_slice(py, close, count)?;
    let volume = column_slice(py, volume, count)?;
    let mut validator = Validator::default();
    let mut data = Vec::with_capacity(count);
    for i in 0..count {
        let bar = Bar::new(
            timestamps[i].get(),
            open[i].get(),
            high[i].get(),
            low[i].get(),
            close[i].get(),
            volume[i].get(),
        );
        validator.check(&bar)?;
        data.push(bar);
    }
    Ok(data)
}

/// Load checked, chronologically ordered OHLCV data; volume is optional.
pub fn load_csv(path: &str) -> Result<Vec<Bar>, DataError> {
    read_csv(File::open(path)?)
}

fn read_csv(reader: impl Read) -> Result<Vec<Bar>, DataError> {
    let mut reader = csv::Reader::from_reader(reader);
    let mut columns = HashMap::new();
    for (index, name) in reader.headers()?.iter().enumerate() {
        let name = name.to_lowercase();
        if columns.insert(name.clone(), index).is_some() {
            return Err(DataError::DuplicateColumn(name));
        }
    }
    let column = |name: &'static str| {
        columns
            .get(name)
            .copied()
            .ok_or(DataError::MissingColumn(name))
    };
    let timestamp = column("timestamp")?;
    let prices = [
        column("open")?,
        column("high")?,
        column("low")?,
        column("close")?,
    ];
    let volume = columns.get("volume").copied();
    let mut data = Vec::new();
    let mut validator = Validator::default();
    for (index, record) in reader.records().enumerate() {
        let record = record?;
        let time =
            DateTime::parse_from_rfc3339(&record[timestamp]).map_err(|_| DataError::Timestamp {
                index,
                value: record[timestamp].to_owned(),
            })?;
        let number = |column: usize, field| {
            record[column]
                .parse::<f64>()
                .map_err(|_| DataError::Number {
                    index,
                    field,
                    value: record[column].to_owned(),
                })
        };
        let bar = Bar::new(
            time.timestamp(),
            number(prices[0], "open")?,
            number(prices[1], "high")?,
            number(prices[2], "low")?,
            number(prices[3], "close")?,
            volume
                .map(|column| number(column, "volume"))
                .transpose()?
                .unwrap_or(0.0),
        );
        validator.check(&bar)?;
        data.push(bar);
    }
    Ok(data)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Engine;

    #[test]
    fn prices_volume_and_ranges_are_checked_without_rejecting_flat_bars() {
        for value in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            for field in 0..4 {
                let mut prices = [1.1; 4];
                prices[field] = value;
                assert!(matches!(
                    Bar::try_new(0, prices[0], prices[1], prices[2], prices[3], 0.0),
                    Err(DataError::Price { .. })
                ));
            }
        }
        for volume in [-1.0, f64::NAN, f64::INFINITY] {
            assert!(matches!(
                Bar::try_new(0, 1.1, 1.1, 1.1, 1.1, volume),
                Err(DataError::Volume { .. })
            ));
        }
        assert!(matches!(
            Bar::try_new(0, 1.5, 1.4, 1.0, 1.2, 0.0),
            Err(DataError::Range { .. })
        ));
        assert!(matches!(
            Bar::try_new(0, 1.1, 1.4, 1.0, 0.9, 0.0),
            Err(DataError::Range { .. })
        ));
        assert!(Bar::try_new(0, 1.1, 1.1, 1.1, 1.1, 0.0).is_ok());
    }

    #[test]
    fn checked_native_engine_rejects_duplicates_and_backwards_bars() {
        for times in [[1, 1], [1, 0]] {
            let data = times
                .map(|time| Bar::new(time, 1.1, 1.1, 1.1, 1.1, 0.0))
                .to_vec();
            assert!(matches!(
                Engine::try_new(data, 10_000.0, 0.0, 0.0, 100_000.0, 1.0),
                Err(DataError::TimestampOrder { index: 1 })
            ));
        }
        assert!(validate_bars(&[]).is_ok());
        assert!(Engine::try_new(Vec::new(), 10_000.0, 0.0, 0.0, 100_000.0, 1.0).is_ok());
        let data = vec![
            Bar::new(0, 1.1, 1.1, 1.1, 1.1, 0.0),
            Bar::new(1, 1.1, 1.1, 1.1, f64::NAN, 0.0),
        ];
        assert!(matches!(
            Engine::try_new(data, 10_000.0, 0.0, 0.0, 100_000.0, 1.0),
            Err(DataError::Price {
                index: 1,
                field: "close"
            })
        ));
    }

    #[test]
    fn csv_resolves_case_insensitive_columns_by_name_and_defaults_volume() {
        let csv = "Close,HIGH,Timestamp,Open,LOW\n1.2,1.3,2026-01-01T00:00:00Z,1.1,1.0\n";
        let data = read_csv(csv.as_bytes()).unwrap();
        assert_eq!(data.len(), 1);
        let bar = &data[0];
        assert_eq!(bar.timestamp, 1767225600);
        assert_eq!(
            (bar.open, bar.high, bar.low, bar.close, bar.volume),
            (1.1, 1.3, 1.0, 1.2, 0.0)
        );
        assert!(
            read_csv("timestamp,open,high,low,close,volume\n".as_bytes())
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn csv_returns_errors_for_bad_schema_values_and_record_lengths() {
        for (csv, expected) in [
            ("open,high,low,close\n", "timestamp"),
            ("timestamp,open,Open,high,low,close\n", "duplicate"),
            (
                "timestamp,open,high,low,close\ninvalid,1,1,1,1\n",
                "RFC 3339",
            ),
            (
                "timestamp,open,high,low,close\n2026-01-01T00:00:00Z,no,1,1,1\n",
                "open",
            ),
            (
                "timestamp,open,high,low,close\n2026-01-01T00:00:00Z,1,1,1,NaN\n",
                "close",
            ),
            (
                "timestamp,open,high,low,close\n2026-01-01T00:00:00Z,2,1,1,1\n",
                "OHLC",
            ),
            (
                "timestamp,open,high,low,close\n2026-01-01T00:00:00Z,1,1\n",
                "CSV",
            ),
        ] {
            let error = read_csv(csv.as_bytes()).err().unwrap();
            assert!(error.to_string().contains(expected), "{error}");
        }
    }

    #[test]
    fn csv_checks_converted_timestamp_order_and_propagates_io_failure() {
        for last in [
            "2026-01-01T00:00:00Z",
            "2025-12-31T23:59:00Z",
            "2026-01-01T00:00:00.5Z",
        ] {
            let csv = format!(
                "timestamp,open,high,low,close\n2026-01-01T00:00:00Z,1,1,1,1\n{last},1,1,1,1\n"
            );
            assert!(matches!(
                read_csv(csv.as_bytes()),
                Err(DataError::TimestampOrder { index: 1 })
            ));
        }
        struct Broken;
        impl Read for Broken {
            fn read(&mut self, _buffer: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("test read failure"))
            }
        }
        assert!(matches!(read_csv(Broken), Err(DataError::Csv(_))));
    }
}
