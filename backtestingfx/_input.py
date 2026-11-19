"""Reference input preparation and one positional map for public index identity."""

from dataclasses import dataclass, field
from enum import Enum
import operator
import warnings

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


REFERENCE_OHLCV = ("Open", "High", "Low", "Close", "Volume")


class InputMode(Enum):
    FX = "fx"
    REFERENCE = "reference"

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str):
            raise ValueError("data_mode must be 'fx' or 'reference'")
        try:
            return cls(value)
        except ValueError as error:
            raise ValueError("data_mode must be 'fx' or 'reference'") from error


def prepare_reference(data, cash):
    """Apply the pinned 0.6.6 input policy through supported pandas APIs."""
    data = data.copy(deep=False)
    if (not isinstance(data.index, (pd.DatetimeIndex, pd.RangeIndex))
            and is_numeric_dtype(data.index.dtype)
            and (data.index > pd.Timestamp("1975").timestamp()).mean() > 0.8):
        try:
            data.index = pd.to_datetime(data.index)
        except ValueError:
            pass
    if "Volume" not in data:
        data["Volume"] = np.nan
    if len(data) == 0:
        raise ValueError("OHLC `data` is empty")
    if len(data.columns.intersection(REFERENCE_OHLCV)) != len(REFERENCE_OHLCV):
        raise ValueError("reference data must contain 'Open', 'High', 'Low', and 'Close' columns")
    if data[list(REFERENCE_OHLCV[:-1])].isnull().to_numpy().any():
        raise ValueError("Some OHLC values are missing (NaN)")
    if np.any(data["Close"] > cash):
        warnings.warn("Some prices are larger than initial cash value. Fractional asset trading is not enabled.",
                      UserWarning, stacklevel=3)
    if not data.index.is_monotonic_increasing:
        warnings.warn("Data index is not sorted in ascending order. Sorting.", UserWarning, stacklevel=3)
        data = data.sort_index()
    if not isinstance(data.index, pd.DatetimeIndex):
        warnings.warn("Data index is not datetime. Assuming simple periods, but `pd.DateTimeIndex` is advised.",
                      UserWarning, stacklevel=3)
    return data


def reference_columns(frame):
    values = [np.require(frame[name].to_numpy(), dtype=np.float64, requirements=["C", "A"])
              for name in REFERENCE_OHLCV]
    return np.arange(len(frame), dtype=np.int64), values


@dataclass(frozen=True)
class ReferenceSnapshot:
    frame: pd.DataFrame
    _index: pd.Index = field(init=False, repr=False)

    def __post_init__(self):
        index = self.frame.index.copy(deep=True)
        self.frame.index = index
        object.__setattr__(self, "_index", index)

    @property
    def index(self):
        return self._index

    def offset(self, value):
        offset = operator.index(value)
        if not 0 <= offset < len(self._index):
            raise IndexError("event row is outside this input snapshot")
        return offset

    def label(self, value):
        return self._index[self.offset(value)]

    def record(self, native):
        return IndexedRecord(native, self)

    def result(self, native):
        return IndexedResult(native, self)


_TIME_FIELDS = {
    "timestamp": ("timestamp", True),
    "time": ("timestamp", True),
    "bar": ("timestamp", False),
    "entry_timestamp": ("entry_timestamp", True),
    "entry_time": ("entry_timestamp", True),
    "entry_bar": ("entry_timestamp", False),
    "exit_timestamp": ("exit_timestamp", True),
    "exit_time": ("exit_timestamp", True),
    "exit_bar": ("exit_timestamp", False),
}


@dataclass(frozen=True)
class IndexedRecord:
    _native: object
    _snapshot: ReferenceSnapshot

    def __getattr__(self, name):
        mapping = _TIME_FIELDS.get(name)
        if mapping is None:
            return getattr(self._native, name)
        source, label = mapping
        value = getattr(self._native, source)
        return self._snapshot.label(value) if label else self._snapshot.offset(value)


@dataclass(frozen=True)
class IndexedResult:
    _native: object
    _snapshot: ReferenceSnapshot

    def __getattr__(self, name):
        return getattr(self._native, name)

    def __repr__(self):
        return repr(self._native)

    @property
    def index(self):
        return self._snapshot.index

    @property
    def equity(self):
        return pd.Series(self._native.equity_curve[1:], index=self.index, name="Equity")

    @property
    def trades(self):
        return [self._snapshot.record(trade) for trade in self._native.trades]

    @property
    def open_positions(self):
        return [self._snapshot.record(position) for position in self._native.open_positions]

    @property
    def order_rejections(self):
        return [self._snapshot.record(record) for record in self._native.order_rejections]

    @property
    def liquidation(self):
        event = self._native.liquidation
        return None if event is None else self._snapshot.record(event)
