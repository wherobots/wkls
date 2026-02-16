# AGENTS.md - Agentic Coding Guide for wkls

This document provides coding agents with essential information about the wkls repository.

## Project Overview

**wkls** (Well-Known Locations) is a Python library for accessing global administrative
boundaries using chainable syntax. It fetches geometries from Overture Maps Foundation
GeoParquet data via Apache SedonaDB.

**Python Version:** 3.9+ (tested on 3.9, 3.10, 3.11, 3.12, 3.13)
**Package Manager:** `uv` (Astral's Python package manager)

## Build/Lint/Test Commands

### Setup
```bash
# Install dependencies with development tools
uv sync --all-extras --dev
```

### Testing
```bash
# Run all tests
uv run pytest -v

# Run specific test file
uv run pytest tests/test_us.py -v

# Run single test function
uv run pytest tests/test_errors.py::test_did_you_mean_suggestions -v

# Run tests with keyword match
uv run pytest -k "error" -v

# Run tests with coverage
uv run pytest tests/ --cov=wkls
```

### Linting and Formatting
```bash
# Check code style (linting)
uv run ruff check .

# Auto-fix linting issues
uv run ruff check . --fix

# Format code
uv run ruff format .

# Check formatting without changes (CI mode)
uv run ruff format --check .
```

### Building
```bash
uv build
```

## Code Style Guidelines

### Ruff Configuration
The project uses Ruff for linting and formatting. Configuration is in `pyproject.toml`:

- **Line length:** 88 characters
- **Quote style:** Double quotes
- **Indent style:** 4 spaces
- **Target Python:** 3.9

**Enabled Ruff rules:**
- `E`, `W` - pycodestyle errors/warnings
- `F` - Pyflakes
- `I` - isort (import sorting)
- `B` - flake8-bugbear
- `UP` - pyupgrade

### Import Ordering
Imports must follow isort style (enforced by ruff `I` rule):

```python
from __future__ import annotations       # 1. __future__ imports first

import importlib.resources               # 2. Standard library (alphabetized)
import os
from typing import Any, Callable

import sedonadb                          # 3. Third-party packages (alphabetized)
import sqlescapy

from . import data                       # 4. Local/relative imports
from .core import Wkl
```

### Naming Conventions
- **Classes:** `PascalCase` (e.g., `Wkl`, `ChainableDataFrame`)
- **Functions/Methods:** `snake_case` (e.g., `overture_version`, `resolve`)
- **Private functions:** Leading underscore (e.g., `_initialize_table`, `_get_geom_expr`)
- **Constants:** `UPPER_SNAKE_CASE` (e.g., `OVERTURE_VERSION`, `CITY_QUERY`)
- **Variables:** `snake_case` (e.g., `country_iso`, `region_iso`)

### Type Annotations
Use full type annotations on all public methods. Use Python 3.10+ union syntax
with `from __future__ import annotations`:

```python
from __future__ import annotations
from typing import Any, Callable

def __init__(self, chain: list[str] | None = None) -> None:
    """Initialize a Wkl instance."""
    self.chain: list[str] = chain or []

def _log_and_query(
    exec_fn: Callable[[str], sedonadb.dataframe.DataFrame], query: str
) -> sedonadb.dataframe.DataFrame:
```

### Docstrings (Google Style)
All public functions and classes must have Google-style docstrings:

```python
def sqlescape(v: str) -> str:
    """Escape a string for safe SQL interpolation.

    Escapes special characters while preserving % for LIKE operators.

    Args:
        v: String value to escape.

    Returns:
        SQL-safe escaped string.

    Raises:
        ValueError: If input cannot be escaped.
    """
```

### Error Handling
- Use `ValueError` for user-facing errors with helpful messages
- Provide "Did you mean?" suggestions for typos using fuzzy matching
- Include actionable tips in error messages
- Use `NotImplementedError` for unimplemented features

```python
def _get_geom_expr(self, expr: str) -> Any:
    df = self.resolve()
    if df.count() == 0:
        chain_str = ".".join(self.chain)
        failed_name = self.chain[-1]
        suggestions = self._get_suggestions(failed_name)
        if suggestions:
            hint = f" Did you mean: {', '.join(suggestions)}?"
        else:
            hint = ""
        chain_prefix = ".".join(self.chain[:-1])
        wildcard_example = f"wkls.{chain_prefix}['%{failed_name}%']"
        tip = f"\nTip: Use {wildcard_example} to perform a wildcard search."
        raise ValueError(f"No result found for: {chain_str}.{hint}{tip}")
```

## Testing Patterns

Tests use pytest and are located in `tests/`. Test organization:
- `test_us.py` - US-specific functionality tests
- `test_errors.py` - Error handling and validation tests
- `test_geometry_access.py` - Geometry format tests
- `test_dependencies.py` - Dependencies/territories tests

### Test Structure
```python
import pytest
import wkls

def test_countries_without_region():
    """Test that countries without regions raise appropriate errors."""
    with pytest.raises(ValueError) as exc_info:
        wkls.fk.regions()
    assert "The country 'FK' does not have regions in the dataset" in str(
        exc_info.value
    )

@pytest.fixture
def sf() -> wkls.Wkl:
    """Fixture for San Francisco Wkl instance."""
    return wkls.us.ca.sanfrancisco
```

### Test Naming
- Test functions prefixed with `test_`
- Use descriptive names: `test_did_you_mean_suggestions`, `test_empty_chain_error`

## Project Structure

```
wkls/
├── wkls/                    # Main package
│   ├── __init__.py          # Module init, creates singleton instance
│   ├── core.py              # Core implementation (Wkl, ChainableDataFrame)
│   └── data/                # Data package
│       └── overture.zstd18.parquet  # Bundled metadata
├── tests/                   # Test suite
├── pyproject.toml           # Project configuration (PEP 621)
├── uv.lock                  # Lock file for dependencies
├── README.md                # User documentation
├── CONTRIBUTING.md          # Contribution guidelines
└── RELEASING.md             # Release guidelines and versioning policy
```

## Key Architecture Notes

1. **Singleton Pattern:** The package uses a singleton pattern in `__init__.py` that
   replaces the module with a `Wkl` instance, enabling `wkls.us.ca` syntax directly.

2. **Chainable API:** The `Wkl` and `ChainableDataFrame` classes enable location chaining
   up to 3 levels: country -> region -> place (e.g., `wkls.us.ca.sanfrancisco`).

3. **SedonaDB Integration:** Uses Apache SedonaDB for spatial SQL queries against
   Overture Maps GeoParquet data hosted on AWS S3.

4. **SQL Templates:** SQL queries are defined as module-level constants (e.g.,
   `CITY_QUERY`, `REGION_QUERY`) and use f-string formatting with `sqlescape()`.

## Dependencies

**Runtime:**
- `sedonadb>=0.2.0` - Spatial query engine
- `pyarrow>=14.0.0` - Arrow table data extraction
- `geoarrow-pyarrow>=0.2.0` - GeoArrow integration for spatial data
- `sqlescapy>=1.0.1` - SQL escaping

**Development:**
- `pytest>=8.3.5` - Testing framework
- `ruff>=0.11.12` - Linting and formatting

## Important Notes

- Tests require internet access to fetch data from AWS Open Data Registry
- Enable debug logging with `WKLS_DEBUG=true` environment variable
- The library is typed (includes `py.typed` marker)
