"""
wkls - Well-Known Locations

A Python library for accessing global administrative boundaries using chainable syntax.
Fetches geometries from Overture Maps Foundation GeoParquet data.

Example usage:
    >>> import wkls
    >>> wkls.us.ca.sanfrancisco.wkt()
    'MULTIPOLYGON (((-122.5279985 37.8155806...)))'

    >>> wkls.countries()  # List all countries
    >>> wkls.us.regions()  # List US states/regions
"""

from __future__ import annotations

import importlib.resources
import os
from typing import Any, Callable

import sedonadb
import sqlescapy

from . import data

__all__ = ["Wkl", "ChainableDataFrame", "OVERTURE_VERSION"]

# Overture Maps dataset version
OVERTURE_VERSION = "2025-12-17.0"
OVERTURE_URI = f"s3://overturemaps-us-west-2/release/{OVERTURE_VERSION}/theme=divisions/type=division_area/"

# SQL query templates
INITIALIZATION_QUERY = """
    SET datafusion.execution.parquet.pushdown_filters = true
"""

COUNTRY_DEPENDENCY_QUERY = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND subtype IN ('country', 'dependency')
"""

REGION_QUERY = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND region = '{region}'
      AND subtype = 'region'
"""

CITY_QUERY = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND region = '{region}'
      AND subtype IN ('county', 'locality', 'localadmin')
      AND (
        REPLACE(name_primary, ' ', '') ILIKE REPLACE('{city}', ' ', '')
        OR
        REPLACE(name_en, ' ', '') ILIKE REPLACE('{city}', ' ', '')
    )
"""

CITY_QUERY_WITHOUT_REGION = """
    SELECT * FROM wkls
    WHERE country = '{country}'
      AND subtype IN ('county', 'locality', 'localadmin')
      AND (
        REPLACE(name_primary, ' ', '') ILIKE REPLACE('{city}', ' ', '')
        OR
        REPLACE(name_en, ' ', '') ILIKE REPLACE('{city}', ' ', '')
    )
"""

COUNTRY_REGION_CHECK_QUERY = """
    SELECT * FROM wkls
    WHERE country = '{country}'
    AND subtype != 'country'
    AND region IS NULL
"""


def _log_and_query(
    exec_fn: Callable[[str], sedonadb.dataframe.DataFrame], query: str
) -> sedonadb.dataframe.DataFrame:
    """Execute a SQL query with optional debug logging.

    Args:
        exec_fn: Function to execute the SQL query.
        query: SQL query string to execute.

    Returns:
        DataFrame containing the query results.
    """
    if os.environ.get("WKLS_DEBUG", "false").lower() in ["true", "yes", "1"]:
        print(query)
    return exec_fn(query)


def _initialize_table() -> sedonadb.SedonaContext:
    """Initialize the wkls table and Overture data views.

    Creates SedonaDB views for the local metadata table and remote
    Overture Maps GeoParquet data.

    Returns:
        Configured SedonaContext instance.
    """
    sedona = sedonadb.connect()

    # Enable interactive mode for auto-display
    sedona.options.interactive = True

    # Monkey-patch `.sql()` for debug mode.
    sedona_sql = sedona.sql
    sedona.sql = lambda q: _log_and_query(sedona_sql, q)

    sedona.sql(INITIALIZATION_QUERY)
    sedona.read_parquet(
        f"{importlib.resources.files(data)}/overture.zstd18.parquet"
    ).to_view("wkls")
    sedona.read_parquet(
        OVERTURE_URI,
        options={
            "aws.skip_signature": True,
            "aws.region": "us-west-2",
        },
    ).to_view("overture")
    return sedona


# Initialize the table when the module is imported
sedona = _initialize_table()


def sqlescape(v: str) -> str:
    """Escape a string for safe SQL interpolation.

    Escapes special characters while preserving % for LIKE operators.

    Args:
        v: String value to escape.

    Returns:
        SQL-safe escaped string.
    """
    # SQL escape, but maintain the use of % for the LIKE operator.
    return sqlescapy.sqlescape(v).replace("\\%", "%")


class ChainableDataFrame(sedonadb.dataframe.DataFrame):
    """A DataFrame that maintains chaining capability for the wkls library.

    This class wraps SedonaDB DataFrames to allow attribute-based chaining
    (e.g., `wkls.us.ca.sanfrancisco`) while preserving DataFrame functionality.

    Attributes:
        _chain: List of chained attribute names representing the location path.
    """

    _metadata = ["_chain"]

    def __init__(
        self, df: sedonadb.dataframe.DataFrame, chain: list[str] | None = None
    ) -> None:
        """Initialize a ChainableDataFrame.

        Args:
            df: Source SedonaDB DataFrame to wrap.
            chain: List of chained attribute names (e.g., ['us', 'ca']).
        """
        super().__init__(df._ctx, df._impl, df._options)
        object.__setattr__(self, "_chain", chain or [])

    def __getattr__(self, attr: str) -> ChainableDataFrame:
        """Handle attribute access for location chaining.

        Args:
            attr: Attribute name to access (e.g., 'ca' for California).

        Returns:
            New ChainableDataFrame with the attribute added to the chain.

        Raises:
            AttributeError: For internal attributes or root-only methods.
            ValueError: If chain exceeds maximum depth of 3.
        """
        # Avoid infinite recursion for pandas internal attributes
        if attr.startswith("_") or attr in ["_chain"]:
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        # Block root-level only methods
        if attr == "overture_version":
            raise AttributeError(
                f"'{attr}' is only available at the root level. Use wkls.{attr}(), not on chained objects."
            )

        # Continue chaining
        new_wkl = Wkl(self._chain + [attr.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3:
            raise ValueError("Too many chained attributes (max = 3)")
        if len(new_wkl.chain) <= 3:
            df = new_wkl.resolve()
            return ChainableDataFrame(df, new_wkl.chain)
        return new_wkl

    def __getitem__(
        self, key: Any
    ) -> ChainableDataFrame | sedonadb.dataframe.DataFrame:
        """Handle bracket access for location chaining or DataFrame indexing.

        Supports both pandas-style indexing and location chaining with
        search patterns (using % wildcards).

        Args:
            key: Column name, list of columns, slice, or location string.

        Returns:
            ChainableDataFrame for location chaining, or DataFrame for indexing.

        Raises:
            ValueError: If chain exceeds maximum depth of 3.
        """
        # If it's a regular pandas indexing operation, use parent class
        if isinstance(key, (str, list, slice)) and not (
            isinstance(key, str) and "%" in key
        ):
            return super().__getitem__(key)

        # Otherwise, handle chaining with search patterns
        new_wkl = Wkl(self._chain + [key.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3 and "%" not in str(key):
            raise ValueError("Too many chained attributes (max = 3)")
        if "%" in str(key):
            return new_wkl.resolve()
        return new_wkl

    def wkt(self) -> str:
        """Get Well-Known Text (WKT) geometry for the first result.

        Returns:
            WKT string representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        wkl = Wkl(self._chain)
        return wkl.wkt()

    def wkb(self) -> bytes:
        """Get Well-Known Binary (WKB) geometry for the first result.

        Returns:
            Binary WKB representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        wkl = Wkl(self._chain)
        return wkl.wkb()

    def hexwkb(self) -> str:
        """Get hex-encoded WKB geometry for the first result.

        Returns:
            Hex-encoded WKB string.

        Raises:
            NotImplementedError: This format is not yet supported.
        """
        wkl = Wkl(self._chain)
        return wkl.hexwkb()

    def geojson(self) -> str:
        """Get GeoJSON geometry for the first result.

        Returns:
            GeoJSON string representation.

        Raises:
            NotImplementedError: This format is not yet supported.
        """
        wkl = Wkl(self._chain)
        return wkl.geojson()

    def svg(self) -> str:
        """Get SVG path geometry for the first result.

        Returns:
            SVG path string.

        Raises:
            NotImplementedError: This format is not yet supported.
        """
        wkl = Wkl(self._chain)
        return wkl.svg()

    def dependencies(self) -> sedonadb.dataframe.DataFrame:
        """Get all dependencies (territories, overseas regions, etc.).

        Returns:
            DataFrame containing all dependency records.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        wkl = Wkl(self._chain)
        return wkl.dependencies()

    def countries(self) -> sedonadb.dataframe.DataFrame:
        """Get all countries.

        Returns:
            DataFrame containing all country records.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        wkl = Wkl(self._chain)
        return wkl.countries()

    def regions(self) -> sedonadb.dataframe.DataFrame:
        """Get regions for the current country.

        Must be called on a single-level chain (e.g., `wkls.us.regions()`).

        Returns:
            DataFrame containing region records for the country.

        Raises:
            ValueError: If called at wrong chain level.
        """
        wkl = Wkl(self._chain)
        return wkl.regions()

    def counties(self) -> sedonadb.dataframe.DataFrame:
        """Get counties for the current region.

        Must be called on a two-level chain (e.g., `wkls.us.ca.counties()`).

        Returns:
            DataFrame containing county records for the region.

        Raises:
            ValueError: If called at wrong chain level.
        """
        wkl = Wkl(self._chain)
        return wkl.counties()

    def cities(self) -> sedonadb.dataframe.DataFrame:
        """Get cities for the current region.

        Must be called on a two-level chain (e.g., `wkls.us.ca.cities()`).

        Returns:
            DataFrame containing city records for the region.

        Raises:
            ValueError: If called at wrong chain level.
        """
        wkl = Wkl(self._chain)
        return wkl.cities()

    def subtypes(self) -> sedonadb.dataframe.DataFrame:
        """Get all distinct division subtypes in the dataset.

        Returns:
            DataFrame containing all unique subtype values.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        wkl = Wkl(self._chain)
        return wkl.subtypes()

    @property
    def _constructor(self) -> type[ChainableDataFrame]:
        """Return the constructor for DataFrame operations.

        Returns:
            ChainableDataFrame class.
        """
        return ChainableDataFrame


class Wkl:
    """Well-Known Location resolver for administrative boundaries.

    This class handles the resolution of chained location attributes to
    database queries and geometry retrieval from Overture Maps data.

    The chain supports up to 3 levels:
        1. Country/Dependency (ISO 3166-1 alpha-2 code)
        2. Region (region code suffix)
        3. Place (county, locality, or neighborhood name)

    Example:
        >>> wkl = Wkl(['us', 'ca', 'sanfrancisco'])
        >>> wkl.wkt()
        'MULTIPOLYGON (((-122.5279985 37.8155806...)))'

    Attributes:
        chain: List of location identifiers in the chain.
    """

    _has_region: bool = True

    def __init__(self, chain: list[str] | None = None) -> None:
        """Initialize a Wkl instance.

        Args:
            chain: List of location identifiers (e.g., ['us', 'ca']).
        """
        if chain and len(chain) >= 1:
            country_iso = chain[0].upper()
            df_check = sedona.sql(
                COUNTRY_REGION_CHECK_QUERY.format(country=sqlescape(country_iso))
            )
            # If the query returns any rows, it means it has no regions
            self._has_region = df_check.count() == 0
        self.chain: list[str] = chain or []

    def overture_version(self) -> str:
        """Return the version of the Overture Maps dataset being used.

        This method is only available at the root level (wkls.overture_version()),
        not on chained objects.

        Returns:
            Version string of the Overture Maps dataset.

        Raises:
            ValueError: If called on a chained object.
        """
        if self.chain:
            raise ValueError(
                "overture_version() is only available at the root level. Use wkls.overture_version(), not wkls.us.overture_version()."
            )
        return OVERTURE_VERSION

    def __getattr__(self, attr: str) -> ChainableDataFrame | Wkl:
        """Handle attribute access for location chaining.

        Args:
            attr: Attribute name representing a location identifier.

        Returns:
            ChainableDataFrame if chain is complete, otherwise Wkl.

        Raises:
            AttributeError: For private/dunder attributes.
            ValueError: If chain exceeds maximum depth of 3.
        """
        # Don't intercept private/dunder attributes - raise AttributeError
        if attr.startswith("_"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        new_wkl = Wkl(self.chain + [attr.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3:
            raise ValueError("Too many chained attributes (max = 3)")

        if len(new_wkl.chain) <= 3:
            df = new_wkl.resolve()
            return ChainableDataFrame(df, new_wkl.chain)
        return new_wkl

    def __getitem__(
        self, key: str
    ) -> ChainableDataFrame | sedonadb.dataframe.DataFrame:
        """Handle bracket access for location chaining.

        Supports search patterns with % wildcards for fuzzy matching.

        Args:
            key: Location identifier or search pattern.

        Returns:
            ChainableDataFrame or DataFrame for search patterns.

        Raises:
            ValueError: If chain exceeds maximum depth of 3.
        """
        new_wkl = Wkl(self.chain + [key.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3 and "%" not in key:
            raise ValueError("Too many chained attributes (max = 3)")
        # If this looks like a search pattern (contains %), return DataFrame directly
        if "%" in key:
            return new_wkl.resolve()
        return new_wkl

    def __repr__(self) -> str:
        """Return string representation of the resolved DataFrame.

        Returns:
            String representation of the underlying data.
        """
        return repr(self.resolve())

    def resolve(self) -> sedonadb.dataframe.DataFrame:
        """Resolve the location chain to a DataFrame.

        Executes the appropriate SQL query based on the chain depth
        to retrieve matching location records.

        Returns:
            DataFrame containing matching location records.

        Raises:
            ValueError: If the chain is empty.
        """
        if not self.chain:
            raise ValueError(
                "No attributes in the chain. Use wkls.country or wkls.country.region, etc."
            )

        params: dict[str, str] = {}
        country_iso = self.chain[0].upper()
        query = COUNTRY_DEPENDENCY_QUERY
        params["country"] = country_iso

        if len(self.chain) > 1:
            if self._has_region:
                query = REGION_QUERY
                region_iso = country_iso + "-" + self.chain[1].upper()
                params["region"] = region_iso
            else:
                query = CITY_QUERY_WITHOUT_REGION
                city = self.chain[1].lower()
                params["city"] = city

        if len(self.chain) > 2:
            query = CITY_QUERY
            region_iso = country_iso + "-" + self.chain[1].upper()
            city = self.chain[2]
            params["region"] = region_iso
            params["city"] = city

        return sedona.sql(query.format(**{k: sqlescape(v) for k, v in params.items()}))

    def _get_geom_expr(self, expr: str) -> Any:
        """Retrieve geometry using a SQL expression.

        Args:
            expr: SQL expression to apply to the geometry column.

        Returns:
            Result of the geometry expression (type depends on expression).

        Raises:
            ValueError: If no results found or no geometry exists.
        """
        df = self.resolve()
        if df.count() == 0:
            raise ValueError(f"No result found for: {'.'.join(self.chain)}")

        geom_id = df.to_pandas().iloc[0]["id"]
        query = f"""
            SELECT {expr}
            FROM overture
            WHERE id = '{geom_id}'
        """
        result_df = sedona.sql(query)
        if result_df.count() == 0:
            raise ValueError(f"No geometry found for ID: {geom_id}")
        return result_df.to_pandas().iloc[0, 0]

    def wkt(self) -> str:
        """Get Well-Known Text (WKT) geometry for the first result.

        Returns:
            WKT string representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        return self._get_geom_expr("ST_AsText(geometry)")

    def wkb(self) -> bytes:
        """Get Well-Known Binary (WKB) geometry for the first result.

        Returns:
            Binary WKB representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        return self._get_geom_expr("ST_AsWKB(geometry)")

    def hexwkb(self) -> str:
        """Get hex-encoded WKB geometry for the first result.

        Returns:
            Hex-encoded WKB string.

        Raises:
            NotImplementedError: This format is not yet supported in SedonaDB.
        """
        # return self._get_geom_expr("ST_AsHEXWKB(geometry)")
        raise NotImplementedError("ST_AsHEXWKB() isn't implemented yet")

    def geojson(self) -> str:
        """Get GeoJSON geometry for the first result.

        Returns:
            GeoJSON string representation.

        Raises:
            NotImplementedError: This format is not yet supported in SedonaDB.
        """
        # return self._get_geom_expr("ST_AsGeoJSON(geometry)")
        raise NotImplementedError("ST_AsGeoJSON() isn't implemented yet")

    def svg(self, relative: bool = False, precision: int = 15) -> str:
        """Get SVG path geometry for the first result.

        Args:
            relative: Use relative coordinates if True.
            precision: Decimal precision for coordinates.

        Returns:
            SVG path string.

        Raises:
            NotImplementedError: This format is not yet supported in SedonaDB.
        """
        # return self._get_geom_expr(
        #     f"ST_AsSVG(geometry, {str(relative).lower()}, {precision})"
        # )
        raise NotImplementedError("ST_AsSVG() isn't implemented yet")

    def dependencies(self) -> sedonadb.dataframe.DataFrame:
        """Get all dependencies (territories, overseas regions, etc.).

        Returns:
            DataFrame containing all dependency records with id, country,
            subtype, name_primary, and name_en columns.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        if self.chain:
            raise ValueError(
                "dependencies() can only be called on the root object. Use wkls.dependencies() instead of chaining."
            )

        query = """
            SELECT DISTINCT id, country, subtype, name_primary, name_en
            FROM wkls
            WHERE subtype = 'dependency'
        """
        return sedona.sql(query)

    def countries(self) -> sedonadb.dataframe.DataFrame:
        """Get all countries.

        Returns:
            DataFrame containing all country records with id, country,
            subtype, name_primary, and name_en columns.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        if self.chain:
            raise ValueError(
                "countries() can only be called on the root object. Use wkls.countries() instead of chaining."
            )

        query = """
            SELECT DISTINCT id, country, subtype, name_primary, name_en
            FROM wkls
            WHERE subtype = 'country'
        """
        return sedona.sql(query)

    def regions(self) -> sedonadb.dataframe.DataFrame:
        """Get regions for the current country.

        Must be called on a single-level chain (e.g., `wkls.us.regions()`).

        Returns:
            DataFrame containing all region records for the country.

        Raises:
            ValueError: If called at wrong chain level or country has no regions.
        """
        if not self.chain or len(self.chain) > 1:
            raise ValueError(
                "regions() requires exactly one level of chaining. Use wkls.country.regions() to get regions for a country."
            )
        if len(self.chain) == 1:
            country_iso = self.chain[0].upper()

            if self._has_region:
                query = """
                    SELECT * FROM wkls
                    WHERE country = '{country}'
                        AND subtype = 'region'
                """
                return sedona.sql(query.format(country=sqlescape(country_iso)))
            else:
                raise ValueError(
                    f"The country '{country_iso}' does not have regions in the dataset. Please directly call wkls.{str.lower(country_iso)}.counties() or wkls.{str.lower(country_iso)}.cities() to access its counties or cities."
                )

    def counties(self) -> sedonadb.dataframe.DataFrame:
        """Get counties for the current region.

        Must be called on a two-level chain (e.g., `wkls.us.ca.counties()`),
        or single-level for countries without regions.

        Returns:
            DataFrame containing all county records for the region.

        Raises:
            ValueError: If called at wrong chain level.
        """
        if not self.chain or len(self.chain) > 2:
            raise ValueError(
                "counties() requires exactly two levels of chaining. Use wkls.country.region.counties() to get counties for a region."
            )
        country_iso = self.chain[0].upper()
        if len(self.chain) == 1:
            if self._has_region:
                raise ValueError(
                    "counties() cannot be called on a country alone. Use wkls.country.region.counties() to get counties for a region."
                )
            else:
                query = """
                    SELECT * FROM wkls
                    WHERE country = '{country}'
                      AND subtype = 'county'
                """
                return sedona.sql(query.format(country=sqlescape(country_iso)))
        if len(self.chain) == 2:
            region_iso = country_iso + "-" + self.chain[1].upper()
            query = """
                SELECT * FROM wkls
                WHERE country = '{country}'
                  AND region = '{region}'
                  AND subtype = 'county'
            """
            return sedona.sql(
                query.format(
                    country=sqlescape(country_iso), region=sqlescape(region_iso)
                )
            )

    def cities(self) -> sedonadb.dataframe.DataFrame:
        """Get cities for the current region.

        Must be called on a two-level chain (e.g., `wkls.us.ca.cities()`),
        or single-level for countries without regions.

        Returns:
            DataFrame containing all city records for the region.

        Raises:
            ValueError: If called at wrong chain level.
        """
        if not self.chain:
            raise ValueError(
                "cities() requires exactly two levels of chaining. Use wkls.country.region.cities() to get cities for a region."
            )
        if len(self.chain) == 1:
            if self._has_region:
                raise ValueError(
                    "cities() cannot be called on a country alone. Use wkls.country.region.cities() to get cities for a region."
                )
            else:
                country_iso = self.chain[0].upper()
                query = """
                    SELECT * FROM wkls
                    WHERE country = '{country}'
                      AND subtype IN ('locality', 'localadmin')
                """
                return sedona.sql(query.format(country=sqlescape(country_iso)))

        if len(self.chain) == 3:
            raise ValueError(
                "cities() cannot be called on a specific city. Use wkls.country.region.cities() to get cities for a region."
            )
        if len(self.chain) > 3:
            raise ValueError(
                "cities() requires exactly two levels of chaining. Use wkls.country.region.cities() to get cities for a region."
            )
        if len(self.chain) == 2:
            country_iso = self.chain[0].upper()
            region_iso = country_iso + "-" + self.chain[1].upper()
            query = """
                SELECT * FROM wkls
                WHERE country = '{country}'
                  AND region = '{region}'
                  AND subtype IN ('locality', 'localadmin')
            """
            return sedona.sql(
                query.format(
                    country=sqlescape(country_iso), region=sqlescape(region_iso)
                )
            )

    def subtypes(self) -> sedonadb.dataframe.DataFrame:
        """Get all distinct division subtypes in the dataset.

        Returns:
            DataFrame containing all unique subtype values.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        if self.chain:
            raise ValueError(
                "subtypes() can only be called on the root object. Use wkls.subtypes() instead of chaining."
            )

        query = """SELECT DISTINCT subtype FROM wkls"""
        return sedona.sql(query)
