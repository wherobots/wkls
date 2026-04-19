# wkls: Well-Known Locations

[![PyPI version](https://img.shields.io/pypi/v/wkls.svg)](https://pypi.org/project/wkls/)
[![PyPI downloads](https://img.shields.io/pypi/dm/wkls.svg)](https://pypi.org/project/wkls/)
[![Python versions](https://img.shields.io/pypi/pyversions/wkls.svg)](https://pypi.org/project/wkls/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/wherobots/wkls/actions/workflows/run_tests.yaml/badge.svg)](https://github.com/wherobots/wkls/actions/workflows/run_tests.yaml)

`wkls` gives you administrative boundaries — countries, regions, counties, and cities — in one line of Python.

```python
import wkls

wkls.us.ca.sanfrancisco.wkt()
# "MULTIPOLYGON (((-122.5279985 37.8155806...)))"
```

- Chainable attribute access by ISO code *or* English name (`wkls.us.ca.sanfrancisco`, `wkls.india.maharashtra`)
- `.search("query")` at any chain level — scoped to the current subtree
- Precise geometries from [Overture Maps Foundation](https://overturemaps.org/) — no bounding boxes, no shapefiles
- Outputs boundaries in WKT, WKB, or GeoJSON
- Support for HexWKB and SVG planned
- Zero configuration — no API keys, no downloads, no setup
- Automatically uses the latest Overture Maps release

## Installation

```bash
pip install wkls
```

## Usage

### Countries, regions, counties, and cities

Chain up to 3 levels: **country** → **region** → **county or city**.

```python
import wkls

wkls.us.wkt()                  # United States
wkls.us.ca.wkt()               # California
wkls.us.ca.sanfrancisco.wkt()  # San Francisco
```

Every level accepts an [ISO 3166-1 alpha-2](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) code
*or* the English name (lowercase, spaces removed):

```python
wkls.india.maharashtra.wkt()   # country name + region name
wkls.us.oregon                 # sidesteps the `or` keyword collision
wkls.austria.burgenland        # sidesteps numeric region suffixes (AT-1)
```

If you prefer explicit instantiation over the module singleton:

```python
from wkls import Wkl
wkl = Wkl()
wkl.us.ca.sanfrancisco.wkt()
```

### Geometry formats

```python
wkls.de.wkt()      # Well-Known Text string
wkls.de.wkb()      # Well-Known Binary bytes
wkls.de.geojson()  # GeoJSON string
```

### Exploring the dataset

Listing methods scope to the current chain, all the way up to the dataset root:

```python
wkls.countries()       # all countries
wkls.dependencies()    # all dependencies
wkls.regions()         # every region worldwide
wkls.counties()        # every county worldwide
wkls.cities()          # every city worldwide

wkls.us.regions()      # regions in the US
wkls.us.counties()     # every county in the US
wkls.us.cities()       # every city in the US

wkls.us.ca.counties()  # counties in California
wkls.us.ca.cities()    # cities in California
```

`dir(wkls)` and `dir(wkls.us)` list every attribute that resolves at that
level — both ISO codes and English names — for interactive exploration
and tab completion.

### Search

`.search(query)` finds every row whose name contains the query substring.
Scope narrows with chain depth:

```python
wkls.search("san francisco")        # anywhere in the dataset
wkls.us.search("san francisco")     # anywhere in the US
wkls.us.ca.search("san francisco")  # anywhere in California
```

Results come back as a DataFrame with a `subtype` column, so callers can
filter countries from cities easily.

### Pinning an Overture version

`wkls` auto-detects the latest Overture Maps release. To pin a specific version:

```python
wkls.overture_releases()  # list currently available versions
wkls.configure(overture_version="2026-04-15.0")
wkls.overture_version()   # current version
```

Or set the `WKLS_OVERTURE_VERSION` environment variable:

```bash
export WKLS_OVERTURE_VERSION=2026-04-15.0
```

Priority: `configure()` > environment variable > auto-detect.

Overture retains only the 2 most recent releases on S3, so long-lived
pins eventually become invalid. Use `overture_releases()` to check
what's currently available.

### Bracket access (deprecated)

Bracket access (`wkls.us["ne"]`, `wkls.us.ca["%fran%"]`) still works but
emits a `DeprecationWarning` pointing at the modern replacement:

- Keyword / numeric collisions → use the English name:
  `wkls.us.nebraska`, `wkls.austria.burgenland`.
- Wildcard search → use `.search()`:
  `wkls.us.ca.search("fran")`.

Bracket access at the module root (`wkls["..."]`) is not supported — real
Python modules aren't subscriptable. Use dot access or `Wkl()["..."]`.

## How it works

`wkls` resolves locations in two stages:

1. **Metadata resolution** — your chained attributes are matched against a
   bundled metadata table (country by ISO code, region by code suffix, county
   or city by name). No geometry is loaded at this stage.

2. **Geometry fetch** — when you call `.wkt()`, `.wkb()`, or `.geojson()`, the geometry is
   fetched from Overture Maps GeoParquet on S3 via
   [Apache SedonaDB](https://sedona.apache.org/sedonadb/).

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details on how to get started, development setup, and submission guidelines.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
`wkls` includes, references, and leverages data from the "Divisions" theme of [Overture](https://overturemaps.org), from Overture Maps Foundation:

 * © OpenStreetMap contributors. Available under the [Open Database License](https://www.openstreetmap.org/copyright).
 * [geoBoundaries](https://www.geoboundaries.org/). Available under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
 * [Esri Community Maps contributors](https://communitymaps.arcgis.com/home/). Available under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
 * [Land Information New Zealand (LINZ)](https://www.linz.govt.nz/). Available under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Acknowledgments

- [Overture Maps Foundation](https://overturemaps.org/) for providing high-quality, open geospatial data.
- [AWS Open Data Registry](https://registry.opendata.aws/) for hosting the dataset.
- [Apache SedonaDB](https://sedona.apache.org/sedonadb/) for the high-performance, single-node spatial query and analytics engine.
