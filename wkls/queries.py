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
    WHERE subtype IN ('country', 'dependency')
      AND (
        country ILIKE '{country}'
        OR REPLACE(name_en, ' ', '') ILIKE REPLACE('{country}', ' ', '')
        OR REPLACE(name_primary, ' ', '') ILIKE REPLACE('{country}', ' ', '')
      )
"""

REGION = """
    SELECT * FROM wkls
    WHERE country ILIKE '{country}'
      AND subtype = 'region'
      AND (
        region ILIKE '{region}'
        OR REPLACE(name_en, ' ', '') ILIKE REPLACE('{region_name}', ' ', '')
        OR REPLACE(name_primary, ' ', '') ILIKE REPLACE('{region_name}', ' ', '')
      )
"""

COUNTRY_LOOKUP = """
    SELECT DISTINCT country AS iso
    FROM wkls
    WHERE subtype IN ('country', 'dependency')
      AND (
        country ILIKE '{identifier}'
        OR REPLACE(name_en, ' ', '') ILIKE REPLACE('{identifier}', ' ', '')
        OR REPLACE(name_primary, ' ', '') ILIKE REPLACE('{identifier}', ' ', '')
      )
    LIMIT 1
"""

COUNTRY_HAS_REGIONS = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND subtype = 'region'
      LIMIT 1
"""

REGION_LOOKUP = """
    SELECT DISTINCT region AS iso
    FROM wkls
    WHERE country = '{country}'
      AND subtype = 'region'
      AND (
        region ILIKE '{identifier}'
        OR REPLACE(name_en, ' ', '') ILIKE REPLACE('{name}', ' ', '')
        OR REPLACE(name_primary, ' ', '') ILIKE REPLACE('{name}', ' ', '')
      )
    LIMIT 1
"""

ROW_BY_ID = """
    SELECT id, country, region, subtype, name_primary, name_en, parent_id
    FROM wkls
    WHERE id = '{row_id}'
    LIMIT 1
"""

# Children of a resolved row (4-level chain parent narrower).
# Matches by parent_id AND location name. parent_id self-references
# another row's ``id`` within the bundled metadata.
CHILDREN_BY_PARENT = """
    SELECT * FROM wkls
    WHERE parent_id = '{parent_id}'
      AND (
        REPLACE(name_primary, ' ', '') ILIKE REPLACE('{name}', ' ', '')
        OR REPLACE(name_en, ' ', '') ILIKE REPLACE('{name}', ' ', '')
      )
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
    SELECT DISTINCT LOWER(SPLIT_PART(region, '-', 2)) as chainable_name
    FROM wkls
    WHERE country = '{country}'
      AND subtype = 'region'
      AND (
        LOWER(SPLIT_PART(region, '-', 2)) LIKE '{search_term}%'
        OR '{search_term}' LIKE LOWER(SPLIT_PART(region, '-', 2)) || '%'
      )
    ORDER BY chainable_name ASC
    LIMIT {limit}
"""

# Common expression for normalizing names to chainable format.
# Used by city-level suggestions, dir() queries, and search().
_CHAINABLE_NAME_EXPR = (
    "LOWER(REGEXP_REPLACE(COALESCE(name_en, name_primary), '[^a-zA-Z0-9]', '', 'g'))"
)

# --- dir() queries (for Wkl.__dir__ introspection) ---

# Root dir(): ISO codes and normalized names for countries and dependencies.
DIR_COUNTRIES = f"""
    SELECT DISTINCT
      LOWER(country)          AS iso,
      {_CHAINABLE_NAME_EXPR}  AS name
    FROM wkls
    WHERE subtype IN ('country', 'dependency')
"""

# Country-level dir(): ISO suffixes and normalized names for one country's regions.
DIR_REGIONS = f"""
    SELECT DISTINCT
      LOWER(SPLIT_PART(region, '-', 2)) AS iso,
      {_CHAINABLE_NAME_EXPR}            AS name
    FROM wkls
    WHERE country = '{{country}}'
      AND subtype = 'region'
"""

# --- search() queries ---
# Each level scans all entities in its chain scope (any subtype), matching
# the query substring against name_primary and name_en.

SEARCH_ROOT = """
    SELECT DISTINCT id, country, region, subtype, name_primary, name_en, parent_id
    FROM wkls
    WHERE name_primary ILIKE '%{query}%'
       OR name_en ILIKE '%{query}%'
    ORDER BY COALESCE(name_en, name_primary) ASC
"""

SEARCH_COUNTRY = """
    SELECT DISTINCT id, country, region, subtype, name_primary, name_en, parent_id
    FROM wkls
    WHERE country = '{country}'
      AND (
        name_primary ILIKE '%{query}%'
        OR name_en ILIKE '%{query}%'
      )
    ORDER BY COALESCE(name_en, name_primary) ASC
"""

SEARCH_REGION = """
    SELECT DISTINCT id, country, region, subtype, name_primary, name_en, parent_id
    FROM wkls
    WHERE country = '{country}'
      AND region = '{region}'
      AND (
        name_primary ILIKE '%{query}%'
        OR name_en ILIKE '%{query}%'
      )
    ORDER BY COALESCE(name_en, name_primary) ASC
"""

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
