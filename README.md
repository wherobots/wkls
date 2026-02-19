# wkls: Well-Known Locations

[![PyPI version](https://img.shields.io/pypi/v/wkls.svg)](https://pypi.org/project/wkls/)
[![PyPI downloads](https://img.shields.io/pypi/dm/wkls.svg)](https://pypi.org/project/wkls/)
[![Python versions](https://img.shields.io/pypi/pyversions/wkls.svg)](https://pypi.org/project/wkls/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/wherobots/wkls/actions/workflows/run_tests.yaml/badge.svg)](https://github.com/wherobots/wkls/actions/workflows/run_tests.yaml)

Precise administrative boundaries — countries, regions, and cities — in one line of Python.

```python
import wkls

wkls.us.ca.sanfrancisco.wkt()
# "MULTIPOLYGON (((-122.5279985 37.8155806...)))"
```

- Chainable attribute access to countries, states, counties, and cities
- Precise geometries from [Overture Maps Foundation](https://overturemaps.org/) — no bounding boxes, no shapefiles
- Output in WKT or WKB
- Zero configuration — no API keys, no downloads, no setup
- Automatically uses the latest Overture Maps release

## Installation

```bash
pip install wkls
```

## Usage

### Countries, regions, and places

Chain up to 3 levels: **country** → **region** → **place**.

```python
import wkls

wkls.us.wkt()                  # United States
wkls.us.ca.wkt()               # California
wkls.us.ca.sanfrancisco.wkt()  # San Francisco
```

Countries and dependencies use [ISO 3166-1 alpha-2](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) codes.
Regions use the code suffix from Overture (e.g. `ca` for `US-CA`).
Places match by name against counties, localities, and neighborhoods.

### Geometry formats

```python
wkls.de.wkt()  # Well-Known Text string
wkls.de.wkb()  # Well-Known Binary bytes
```

### Exploring the dataset

```python
wkls.countries()       # all countries
wkls.dependencies()    # all dependencies
wkls.us.regions()      # regions in the US
wkls.us.ca.counties()  # counties in California
wkls.us.ca.cities()    # cities in California
wkls.fk.cities()       # countries without regions work too
```

### Wildcard search

Use `%` for pattern matching when you're not sure of the exact name:

```python
wkls.us.ca["%francis%"]  # matches "San Francisco"
```

### Pinning an Overture version

`wkls` auto-detects the latest Overture Maps release. To pin a specific version:

```python
wkls.configure(overture_version="2025-12-17.0")

wkls.overture_version()   # current version
wkls.overture_releases()  # available versions
```

Or set the `WKLS_OVERTURE_VERSION` environment variable:

```bash
export WKLS_OVERTURE_VERSION=2025-12-17.0
```

Priority: `configure()` > environment variable > auto-detect.

### Bracket access

Some names collide with Python keywords or DataFrame methods. Use bracket
syntax when attribute access doesn't work:

```python
wkls["us"]["ne"].wkt()  # Nebraska (wkls.us.ne would call DataFrame.ne)
wkls["at"]["1"].regions()  # Austria's region "1"
```

You can mix attribute and bracket access freely.

## How it works

`wkls` resolves locations in two stages:

1. **Metadata resolution** — your chained attributes are matched against a
   bundled metadata table (country by ISO code, region by code suffix, place
   by name). No geometry is loaded at this stage.

2. **Geometry fetch** — when you call `.wkt()` or `.wkb()`, the geometry is
   fetched from Overture Maps GeoParquet on S3 via
   [Apache SedonaDB](https://sedona.apache.org/sedonadb/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

This project uses data from the Overture Maps Foundation [Divisions](https://docs.overturemaps.org/schema/reference/divisions/division/) theme:

- © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL)
- [geoBoundaries](https://www.geoboundaries.org/) (CC BY 4.0)
- [Esri Community Maps](https://communitymaps.arcgis.com/home/) (CC BY 4.0)
- [Land Information New Zealand](https://www.linz.govt.nz/) (CC BY 4.0)

## Acknowledgments

- [Overture Maps Foundation](https://overturemaps.org/) for open geospatial data
- [AWS Open Data Registry](https://registry.opendata.aws/) for hosting
- [Apache SedonaDB](https://sedona.apache.org/sedonadb/) for spatial queries
