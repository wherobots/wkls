# Releasing wkls

This document describes the versioning policy, release cadence, and step-by-step
release process for `wkls`.

## Versioning Policy

wkls follows [Semantic Versioning](https://semver.org/) (SemVer):

- **Patch** (`1.0.x`): metadata refreshes from new Overture Maps releases, bug fixes.
  No code changes required by users.
- **Minor** (`1.x.0`): new features such as geometry formats or helper methods.
  Additive and non-breaking.
- **Major** (`x.0.0`): breaking changes to the public API (chainable syntax,
  geometry accessors, utility methods). We don't anticipate these frequently.

### A Note on Data Stability

The `wkls` API is stable: the chainable syntax (`wkls.us.ca.sanfrancisco`),
geometry accessors (`.wkt()`, `.wkb()`), and utility methods will not change
without a major version bump.

However, **place names come from Overture Maps Foundation data and may change
between releases**. For example, if Overture renames "City of New York" to
"New York", the accessor changes from `wkls.us.ny.cityofnewyork` to
`wkls.us.ny.newyork`. This is a data-level change outside our control, not an
API change.

If you need deterministic behavior, pin your `wkls` version:

```
wkls==1.0.3
```

Note that pinned versions may reference Overture releases that have been removed
from S3 (Overture retains only the 2 most recent releases). In practice, the
geometry fetch uses the latest available Overture release by default, so this
is rarely an issue unless you've also pinned the Overture version via
`wkls.configure(overture_version="...")`.

## Release Cadence

### Bi-monthly releases aligned with Overture Maps

Overture Maps releases new data approximately monthly (~21st of each month)
and retains only the **2 most recent releases** on S3. `wkls` should release
a metadata refresh **every 2 months** to keep the bundled metadata within
Overture's retention window.

- **Metadata refresh releases** (patch): regenerate `overture.zstd18.parquet`,
  bump the patch version, tag and release. Even if there are zero code changes.
- **Feature releases** (minor): bundle with the next metadata refresh, or release
  independently if the feature is ready.
- **Bug fix releases** (patch): release as needed, no fixed schedule.

## Release Checklist

### 1. Update the bundled metadata

Regenerate the metadata parquet from the latest Overture release:

```bash
uv run python scripts/generate_metadata.py
```

Run the test suite — count-based assertions in `tests/test_us.py` may need
updating to reflect changes in the new Overture data:

```bash
uv run pytest tests/ -v
```

### 2. Bump the version

Update the `version` field in `pyproject.toml`:

```toml
version = "1.0.1"  # or whatever the new version is
```

### 3. Commit and tag

```bash
git add .
git commit -m "chore: release v1.0.1"
git tag v1.0.1
git push origin main --tags
```

### 4. Verify the release

Pushing the tag triggers two GitHub Actions workflows:

- **`release.yaml`**: creates a GitHub Release with auto-generated release notes
- **`pypi-publish.yaml`**: runs the test suite, validates that the tag matches
  `pyproject.toml`, builds the package, and publishes to PyPI via OIDC trusted
  publishing

Check that both workflows complete successfully:

- [GitHub Actions](https://github.com/wherobots/wkls/actions)
- [PyPI package page](https://pypi.org/project/wkls/)

## Commit Message Conventions

We use [Conventional Commits](https://www.conventionalcommits.org/) for commit
messages. This makes the auto-generated release notes readable:

| Prefix | Use for |
|--------|---------|
| `feat:` | New features or capabilities |
| `fix:` | Bug fixes |
| `chore:` | Metadata refreshes, dependency updates, housekeeping |
| `docs:` | Documentation changes |
| `ci:` | CI/CD workflow changes |
| `build:` | Build system or packaging changes |
| `refactor:` | Code restructuring without behavior changes |
| `test:` | Test additions or modifications |
