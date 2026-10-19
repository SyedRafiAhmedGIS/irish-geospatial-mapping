"""Irish Geospatial Mapping — Interactive Explorer (Project 07)
================================================================

Streamlit front-end for the Irish geospatial portfolio project. It consumes the
GeoJSON artifact produced by the companion notebook
(`07_irish_geospatial_mapping.ipynb` -> `data/processed/counties_wgs84.geojson`)
and adds interactive filtering, choropleth styling and basemap choice. Users can
also upload their own data (GeoJSON, zipped Shapefile, GeoPackage, or a CSV with
lat/lon or easting/northing columns); it is validated, reprojected to WGS 84,
previewed, overlaid on the map and summarised per county automatically.

If the notebook has not been run yet, the app downloads the same official
open-data layer directly (Tailte Éireann Feature Service) so it can run
standalone. County names and areas are normalised in both paths.

Run
---
    pip install "streamlit>=1.46" streamlit-folium "geopandas>=1.0" "folium>=0.16" requests
    streamlit run app.py

Limitations
-----------
Geometry is generalised (20 m publisher, 100 m web layer) — storytelling-grade,
not cadastral. Areas describe the 26 geographic counties; Dublin is one polygon.

Attribution
-----------
Boundaries © Tailte Éireann / Ordnance Survey Ireland (CC BY 4.0).
Basemap tiles © Esri and its data providers, OpenStreetMap contributors.
"""

from __future__ import annotations

import bisect
import io
import pathlib

import folium
import geopandas as gpd
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOCAL_DATA = pathlib.Path("data/processed/counties_wgs84.geojson")   # notebook export
RAW_CACHE = pathlib.Path("data/raw/counties_raw.geojson")            # standalone download

FEATURE_SERVICE = (
    "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services/"
    "Counties___OSi_National_Statutory_Boundaries___Generalised_20m/FeatureServer/0"
)
QUERY_URL = f"{FEATURE_SERVICE}/query?where=1%3D1&outFields=*&f=geojson"

ATTRIBUTION = "Boundaries © Tailte Éireann / OSi, CC BY 4.0"
CRS_ITM = "EPSG:2157"

# --- user uploads ---------------------------------------------------------
UPLOAD_TYPES = ["geojson", "json", "zip", "gpkg", "csv"]
MAX_UPLOAD_MB = 25
MAX_ROWS = 100_000          # hard cap on features/rows analysed
MAX_MAP_FEATURES = 5_000    # more than this is sampled on the map only
IRELAND_BOX = (-11.0, 51.0, -5.0, 56.0)   # lon_min, lat_min, lon_max, lat_max
LAT_NAMES = ("lat", "latitude", "y_wgs84")
LON_NAMES = ("lon", "lng", "long", "longitude", "x_wgs84")
EAST_NAMES = ("easting", "east", "eastings", "x")
NORTH_NAMES = ("northing", "north", "northings", "y")
GRID_CHOICES = {
    "EPSG:2157": "Irish Transverse Mercator (ITM, 2157)",
    "EPSG:29903": "Irish Grid (legacy, 29903)",
}
SAMPLE_CSV = (
    "name,easting,northing\nDublin city centre,715827,734698\nCork city,567269,571923\nGalway city,529508,725006\nLimerick city,557607,657142\nBelfast (outside RoI),733751,874084\n"
)
MAP_CENTRE: tuple[float, float] = (53.4, -8.2)
IRISH_GREEN = "#169B62"
BORDER = "#0B5D3B"
ORANGE = "#FF883E"
FILL_RAMP = ["#E9F5EE", "#C4DFCF", "#93C6A6", "#5CA97F", "#2E8B60", "#12603F"]

# Esri first: no API key and no 403s. OpenStreetMap's public tile servers
# block some hosts; CARTO's now ask for a key.
BASEMAPS = {
    "Esri.WorldGrayCanvas": "Esri light grey (clean)",
    "Esri.WorldStreetMap": "Esri streets",
    "Esri.WorldImagery": "Esri World Imagery (satellite)",
    "OpenStreetMap": "OpenStreetMap (may be blocked)",
}

CRS_CHEATSHEET = """
| EPSG | Name | Units | Use it for |
|---|---|---|---|
| **4326** | WGS 84 | degrees | Storage & exchange — GeoJSON (RFC 7946), GPS, web maps |
| **2157** | Irish Transverse Mercator | metres | Measurement — areas, lengths, engineering |
| **29903** | Irish Grid (1965) | metres | Legacy datasets still in circulation |
"""


# ---------------------------------------------------------------------------
# Data loading (with a notebook-independent fallback)
# ---------------------------------------------------------------------------
def resolve_source() -> str | None:
    """Return a data path the app can read, downloading open data if needed.

    Preference order: the notebook's cleaned export first, then a fresh
    download from the Tailte Éireann Feature Service. Returns ``None`` (after
    showing an error) when neither path is available.
    """
    if LOCAL_DATA.exists():
        return str(LOCAL_DATA)
    if RAW_CACHE.exists() and RAW_CACHE.stat().st_size > 0:
        return str(RAW_CACHE)

    try:
        response = requests.get(
            QUERY_URL, timeout=120, headers={"User-Agent": "portfolio-project/07"}
        )
        response.raise_for_status()
        payload = response.json()  # ArcGIS can answer HTTP 200 with an error body
        if payload.get("type") != "FeatureCollection" or not payload.get("features"):
            raise ValueError(f"unexpected service response: {str(payload)[:120]}")
        RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RAW_CACHE.write_bytes(response.content)
        return str(RAW_CACHE)
    except (requests.RequestException, ValueError) as exc:
        st.error(
            "Could not load county boundaries. Run the notebook first to "
            "produce `data/processed/counties_wgs84.geojson`, or check your "
            f"connection. ({exc})"
        )
        return None


def _tidy(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Normalise either the notebook export or the raw service schema."""
    if "county_name" not in gdf.columns:
        gdf = gdf.rename(
            columns={"ENGLISH": "county_name", "GAEILGE": "contae", "PROVINCE": "province"}
        )
        # Same method as the notebook: planar area in a metre-based CRS (ITM).
        gdf["area_km2"] = gdf.to_crs(CRS_ITM).geometry.area / 1e6
        gdf["county_name"] = gdf["county_name"].str.title().str.strip()
    columns = [c for c in ("county_name", "contae", "province", "area_km2") if c in gdf.columns]
    return (
        gdf[columns + ["geometry"]]
        .sort_values("county_name")
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner="Loading county boundaries ...")
def load_counties(path: str) -> gpd.GeoDataFrame:
    """Read and normalise the boundary layer (cached across re-runs)."""
    return _tidy(gpd.read_file(path))


# ---------------------------------------------------------------------------
# User uploads: parse -> validate -> reproject to WGS 84 (all automatic)
# ---------------------------------------------------------------------------
class UploadError(ValueError):
    """A problem with the uploaded file that the user can fix."""


def _first_match(columns: list[str], names: tuple[str, ...]) -> str | None:
    lowered = {c.lower().strip(): c for c in columns}
    return next((lowered[n] for n in names if n in lowered), None)


def _csv_to_points(df: pd.DataFrame, grid_crs: str) -> tuple[gpd.GeoDataFrame, str]:
    """Turn a CSV into points; returns (gdf in WGS 84, description of the source CRS)."""
    cols = list(df.columns)
    lat, lon = _first_match(cols, LAT_NAMES), _first_match(cols, LON_NAMES)
    if lat and lon:
        x_col, y_col, src_crs = lon, lat, "EPSG:4326"
    else:
        x_col, y_col = _first_match(cols, EAST_NAMES), _first_match(cols, NORTH_NAMES)
        if not (x_col and y_col):
            raise UploadError(
                "CSV needs coordinate columns: `lat` + `lon` (WGS 84), or "
                "`easting` + `northing` (Irish Grid / ITM)."
            )
        src_crs = grid_crs
    xy = df[[x_col, y_col]].apply(pd.to_numeric, errors="coerce")
    ok = xy.notna().all(axis=1)
    if not ok.any():
        raise UploadError(f"Columns `{x_col}`/`{y_col}` contain no numeric coordinates.")
    df = df.loc[ok].copy()
    geom = gpd.points_from_xy(xy.loc[ok, x_col], xy.loc[ok, y_col])
    gdf = gpd.GeoDataFrame(df, geometry=geom, crs=src_crs).to_crs("EPSG:4326")
    return gdf, src_crs


@st.cache_data(show_spinner="Reading your file ...", max_entries=8)
def parse_upload(name: str, data: bytes, grid_crs: str) -> tuple[gpd.GeoDataFrame, dict]:
    """Parse an uploaded file into a clean WGS 84 GeoDataFrame plus a facts dict."""
    if len(data) > MAX_UPLOAD_MB * 1_048_576:
        raise UploadError(f"File is larger than {MAX_UPLOAD_MB} MB.")
    ext = name.lower().rsplit(".", 1)[-1]
    notes: list[str] = []

    try:
        if ext == "csv":
            df = pd.read_csv(io.BytesIO(data), nrows=MAX_ROWS + 1)
            gdf, src_crs = _csv_to_points(df, grid_crs)
        else:
            gdf = gpd.read_file(io.BytesIO(data))
            if gdf.crs is None:
                xmin, ymin, xmax, ymax = gdf.total_bounds
                if max(abs(xmin), abs(xmax)) <= 180 and max(abs(ymin), abs(ymax)) <= 90:
                    gdf = gdf.set_crs("EPSG:4326")
                    notes.append("No CRS declared; coordinates look like degrees, so WGS 84 was assumed.")
                else:
                    raise UploadError(
                        "No CRS declared and the coordinates are not degrees. "
                        "Add a CRS (e.g. include the .prj in a zipped shapefile)."
                    )
            src_crs = gdf.crs.to_string()
            gdf = gdf.to_crs("EPSG:4326")
    except UploadError:
        raise
    except Exception as exc:  # unreadable / corrupt / unsupported content
        raise UploadError(
            f"Could not read this file as .{ext} ({type(exc).__name__}). Check the format; "
            "a zipped Shapefile must contain the .shp, .shx and .dbf (and ideally .prj) files."
        ) from exc

    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
    if gdf.empty:
        raise UploadError("The file has no usable geometries.")
    if len(gdf) > MAX_ROWS:
        gdf = gdf.head(MAX_ROWS)
        notes.append(f"Only the first {MAX_ROWS:,} features were used.")

    xmin, ymin, xmax, ymax = gdf.total_bounds
    in_ireland = (
        xmax >= IRELAND_BOX[0] and xmin <= IRELAND_BOX[2]
        and ymax >= IRELAND_BOX[1] and ymin <= IRELAND_BOX[3]
    )
    if not in_ireland:
        notes.append(
            "Nothing falls over Ireland. If you expected it to, check the CRS "
            "(or the CSV's grid choice in the sidebar)."
        )

    # JSON-safe attributes for tooltips (dates, objects -> text)
    for col in gdf.columns.drop("geometry"):
        if not pd.api.types.is_numeric_dtype(gdf[col]) and not pd.api.types.is_bool_dtype(gdf[col]):
            gdf[col] = gdf[col].astype(str)

    facts = {
        "name": name,
        "size_kb": len(data) / 1024,
        "features": len(gdf),
        "geometry_types": ", ".join(sorted(gdf.geom_type.unique())),
        "source_crs": src_crs,
        "notes": notes,
        "is_points": bool((gdf.geom_type == "Point").all()),
    }
    return gdf, facts


def summarise_by_county(up: gpd.GeoDataFrame, counties: gpd.GeoDataFrame) -> pd.DataFrame:
    """Count uploaded features per county (points: within; others: intersects)."""
    predicate = "within" if (up.geom_type == "Point").all() else "intersects"
    joined = gpd.sjoin(
        up[["geometry"]], counties[["county_name", "geometry"]], how="left", predicate=predicate
    )
    counts = joined.groupby(joined["county_name"].fillna("Outside the 26 counties")).size()
    return counts.rename("Features").sort_values(ascending=False).rename_axis("County").reset_index()


# ---------------------------------------------------------------------------
# Map helpers
# ---------------------------------------------------------------------------
def colour_for_area(area: float, cuts: list[float]) -> str:
    """Map an area onto the green ramp using quantile cut points."""
    return FILL_RAMP[bisect.bisect_left(cuts, area)]


def build_map(
    gdf: gpd.GeoDataFrame,
    tiles: str,
    choropleth: bool,
    cuts: list[float],
    upload: gpd.GeoDataFrame | None = None,
) -> folium.Map:
    """Build the folium map with a styled, hoverable county layer."""
    fmap = folium.Map(location=MAP_CENTRE, zoom_start=7, tiles=tiles, control_scale=True)

    def style_function(feature: dict) -> dict:
        fill = (
            colour_for_area(feature["properties"]["area_km2"], cuts)
            if choropleth
            else IRISH_GREEN
        )
        return {"fillColor": fill, "fillOpacity": 0.65, "color": BORDER, "weight": 1}

    folium.GeoJson(
        gdf,
        name="Counties",
        style_function=style_function,
        highlight_function=lambda f: {"fillOpacity": 0.85, "weight": 2},
        tooltip=folium.GeoJsonTooltip(
            fields=["county_name", "contae", "province", "area_km2"],
            aliases=["County", "Gaeilge", "Province", "Area (km²)"],
            localize=True,
            sticky=False,
        ),
    ).add_to(fmap)

    if upload is not None:
        shown = upload.sample(MAX_MAP_FEATURES, random_state=0) if len(upload) > MAX_MAP_FEATURES else upload
        fields = [c for c in shown.columns if c != "geometry"][:6]
        folium.GeoJson(
            shown,
            name="Your upload",
            marker=folium.CircleMarker(radius=5, color="#B33F00", weight=1,
                                       fill=True, fill_color=ORANGE, fill_opacity=0.9),
            style_function=lambda f: {"color": "#B33F00", "weight": 2,
                                      "fillColor": ORANGE, "fillOpacity": 0.35},
            tooltip=folium.GeoJsonTooltip(fields=fields, sticky=False) if fields else None,
        ).add_to(fmap)
        xmin, ymin, xmax, ymax = upload.total_bounds
        if IRELAND_BOX[0] <= xmin and xmax <= IRELAND_BOX[2] and IRELAND_BOX[1] <= ymin and ymax <= IRELAND_BOX[3]:
            fmap.fit_bounds([[ymin, xmin], [ymax, xmax]], max_zoom=11)

    folium.LayerControl(collapsed=True).add_to(fmap)
    return fmap


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Irish Geospatial Explorer", layout="wide")
    st.title("Irish Geospatial Explorer")
    st.caption(
        "Project 07 — county boundaries of Ireland, mapped CRS-aware. "
        f"{ATTRIBUTION}."
    )

    source = resolve_source()
    if source is None:
        return
    counties = load_counties(source)

    # -- sidebar ------------------------------------------------------------
    with st.sidebar:
        st.header("Controls")
        tiles = st.selectbox("Basemap", list(BASEMAPS), format_func=BASEMAPS.get)
        choropleth = st.toggle("Colour counties by area", value=True)
        provinces = st.multiselect(
            "Provinces",
            sorted(counties["province"].unique()),
            default=sorted(counties["province"].unique()),
        )
        st.divider()
        st.subheader("Add your own data")
        uploaded = st.file_uploader(
            "Upload a file — it is mapped automatically",
            type=UPLOAD_TYPES,
            help="GeoJSON/JSON, zipped Shapefile (.zip), GeoPackage (.gpkg), or a CSV "
                 "with lat/lon or easting/northing columns. Max 25 MB.",
        )
        grid_crs = st.selectbox(
            "CSV easting/northing grid",
            list(GRID_CHOICES),
            format_func=GRID_CHOICES.get,
            help="Only used for CSVs with easting/northing columns.",
        )
        st.download_button("Download a sample CSV (ITM easting/northing)", SAMPLE_CSV, "sample_itm_points.csv", "text/csv")
        with st.expander("CRS cheat-sheet"):
            st.markdown(CRS_CHEATSHEET)
        with st.expander("Data source & licence"):
            st.markdown(
                f"**Dataset:** Counties — National Statutory Boundaries — 2019 "
                f"(Generalised 20 m)  \n"
                f"**Publisher:** Tailte Éireann / Ordnance Survey Ireland  \n"
                f"**Licence:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)  \n"
                f"**Portal:** [OSi Open Data](https://data-osi.opendata.arcgis.com/)"
            )
            st.caption("Regenerate the cleaned layer by running the notebook.")

    # -- filtering ------------------------------------------------------------
    view = counties[counties["province"].isin(provinces)] if provinces else counties
    if view.empty:
        st.info("Select at least one province in the sidebar.")
        return

    # -- headline metrics -------------------------------------------------------
    largest = view.loc[view["area_km2"].idxmax(), "county_name"]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Counties shown", f"{len(view)}")
    col2.metric("Total area", f"{view['area_km2'].sum():,.0f} km²")
    col3.metric("Largest county", largest)
    col4.metric("Map CRS", "EPSG:4326")

    # -- user upload (runs automatically on upload) ---------------------------------
    upload_gdf, upload_facts = None, None
    if uploaded is not None:
        try:
            upload_gdf, upload_facts = parse_upload(uploaded.name, uploaded.getvalue(), grid_crs)
        except UploadError as exc:
            st.error(str(exc))

    # -- interactive map ----------------------------------------------------------
    cuts = view["area_km2"].quantile([1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6]).tolist() if choropleth else []
    fmap = build_map(view, tiles, choropleth, cuts, upload_gdf)
    result = st_folium(
        fmap, height=620, use_container_width=True, returned_objects=["last_active_drawing"]
    )

    drawn = (result or {}).get("last_active_drawing") or {}
    props = drawn.get("properties") or {}
    if props.get("county_name"):
        st.success(
            f"**{props.get('county_name', '—')}** · *{props.get('contae', '')}* · "
            f"{props.get('province', '')} · {props.get('area_km2', 0):,.0f} km²"
        )

    # -- upload preview & analysis ------------------------------------------------------
    if upload_gdf is not None:
        st.subheader(f"Your upload — {upload_facts['name']}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Features", f"{upload_facts['features']:,}")
        m2.metric("Geometry", upload_facts["geometry_types"])
        m3.metric("Source CRS", upload_facts["source_crs"])
        m4.metric("Size", f"{upload_facts['size_kb']:,.1f} KB")
        for note in upload_facts["notes"]:
            st.warning(note)
        if len(upload_gdf) > MAX_MAP_FEATURES:
            st.info(f"Map shows a random sample of {MAX_MAP_FEATURES:,} features; tables use all.")

        tab_table, tab_county = st.tabs(["Preview table", "Per-county summary"])
        with tab_table:
            st.caption("First 100 rows (geometry shown as WKT, reprojected to WGS 84).")
            head = upload_gdf.head(100)
            preview = pd.DataFrame(head.drop(columns="geometry"))
            preview["geometry (WKT)"] = head.geometry.to_wkt().str.slice(0, 60)
            st.dataframe(preview, width="stretch", height=300, hide_index=True)
        with tab_county:
            summary = summarise_by_county(upload_gdf, counties)
            st.dataframe(summary, width="stretch", hide_index=True)
            if len(summary) > 1:
                st.bar_chart(summary.set_index("County")["Features"], horizontal=True, color=ORANGE)

    # -- supporting views ------------------------------------------------------------
    left, right = st.columns((3, 2), gap="large")
    with left:
        st.subheader("Ten largest counties in view")
        top10 = view.nlargest(10, "area_km2").set_index("county_name")["area_km2"]
        st.bar_chart(top10, horizontal=True, color="#169B62")
    with right:
        st.subheader("Boundary data")
        st.dataframe(
            view.drop(columns="geometry")
            .assign(area_km2=lambda df: df["area_km2"].round(1))
            .rename(columns={"county_name": "County", "contae": "Gaeilge",
                             "province": "Province", "area_km2": "Area (km²)"}),
            width="stretch",
            height=420,
            hide_index=True,
        )

    st.divider()
    st.caption(
        f"{ATTRIBUTION} · Basemap tiles © Esri and its data providers, OpenStreetMap contributors · "
        "Built with Streamlit, GeoPandas, pyproj and Folium."
    )


if __name__ == "__main__":
    main()
