# Contributing to wkls

We welcome contributions! If you're unsure where to start, check the
[open issues](https://github.com/wherobots/wkls/issues) or open a new issue
to discuss your idea.

## Development setup

This project uses [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
as its package manager.

```bash
# Fork and clone the repository, then:
cd wkls
uv sync --all-extras --dev  # install all dependencies (including dev tools)

# Verify everything works
uv run pytest tests/ -v
```

> [!NOTE]
> Tests require internet access to fetch data from the AWS Open Data Registry.

## Running tests

```bash
uv run pytest tests/ -v                                    # all tests
uv run pytest tests/test_us.py -v                          # specific file
uv run pytest tests/test_us.py::test_overture_version -v   # specific test
uv run pytest tests/ --cov=wkls                            # with coverage
```

## Code style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.
CI will reject PRs that don't pass both checks:

```bash
uv run ruff check .   # lint
uv run ruff format .  # format
```

## Updating the bundled metadata

The file `wkls/data/overture.zstd18.parquet` contains pre-extracted metadata
(no geometry) from the Overture Maps dataset. When a new Overture release is
available, regenerate it:

```bash
uv run python scripts/generate_metadata.py              # latest release
uv run python scripts/generate_metadata.py --version 2026-08-19.0  # specific version
uv run python scripts/generate_metadata.py --list       # list available releases
```

After regenerating, run the full test suite — count-based assertions in
`tests/test_us.py` may need updating to reflect changes in the new data.

You can inspect the embedded Overture version in the parquet metadata:

```bash
uv run python -c "import pyarrow.parquet as pq; print(pq.read_metadata('wkls/data/overture.zstd18.parquet').metadata)"
```

## Debug mode

Set the `WKLS_DEBUG` environment variable to print the SQL queries executed
by SedonaDB:

```python
import os
import wkls

os.environ["WKLS_DEBUG"] = "true"
wkls.us.ca.sanfrancisco.wkt()  # prints the underlying SQL
```

## Submitting changes

1. Create a feature branch: `git checkout -b feature-name`
1. Make your changes with tests
1. Ensure tests and linting pass: `uv run pytest tests/ -v && uv run ruff check .`
1. Commit using [Conventional Commits](https://www.conventionalcommits.org/):
   `git commit -m "feat: add new geometry format"`
1. Push and open a pull request

## Releases

See [RELEASING.md](RELEASING.md) for versioning policy and release process.
