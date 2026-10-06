# Compatibility policy

## Python and platforms

Package metadata declares Python >=3.9. CPython is the intended interpreter;
PyPy support is not currently verified. pandas and numpy are required.
HTML reporting additionally requires Plotly >=5 through the `report` extra.

| Area | Current coverage |
| --- | --- |
| Functional CI | Ubuntu, CPython 3.12; Rust checks and installed-package Python tests |
| Linux release wheel targets | x86_64 and aarch64, with manylinux selected by maturin |
| macOS release wheel targets | Intel x86_64 and Apple Silicon aarch64 |
| Windows release wheel target | x64 |
| Source distributions | Built by the release workflow; require Rust and a native compiler to install |

These wheel targets describe the configured release workflow, not a guarantee
that every Python version has a published wheel or has passed functional tests.
The extension does not currently use abi3, so wheel compatibility depends on
the interpreter and platform tags. Linux/macOS builds discover interpreters;
Windows explicitly selects Python 3.13. Check the artifacts for the release
and your interpreter before relying on wheel availability.

If pip falls back to a source build, install a current stable Rust toolchain
and your platform's compiler/linker, then retry installation. Contributor
setup is in [CONTRIBUTING.md](CONTRIBUTING.md). Broader wheel verification and
functional coverage are tracked in issues
[#35](https://github.com/KhizarImran/backtestingfx/issues/35) and
[#36](https://github.com/KhizarImran/backtestingfx/issues/36).

## Public API and stability

The project is pre-1.0. Public Python imports from `backtestingfx`, documented
strategy methods/properties, result fields, and documented execution assumptions
are the compatibility surface. Underscore-prefixed members and internal Rust
implementation details may change; existing examples using private strategy
members do not make those members stable.

Before 1.0, intentional incompatible API changes belong in a minor release
with a changelog entry and migration instructions. Patch releases should
preserve API signatures, but correctness fixes can change simulated fills,
PnL, equity, and statistics. Such changes must identify the previous and new
behavior and any migration required. After 1.0, intentional incompatible public
API changes require a major release.

Where practical, deprecated Python APIs emit `DeprecationWarning` and remain
available for at least one minor release before removal. Document the
replacement and planned removal version. A correctness fix that cannot retain
the old behavior safely may be made without a deprecation period, with an
explicit explanation and migration notes.

Changes to the minimum Python version or supported platform targets must be
announced in the changelog and this policy. Before 1.0 they belong in a minor
release; after 1.0 they require a major release.

## Reproducibility

Pin the library and dependency versions for reproducible research. Record data
source, instrument, timeframe, timezone, sizing, spread, commission, and
conversion assumptions. Read [CHANGELOG.md](CHANGELOG.md) before upgrading:
unchanged strategy code can produce different results after a modeling fix.
The full bid–ask spread correction in Unreleased is one such example.

This policy describes project expectations; the current tests and published
artifacts establish the coverage actually verified for each release.
