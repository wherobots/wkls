"""Geometry mixin for the Wkl class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import _bootstrap

if TYPE_CHECKING:
    from .core import Wkl


class _GeometryMixin:
    """Mixin providing geometry retrieval methods for Wkl.

    Methods:
        _get_geom_expr(expr) — core geometry query (two-pass: id then name fallback)
        wkt() — WKT string
        wkb() — WKB bytes
        geojson() — GeoJSON string
    """

    def _get_geom_expr(self: Wkl, expr: str) -> Any:
        """Retrieve geometry using a SQL expression.

        Resolves the location chain against the local metadata table, then
        queries the remote Overture GeoParquet. Two separate queries beat
        one ``(id = X OR names.primary = Y)`` query because ``OR`` over a
        nested struct field defeats predicate pushdown in DataFusion /
        SedonaDB — the engine has to scan. Splitting gives the id path
        clean pushdown on a top-level unique column.

        Path 1 (almost always): ``WHERE … AND id = '<gers_id>'``. ``id``
        is globally unique, so this returns the single matching row.

        Path 2 (only if path 1 returns 0 rows — city-tier only): fallback
        to ``WHERE … AND names.primary = '<name>'``. Handles the rare
        case of GERS id drift across Overture releases. is_land stays in
        the filter here because a name like "San Francisco" can match
        both land and territorial-water rows; we want the land one.

        Args:
            expr: SQL expression to apply to the geometry column.

        Returns:
            Result of the geometry expression (type depends on expression).

        Raises:
            ValueError: If no results found or no geometry exists.
            AmbiguousLocationError: If the resolved DataFrame has >1 row.
            ConnectionError: If the remote Overture data can't be
                registered (first geometry call only; requires S3 access).
        """
        from . import core as _core
        from .core import AmbiguousLocationError, _build_error_hint

        _core._ensure_overture_loaded()
        df = self._resolve()
        row_count = df.count()
        if row_count == 0:
            # Chain-mode empty: fall back to the "Did you mean?" hint.
            if self.chain:
                suggestions = self._get_suggestions(self.chain[-1])
                hint = _build_error_hint(self.chain, suggestions)
                raise ValueError(hint.strip())
            raise ValueError("No rows to resolve into a geometry.")

        if row_count > 1:
            raise AmbiguousLocationError(self._ambiguity_message(df), candidates=self)

        row = df.head(1).to_arrow_table()
        gers_id = row.column("id")[0].as_py()
        country = row.column("country")[0].as_py()
        region = row.column("region")[0].as_py()
        subtype = row.column("subtype")[0].as_py()
        name_primary = row.column("name_primary")[0].as_py()

        base_conditions = [
            "country = $country",
            "subtype = $subtype",
            "is_land = true",
        ]
        base_params: dict[str, str] = {"country": country, "subtype": subtype}
        if region:
            base_conditions.append("region = $region")
            base_params["region"] = region

        def _fetch(extra_clause: str, extra_params: dict[str, str]) -> Any | None:
            clauses = " AND ".join(base_conditions + [extra_clause])
            tbl = _bootstrap.sedona.sql(
                f"SELECT {expr} FROM overture WHERE {clauses} LIMIT 1",
                params={**base_params, **extra_params},
            ).to_arrow_table()
            if tbl.num_rows == 0:
                return None
            return tbl.column(0)[0].as_py()

        # Path 1: id match (the common case).
        result = _fetch("id = $id", {"id": gers_id})
        if result is not None:
            return result

        # Path 2: id drifted — only city-tier subtypes use names.primary
        # as a secondary key. Country/region/dependency are unique by
        # country+region+subtype, so no fallback possible or needed there.
        if subtype in ("county", "locality", "localadmin"):
            result = _fetch(
                "names.primary = $name_primary", {"name_primary": name_primary}
            )
            if result is not None:
                return result

        chain_str = ".".join(self.chain)
        raise ValueError(
            f"No geometry found for: {chain_str} "
            f"(country={country}, region={region}, subtype={subtype}, "
            f"id={gers_id}, name={name_primary})"
        )

    def wkt(self: Wkl) -> str:
        """Get Well-Known Text (WKT) geometry for a single-row Wkl.

        Fetches from Overture on S3 (2–10 s, not cached). Output can run
        to more than 1 MB for a large state; check ``len()`` before
        printing. For many rows call ``to_arrow_table()`` once instead.

        Returns:
            WKT string representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
            AmbiguousLocationError: If the chain matches multiple rows.
        """
        return self._get_geom_expr("ST_AsText(geometry)")

    def wkb(self: Wkl) -> bytes:
        """Get Well-Known Binary (WKB) geometry for a single-row Wkl.

        Fetches from Overture on S3 (2–10 s, not cached). Output can run
        to more than 1 MB for a large state; check ``len()`` before
        printing. For many rows call ``to_arrow_table()`` once instead.

        Returns:
            Binary WKB representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
            AmbiguousLocationError: If the chain matches multiple rows.
        """
        return self._get_geom_expr("ST_AsWKB(geometry)")

    def geojson(self: Wkl) -> str:
        """Get GeoJSON geometry for a single-row Wkl.

        Fetches from Overture on S3 (2–10 s, not cached). Output can run
        to more than 1 MB for a large state; check ``len()`` before
        printing. For many rows call ``to_arrow_table()`` once instead.

        Returns:
            GeoJSON string representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
            AmbiguousLocationError: If the chain matches multiple rows.
        """
        return self._get_geom_expr("ST_AsGeoJSON(geometry)")
