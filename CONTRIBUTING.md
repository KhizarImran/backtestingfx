# Contributing

backtestingfx focuses on FX research with OHLCV bars, lot sizing, spread,
commission, and quote-to-account conversion. See the [roadmap](ROADMAP.md)
for priorities and the [compatibility policy](COMPATIBILITY.md) before changing
public behavior. Search existing issues before opening a bug or feature request.

## Local setup

Install Git, CPython 3.9 or newer, and a current stable Rust toolchain with
Cargo and Clippy. The crate uses Rust edition 2024 (Rust 1.85 or newer);
dependency requirements may require a newer compiler. Python 3.12 on Linux
matches the current CI configuration. Building the extension also requires
your platform's native compiler/linker (for example, Xcode Command Line Tools
on macOS or MSVC Build Tools on Windows).

From your clone's root:

```bash
git clone https://github.com/KhizarImran/backtestingfx.git
cd backtestingfx
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "maturin>=1.0,<2.0" pandas numpy "plotly>=5"
maturin develop
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell instead.
Use your virtual environment's Python for all commands. Run `maturin develop`
again after Rust changes. Python source changes are visible through the
development installation.

## Checks

Run the Rust checks from the repository root:

```bash
cargo clippy --all-targets -- -D warnings
cargo test
```

Check the installed Python package outside the source directory, as CI does.
This avoids accidentally importing the source package without its built extension.

```bash
python -m pip install ".[report]"
repo_dir="$PWD"
cd /tmp
python -m unittest discover -s "$repo_dir/tests"
cd "$repo_dir"
```

On Windows, save the repository path, change to a temporary directory, and pass
the absolute path to `tests` to `python -m unittest discover -s`. Reinstall
with `python -m pip install --force-reinstall ".[report]"` after changes when
using this regular installation rather than `maturin develop`.

## Examples

Run these from the repository root after building/installing the package:

```bash
python examples/sma_cross.py
python examples/optimize.py
python examples/html_report.py
cargo run --example simple_strategy
```

SMA and optimization examples use the included `data/EURUSD_1H.csv`.
The HTML example generates synthetic data, writes
`backtestingfx-report.html`, and attempts to open a browser. It requires the
`report` extra. Comparison and benchmark scripts additionally require
`python -m pip install backtesting`; report their versions and execution
assumptions when sharing results.

## Pull requests

Create a focused branch and describe the problem, resulting behavior, and
validation performed. Link the relevant issue using `Closes #number` when
the PR completes it. For behavior changes, add regression tests with small,
deterministic inputs and update documentation and the Unreleased changelog.
Explain intentional changes to fills, costs, timestamps, or statistics and
include migration guidance when existing results can change.

Use the bug template to report versions, input shape and timestamps, broker
settings, expected versus actual results, and a minimal runnable reproduction.
Use synthetic or shareable data rather than private trading records.
