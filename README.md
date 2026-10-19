# Irish Geospatial Mapping

An end-to-end, CRS-aware mapping project for the 26 counties of the Republic of
Ireland: a documented **Jupyter notebook** for the analysis and an interactive
**Streamlit app** where you can explore the counties and **upload your own data**.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License: CC BY 4.0 data](https://img.shields.io/badge/data-CC%20BY%204.0-green)

## What it does

- **Notebook** (`07_irish_geospatial_mapping.ipynb`) downloads the official county
  boundaries, validates them, and works through the coordinate systems used in
  Ireland — **WGS 84 (EPSG:4326)**, **Irish Transverse Mercator (EPSG:2157)** and the
  legacy **Irish Grid (EPSG:29903)**:
  - areas computed three ways (degrees ✗, planar ITM ✓, geodesic ✓) and checked
    against the published values (national total within ~0.01% of the official figure);
  - a spatial check that a transformed Irish Grid point lands in the right county;
  - a demonstration that simply adding the +400 km / +500 km offset between Irish
    Grid and ITM is **~79 m wrong**;
  - an interactive Folium map and a validated export to
    `data/processed/counties_wgs84.geojson`.
- **App** (`app.py`) adds province filtering, a choropleth by area, basemap choice
  and **file upload**:

| Upload format | Requirements |
|---|---|
| CSV | `lat` + `lon` (WGS 84) **or** `easting` + `northing` (Irish Grid / ITM, chosen in the sidebar) |
| GeoJSON / JSON | any CRS |
| Zipped Shapefile (`.zip`) | `.shp`, `.shx`, `.dbf` (ideally `.prj`) |
| GeoPackage (`.gpkg`) | any CRS |

  Uploads are validated, reprojected to WGS 84, previewed, overlaid on the map and
  summarised per county — automatically, with no Run button. A sample CSV can be
  downloaded from the app's sidebar.

## Quickstart

```bash
git clone https://github.com/<your-username>/irish-geospatial-mapping.git
cd irish-geospatial-mapping
pip install -r requirements.txt

# 1) (optional) run the notebook to build the cleaned layer
jupyter lab 07_irish_geospatial_mapping.ipynb

# 2) launch the app (downloads the data itself if the notebook was not run)
streamlit run app.py
```

**No local setup?** Upload the notebook to [Google Colab](https://colab.research.google.com/)
and choose *Runtime → Run all*. (The Streamlit app needs a local machine.)

## Repository layout

```
├── 07_irish_geospatial_mapping.ipynb   # analysis, CRS deep-dive, export
├── app.py                              # Streamlit explorer + upload
├── requirements.txt
└── README.md
```

## Limitations

- Boundaries are generalised to 20 m by the publisher and simplified to 100 m for the
  web layer — suitable for visualisation, not for cadastral or engineering use.
- Areas describe the 26 *geographic* counties (Dublin is a single polygon).
- The Irish Grid ⇄ WGS 84 conversion has a stated accuracy of about 1 m.
- Uploads are limited to 25 MB / 100,000 rows.

## Data & attribution

Boundaries © Tailte Éireann / Ordnance Survey Ireland, used under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
([OSi Open Data](https://data-osi.opendata.arcgis.com/)).
Basemap tiles © Esri and its data providers, OpenStreetMap contributors.
Built with GeoPandas, pyproj, Folium and Streamlit.

> Add a `LICENSE` file for your own code (e.g. MIT) when you publish.
