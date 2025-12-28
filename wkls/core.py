import importlib.resources
from . import data
import os
import sedonadb
import sqlescapy
from typing import Callable


# Overture Maps dataset version
OVERTURE_VERSION = "2025-12-17.0"
OVERTURE_URI = f"s3://overturemaps-us-west-2/release/{OVERTURE_VERSION}/theme=divisions/type=division_area/"

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

def _log_and_query(exec_fn: Callable[str, sedonadb.dataframe.DataFrame], query: str) -> sedonadb.dataframe.DataFrame:
    if os.environ.get("WKLS_DEBUG", "false").lower() in ["true", "yes", "1"]:
        print(query)
    return exec_fn(query)


def _initialize_table():
    """Initialize the wkls table if it doesn't exist. Called once per module import."""

    sedona = sedonadb.connect()

    # Monkey-patch `.sql()` for debug mode.
    sedona_sql = sedona.sql
    sedona.sql = lambda q: _log_and_query(sedona_sql, q)

    sedona.sql(INITIALIZATION_QUERY)
    sedona.read_parquet(f"{importlib.resources.files(data)}/overture.zstd18.parquet").to_view("wkls")
    sedona.read_parquet(OVERTURE_URI, options={
        "aws.skip_signature": True,
        "aws.region": "us-west-2",
    }).to_view("overture")
    return sedona

# Initialize the table when the module is imported
sedona = _initialize_table()


def sqlescape(v: str) -> str:
    # SQL escape, but maintain the use of % for the LIKE operator.
    return sqlescapy.sqlescape(v).replace("\\%", "%")


class ChainableDataFrame(sedonadb.dataframe.DataFrame):
    """A DataFrame that maintains chaining capability for the wkls library."""

    _metadata = ["_chain"]

    def __init__(self, df, chain=None):
        super().__init__(df._ctx, df._impl, df._options)
        object.__setattr__(self, "_chain", chain or [])

    def __getattr__(self, attr):
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

    def __getitem__(self, key):
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
        """Get WKT geometry for the first result."""
        wkl = Wkl(self._chain)
        return wkl.wkt()

    def wkb(self) -> bytes:
        """Get WKB geometry for the first result."""
        wkl = Wkl(self._chain)
        return wkl.wkb()

    def hexwkb(self) -> str:
        """Get HEX WKB geometry for the first result."""
        wkl = Wkl(self._chain)
        return wkl.hexwkb()

    def geojson(self) -> str:
        """Get GeoJSON geometry for the first result."""
        wkl = Wkl(self._chain)
        return wkl.geojson()

    def svg(self) -> str:
        """Get SVG geometry for the first result."""
        wkl = Wkl(self._chain)
        return wkl.svg()

    def dependencies(self):
        """Get all dependencies."""
        wkl = Wkl(self._chain)
        return wkl.dependencies()

    def countries(self):
        """Get all countries."""
        wkl = Wkl(self._chain)
        return wkl.countries()

    def regions(self):
        """Get regions for the current chain."""
        wkl = Wkl(self._chain)
        return wkl.regions()

    def counties(self):
        """Get counties for the current chain."""
        wkl = Wkl(self._chain)
        return wkl.counties()

    def cities(self):
        """Get cities for the current chain."""
        wkl = Wkl(self._chain)
        return wkl.cities()

    def subtypes(self):
        """Get all subtypes."""
        wkl = Wkl(self._chain)
        return wkl.subtypes()

    @property
    def _constructor(self):
        return ChainableDataFrame


class Wkl:
    _has_region = True

    def __init__(self, chain=None):
        if chain and len(chain) >= 1:
            country_iso = chain[0].upper()
            df_check = sedona.sql(
                COUNTRY_REGION_CHECK_QUERY.format(country=sqlescape(country_iso))
            )
            # If the query returns any rows, it means it has no regions
            self._has_region = df_check.count() == 0
        self.chain = chain or []

    def overture_version(self):
        """Return the version of the Overture Maps dataset being used.

        This method is only available at the root level (wkls.overture_version()),
        not on chained objects.
        """
        if self.chain:
            raise ValueError(
                "overture_version() is only available at the root level. Use wkls.overture_version(), not wkls.us.overture_version()."
            )
        return OVERTURE_VERSION

    def __getattr__(self, attr):
        new_wkl = Wkl(self.chain + [attr.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3:
            raise ValueError("Too many chained attributes (max = 3)")

        if len(new_wkl.chain) <= 3:
            df = new_wkl.resolve()
            return ChainableDataFrame(df, new_wkl.chain)
        return new_wkl

    def __getitem__(self, key):
        new_wkl = Wkl(self.chain + [key.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3 and "%" not in key:
            raise ValueError("Too many chained attributes (max = 3)")
        # If this looks like a search pattern (contains %), return DataFrame directly
        if "%" in key:
            return new_wkl.resolve()
        return new_wkl

    def __repr__(self):
        return repr(self.resolve())

    def resolve(self):
        if not self.chain:
            raise ValueError(
                "No attributes in the chain. Use wkls.country or wkls.country.region, etc."
            )

        params = {}

        if len(self.chain) > 0:
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

        return sedona.sql(query.format(
            **{k: sqlescape(v) for k, v in params.items()}
        ))

    def _get_geom_expr(self, expr: str):
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
        return self._get_geom_expr("ST_AsText(geometry)")

    def wkb(self) -> bytes:
        return self._get_geom_expr("ST_AsWKB(geometry)")

    def hexwkb(self) -> str:
        # return self._get_geom_expr("ST_AsHEXWKB(geometry)")
        raise NotImplementedError("ST_AsHEXWKB() isn't implemented yet")

    def geojson(self) -> str:
        # return self._get_geom_expr("ST_AsGeoJSON(geometry)")
        raise NotImplementedError("ST_AsGeoJSON() isn't implemented yet")

    def svg(self, relative: bool = False, precision: int = 15) -> str:
        # return self._get_geom_expr(
        #     f"ST_AsSVG(geometry, {str(relative).lower()}, {precision})"
        # )
        raise NotImplementedError("ST_AsSVG() isn't implemented yet")

    def dependencies(self):
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

    def countries(self):
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

    def regions(self):
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

    def counties(self):
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
            return sedona.sql(query.format(
                country=sqlescape(country_iso),
                region=sqlescape(region_iso)
            ))

    def cities(self):
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
            return sedona.sql(query.format(
                country=sqlescape(country_iso),
                region=sqlescape(region_iso)
            ))

    def subtypes(self):
        if self.chain:
            raise ValueError(
                "subtypes() can only be called on the root object. Use wkls.subtypes() instead of chaining."
            )

        query = """SELECT DISTINCT subtype FROM wkls"""
        return sedona.sql(query)
