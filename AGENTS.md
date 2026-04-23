# Using wkls from an AI agent

`wkls` gives you administrative-boundary geometries (countries,
regions, counties, cities) from Overture Maps via chainable Python
attributes. No API keys, no setup.

## The five things you'll actually do

### 1. Get a geometry

```python
import wkls

wkls.us.ca.sanfrancisco.wkt()      # WKT string
wkls.india.maharashtra.geojson()   # GeoJSON string
wkls.de.wkb()                      # WKB bytes
```

Chain is `<country>.<region>.<city>` — max 3 levels for unambiguous
cases. Names are lowercased, non-alphanumerics stripped. ISO codes
work too: `wkls.us.ca.sanfrancisco` or
`wkls.unitedstates.california.sanfrancisco`.

### 2. Handle ambiguity

When a chain resolves to multiple rows, geometry methods raise
`AmbiguousLocationError` (a subclass of `ValueError`). The message
lists every candidate with its subtype, parent name, and UUID.

Three dot-faithful ways to narrow:

```python
# Subtype modifier — when candidates differ by kind
wkls.us.ca.mission.locality        # the city of Mission
wkls.us.ca.mission.county          # Mission County

# 4-level parent narrower — name the parent in the chain
wkls.us.pa.adamscounty.franklin    # Franklin in Adams County, PA

# Escape hatch — pick by the UUID shown in the error message
wkls.by_id('273bc9a0-96a1-402c-992c-84f5c2f212cb').wkt()
```

### 3. Find things by name

`.search(q)` at any chain depth. Scope narrows with depth. Query is
normalized (lowercased, non-alphanumerics stripped), so
`"san francisco"`, `"San Francisco"`, and `"sanfrancisco"` match the
same rows.

```python
wkls.search("mission")             # anywhere in the dataset
wkls.us.search("mission")          # anywhere in the US
wkls.us.ca.search("mission")       # anywhere in California
```

Returns a `Wkl` — single-row results support `.wkt()` directly;
multi-row results support subtype modifiers to narrow further.

### 4. Navigate the hierarchy

```python
wkls.us.ca.sanfrancisco.parent     # → California (one level up)
wkls.us.ca.sanfrancisco.path       # 'wkls.us.ca.sanfrancisco'
                                   #   (round-trips through eval)
```

### 5. List subtrees

```python
wkls.countries()                   # all countries
wkls.us.regions()                  # regions in the US
wkls.us.ca.counties()              # counties in California
wkls.us.ca.sandiegocounty.cities() # cities in San Diego County
```

## The single return type

Every attribute / method returns a `Wkl`. Inspection methods:

- `.count()` — number of rows
- `.head(n)` / `.to_arrow_table()` — see the rows
- `.wkt()` / `.wkb()` / `.geojson()` — geometry (requires exactly 1 row)
- `.path` — dot-access path (requires single row)
- `.parent` — hierarchy walk (requires single row, not at country level)

`dir(wkl_instance)` returns only the methods that are valid for the
current state — use it to discover what's available.

## Error model

- `AmbiguousLocationError` (subclass of `ValueError`) — geometry on a
  multi-row `Wkl`. Message lists candidates and suggests narrowers.
- `ValueError` — empty chain, chain too deep, `.parent` at country
  level, listing methods called on an ambiguous chain.
- Chain attributes that don't resolve return a `Wkl` with
  `.count() == 0` rather than raising.

## Common gotchas

- Diacritics aren't folded: `wkls.ivorycoast` won't resolve because
  `name_en` is "Côte d'Ivoire". Use the ISO code: `wkls.ci`.
- `Wkl()[...]` bracket access is deprecated; use dot access or
  `.search()`.
- Don't call geometry methods on a multi-row result — check
  `.count() == 1` first, or use one of the narrowers.
