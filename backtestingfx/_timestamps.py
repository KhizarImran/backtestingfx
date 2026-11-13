"""Parse external timestamps into exact, floored UTC Unix seconds."""

from numbers import Integral, Real
import re

import numpy as np
import pandas as pd


UNITS = {"s": 1, "ms": 1000, "us": 1000000, "ns": 1000000000}


def timestamp_value(value, offset, unit):
    try:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError("boolean timestamp")
        if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
            value = int(value)
        if isinstance(value, (Integral, Real)):
            if unit is None:
                raise ValueError("numeric timestamps require timestamp_unit='s', 'ms', 'us', or 'ns'")
            if isinstance(value, Integral):
                seconds = int(value) // UNITS[unit]
            else:
                numerator, denominator = value.as_integer_ratio()
                seconds = numerator // (denominator * UNITS[unit])
        else:
            stamp = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
            if pd.isna(stamp):
                raise ValueError("missing timestamp")
            # asm8 stores UTC for aware values and treats naive values as UTC.
            # Integer division floors before the epoch and avoids float rounding.
            raw = stamp.asm8
            seconds = int(raw.view("i8")) // UNITS[np.datetime_data(raw.dtype)[0]]
        if not -(2**63) <= seconds < 2**63:
            raise ValueError("timestamp outside int64 Unix seconds")
        return seconds
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"bar {offset}: invalid timestamp: {error}") from error


def index_seconds(index):
    return np.ascontiguousarray(index.asi8 // UNITS[getattr(index, "unit", "ns")], dtype=np.int64)
