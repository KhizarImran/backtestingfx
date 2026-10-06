---
name: Bug report
about: Report a reproducible FX simulation, installation, or reporting bug
title: ""
labels: ""
assignees: ""
---

## Problem
Describe the bug and the expected versus actual behavior. Include exact errors
or a traceback and hand-calculated expected results when relevant.

## Environment
- backtestingfx version (or commit):
- Python version and interpreter:
- OS and architecture:
- pandas / numpy versions:
- Plotly version (if reporting):
- Installation method (wheel, source, development); Rust version if source-built:

## Data and simulation settings
- Instrument, account currency, and quote-to-account conversion:
- Row count, column names/dtypes, and index type:
- Timeframe, timestamp format/unit, and timezone:
- OHLC price convention (midpoint, bid, or ask):
- Cash, lot sizes, contract size, spread, commission, and SL/TP settings:

## Minimal reproduction
Provide a runnable code block that constructs a small synthetic DataFrame,
defines the strategy or signal function, and runs the failing call. Include
sample rows and output; avoid private data and external file dependencies.

```python
# Imports, synthetic input, strategy/signals, settings, and failing call
```

## Additional context
Does this happen on the latest release or main? If it is a regression, include
the last working version and relevant logs.
