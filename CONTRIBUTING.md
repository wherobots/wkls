# Contributing to WKLS

We welcome contributions! Whether you're fixing bugs, adding features, improving documentation, or enhancing test coverage, your help is appreciated.

## Getting Started

1. **Fork and clone** the repository
2. **Set up the development environment**:
   ```bash
   cd wkls
   uv sync --all-extras --dev
   ```
3. **Run the tests** to make sure everything works:
   ```bash
   uv run pytest tests/ -v
   ```
4. **Make your changes** and add tests if needed
5. **Run the linting and formatting**:
   ```bash
   uv run ruff check .
   uv run ruff format .
   ```
6. **Submit a pull request**

## Areas for Contribution

- **Bug fixes**: Fix issues with geometry retrieval, chaining logic, or error handling
- **Performance improvements**: Optimize data loading or query performance
- **Documentation**: Improve examples, add use cases, or enhance API documentation  
- **Testing**: Add test cases for edge cases or new functionality
- **Feature requests**: Add new geometry formats or helper methods

## Before Contributing

- **Check existing issues** to see if your idea is already being discussed
- **Open an issue** for major changes to discuss the approach first
- **Follow the coding style** enforced by ruff
- **Add tests** for any new functionality
- **Update documentation** if you change the API

## Development Notes

- The library uses **Apache SedonaDB** for spatial operations and **PyArrow** for data extraction
- Geometry data comes from **Overture Maps Foundation** via S3
- Tests require internet access to fetch data from AWS Open Data Registry

## Updating the Bundled Metadata

The file `wkls/data/overture.zstd18.parquet` contains pre-extracted metadata (no geometry) from the Overture Maps dataset. When a new Overture release is available, regenerate it using the provided script:

```bash
# Auto-detect and use the latest Overture release
uv run python scripts/generate_metadata.py

# Or pin a specific version
uv run python scripts/generate_metadata.py --version 2026-01-21.0

# List available releases
uv run python scripts/generate_metadata.py --list
```

The script embeds the Overture version in the parquet file-level metadata for traceability. You can inspect it with:

```bash
uv run python -c "import pyarrow.parquet as pq; print(pq.read_metadata('wkls/data/overture.zstd18.parquet').metadata)"
```

After regenerating, run the full test suite — some count-based assertions in `tests/test_us.py` may need updating to reflect changes in the new Overture release.

## Development Commands

```bash
# Install dependencies with development tools
uv sync --all-extras --dev

# Run tests
uv run pytest tests/ -v

# Run specific test file  
uv run pytest tests/test_us.py -v

# Check code style
uv run ruff check .

# Auto-format code
uv run ruff format .

# Build the package
uv build

# Run all tests with coverage
uv run pytest tests/ --cov=wkls
```

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. Make sure your code passes:

```bash
uv run ruff check .
uv run ruff format .
```

## Testing

We use pytest for testing. Please add tests for any new functionality:

```bash
# Run all tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ --cov=wkls

# Run specific test
uv run pytest tests/test_us.py::test_overture_version -v
```

## Submitting Changes

1. **Create a feature branch**: `git checkout -b feature-name`
2. **Make your changes** with appropriate tests
3. **Ensure all tests pass**: `uv run pytest tests/ -v`
4. **Check code style**: `uv run ruff check . && uv run ruff format .`
5. **Commit your changes**: `git commit -m "Add feature description"`
6. **Push to your fork**: `git push origin feature-name`
7. **Create a pull request** on GitHub

## Releases

For release guidelines, versioning policy, and the release checklist, see
[RELEASING.md](RELEASING.md).

## Questions?

Feel free to open an issue if you have questions about contributing or need help getting started!
