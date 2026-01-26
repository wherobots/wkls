"""
SQL query templates for wkls.

This module contains all SQL queries used by the wkls library for querying
the Overture Maps dataset via SedonaDB.
"""

# Initialization query for SedonaDB
INITIALIZATION = """
    SET datafusion.execution.parquet.pushdown_filters = true
"""

# --- Resolution queries (for resolving location chains) ---

COUNTRY_DEPENDENCY = """
    SELECT * FROM wkls
    WHERE country ILIKE '{country}'
      AND subtype IN ('country', 'dependency')
"""

REGION = """
    SELECT * FROM wkls
    WHERE country ILIKE '{country}'
      AND region ILIKE '{region}'
      AND subtype = 'region'
"""

CITY = """
    SELECT * FROM wkls
    WHERE country ILIKE '{country}'
      AND region ILIKE '{region}'
      AND subtype IN ('county', 'locality', 'localadmin')
      AND (
        REPLACE(name_primary, ' ', '') ILIKE REPLACE('{city}', ' ', '')
        OR
        REPLACE(name_en, ' ', '') ILIKE REPLACE('{city}', ' ', '')
    )
"""

CITY_NO_REGION = """
    SELECT * FROM wkls
    WHERE country ILIKE '{country}'
      AND subtype IN ('county', 'locality', 'localadmin')
      AND (
        REPLACE(name_primary, ' ', '') ILIKE REPLACE('{city}', ' ', '')
        OR
        REPLACE(name_en, ' ', '') ILIKE REPLACE('{city}', ' ', '')
    )
"""

# --- Metadata queries ---

COUNTRY_HAS_REGIONS = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND subtype != 'country'
      AND region IS NULL
"""

REGIONS_LIST = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND subtype = 'region'
"""

COUNTRIES_LIST = """
    SELECT DISTINCT id, country, subtype, name_primary, name_en
    FROM wkls
    WHERE subtype = 'country'
"""

DEPENDENCIES_LIST = """
    SELECT DISTINCT id, country, subtype, name_primary, name_en
    FROM wkls
    WHERE subtype = 'dependency'
"""

SUBTYPES_LIST = """
    SELECT DISTINCT subtype FROM wkls
"""

SUBDIVISIONS = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND region = '{region}'
      AND subtype IN {subtype_filter}
"""

SUBDIVISIONS_NO_REGION = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND subtype IN {subtype_filter}
"""

# --- Suggestion queries (for "did you mean" feature) ---

# For country-level suggestions (bidirectional prefix match on ISO codes)
SUGGEST_COUNTRY = """
    SELECT DISTINCT LOWER(country) as chainable_name
    FROM wkls
    WHERE subtype = 'country'
      AND (
        LOWER(country) LIKE '{search_term}%'
        OR '{search_term}' LIKE LOWER(country) || '%'
      )
    ORDER BY chainable_name ASC
    LIMIT {limit}
"""

# For region-level suggestions (bidirectional prefix match on region codes)
SUGGEST_REGION = """
    SELECT DISTINCT LOWER(SUBSTRING(region, 4)) as chainable_name
    FROM wkls
    WHERE country = '{country}'
      AND subtype = 'region'
      AND (
        LOWER(SUBSTRING(region, 4)) LIKE '{search_term}%'
        OR '{search_term}' LIKE LOWER(SUBSTRING(region, 4)) || '%'
      )
    ORDER BY chainable_name ASC
    LIMIT {limit}
"""

# Common expression for normalizing city names to chainable format
_CHAINABLE_NAME_EXPR = (
    "LOWER(REGEXP_REPLACE(COALESCE(name_en, name_primary), '[^a-zA-Z0-9]', '', 'g'))"
)

# For city-level suggestions (Levenshtein fuzzy matching)
# Use {region_filter} = "AND region = '{region}'" or "" for countries without regions
SUGGEST_CITY = f"""
    SELECT DISTINCT
           {_CHAINABLE_NAME_EXPR} as chainable_name,
           CASE
             WHEN {_CHAINABLE_NAME_EXPR} = '{{search_term}}' THEN 0
             WHEN {_CHAINABLE_NAME_EXPR} LIKE '{{search_term}}%' THEN 1
             WHEN {_CHAINABLE_NAME_EXPR} LIKE '%{{search_term}}%' THEN 2
             ELSE levenshtein({_CHAINABLE_NAME_EXPR}, '{{search_term}}') + 10
           END as distance
    FROM wkls
    WHERE country = '{{country}}'
      {{region_filter}}
      AND subtype IN ('county', 'locality', 'localadmin')
    ORDER BY distance ASC, chainable_name ASC
    LIMIT {{limit}}
"""
