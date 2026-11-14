"""Prepare radar, temperature and NOAA bloom detections over Lake Erie."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform_bounds, transform_geom
from rasterio.windows import from_bounds

from .prep import _measurement_valid, _text_scalar, _write_cog

LAKE_BOUNDS = [-83.6, 41.25, -78.7, 43.2]


def _inside(geometry: dict, shape: tuple, affine: Affine, crs: CRS) -> np.ndarray:
    return geometry_mask(
        [transform_geom("EPSG:4326", crs, geometry)],
        out_shape=shape,
        transform=affine,
        invert=True,
    )


def _publish(
    data: np.ndarray,
    affine: Affine,
    crs: CRS,
    geometry: dict,
    out_dir: Path,
    layer_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    inside = _inside(geometry, data.shape, affine, crs)
    valid = inside & np.isfinite(data)
    if not valid.any():
        raise ValueError(f"{layer_id}: source contains no usable measurements over Lake Erie")
    data = np.where(valid, data, np.nan).astype(np.float32)
    path = out_dir / f"{layer_id}.tif"
    if not _write_cog(data, affine, crs, path, nodata=np.nan):
        raise ValueError(f"{layer_id}: generated raster failed COG validation")
    valid_pixels, lake_pixels = int(valid.sum()), int(inside.sum())
    rows, cols = np.nonzero(valid)
    xmin, ymax = affine @ (int(cols.min()), int(rows.min()))
    xmax, ymin = affine @ (int(cols.max()) + 1, int(rows.max()) + 1)
    valid_bounds = list(transform_bounds(crs, "EPSG:4326", xmin, ymin, xmax, ymax))
    return {
        **config,
        "path": path.name,
        "type": "single",
        "crs": crs.to_string(),
        "spacing": [abs(affine.a), abs(affine.e)],
        "spacing_unit": "degrees" if crs.is_geographic else "m",
        "bounds_wgs84": list(
            transform_bounds(crs, "EPSG:4326", *array_bounds(*data.shape, affine))
        ),
        "usable_pixels": valid_pixels,
        "lake_pixels": lake_pixels,
        "coverage_percent": round(valid_pixels / lake_pixels * 100, 2),
        "valid_bounds_wgs84": valid_bounds,
        "coverage_method": "Valid native pixel centers / all Lake Erie pixel centers on this layer's grid",
        "value_range": [float(data[valid].min()), float(data[valid].max())],
    }


def prepare_radar(source: Path, geometry: dict, out_dir: Path) -> dict[str, Any]:
    """Read the actual sigma0HH radar input carried in SME2, not soil moisture."""
    with h5py.File(source) as f:
        g = f["science/LSAR/SME2/grids/radarData/frequencyA"]
        ds = g["sigma0HH"]
        x, y = g["xCoordinates"][:], g["yCoordinates"][:]
        dx, dy = float(g["xCoordinateSpacing"][()]), float(g["yCoordinateSpacing"][()])
        crs = CRS.from_epsg(int(g["projection"].attrs["epsg_code"]))
        native = Affine(dx, 0, x[0] - dx / 2, 0, dy, y[0] - dy / 2)
        w = from_bounds(*transform_bounds("EPSG:4326", crs, *LAKE_BOUNDS), native)
        c0, r0 = int(np.floor(w.col_off)), int(np.floor(w.row_off))
        c1, r1 = int(np.ceil(w.col_off + w.width)), int(np.ceil(w.row_off + w.height))
        affine = native @ Affine.translation(c0, r0)
        data = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
        sr0, sr1, sc0, sc1 = max(0, r0), min(len(y), r1), max(0, c0), min(len(x), c1)
        raw = ds[sr0:sr1, sc0:sc1]
        valid = _measurement_valid(ds, raw) & (raw > 0)
        cropped = np.full(raw.shape, np.nan, dtype=np.float32)
        cropped[valid] = 10 * np.log10(raw[valid])
        data[sr0 - r0 : sr1 - r0, sc0 - c0 : sc1 - c0] = cropped
        ident = f["science/LSAR/identification"]
        return _publish(
            data,
            affine,
            crs,
            geometry,
            out_dir,
            "radar",
            {
                "label": "Radar imagery",
                "unit": "dB",
                "rescale": [-25, -14],
                "legend_note": "Display contrast: −25 to −14 dB. Values outside this range use the end colours.",
                "colormap": "gray",
                "start": _text_scalar(ident, "zeroDopplerStartTime"),
                "stop": _text_scalar(ident, "zeroDopplerEndTime"),
                "period": "Single acquisition",
                "source": "NASA NISAR · HH radar backscatter (σ⁰)",
                "source_file": source.name,
                "source_variable": ds.name,
                "source_url": "https://nisar-docs.asf.alaska.edu/sme2/",
                "status": "PROVISIONAL",
                "resolution_label": f"{dx:.2f} m native grid",
                "description": "Radar brightness over the lake, sensitive to surface roughness. Western-lake coverage; transparent areas are outside this observation. This is the 200 m radar input stored in the SME2 file, not the soil-moisture layer.",
            },
        )


def prepare_netcdf(
    source: Path,
    geometry: dict,
    out_dir: Path,
    layer_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Read a CF latitude/longitude subset using its coordinate-derived transform."""
    with rasterio.open(source) as src:
        tags = src.tags()
        variable = config["source_variable"]
        expected_units = config["source_units"]
        if tags.get(f"{variable}#units") not in expected_units:
            raise ValueError(f"{layer_id}: unexpected or missing source units")
        if (
            tags.get("latitude#units") != "degrees_north"
            or tags.get("longitude#units") != "degrees_east"
        ):
            raise ValueError(f"{layer_id}: expected a CF latitude/longitude grid")
        data = src.read(1, masked=True).filled(np.nan).astype(np.float32)
        if layer_id == "chlorophyll":
            data[data <= 0] = np.nan
        start = tags.get("NC_GLOBAL#time_coverage_start")
        stop = tags.get("NC_GLOBAL#time_coverage_end")
        if not start or not stop:
            raise ValueError(f"{layer_id}: source does not identify its observation time")
        return _publish(
            data,
            src.transform,
            CRS.from_epsg(4326),
            geometry,
            out_dir,
            layer_id,
            {
                **config,
                "source_file": source.name,
                "start": start,
                "stop": stop,
                "resolution_label": f"{abs(src.transform.a):.5f}° native grid",
            },
        )


def prepare_lake(
    radar_path: Path,
    temperature_path: Path,
    chlorophyll_path: Path,
    outline_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    feature = json.loads(outline_path.read_text())
    geometry = feature["geometry"]
    out_dir.mkdir(parents=True, exist_ok=True)
    radar = prepare_radar(radar_path, geometry, out_dir)
    temperature = prepare_netcdf(
        temperature_path,
        geometry,
        out_dir,
        "temperature",
        {
            "label": "Water temperature",
            "unit": "°C",
            "rescale": [0, 30],
            "colormap": "turbo",
            "source": "NOAA CoastWatch · ACSPO GLSEA",
            "source_variable": "sst",
            "source_units": ["degree_C"],
            "source_url": "https://apps.glerl.noaa.gov/erddap/info/GLSEA_ACSPO_GCS/index.html",
            "period": "Daily surface-temperature analysis",
            "status": "Satellite-derived analysis",
            "description": "Lake surface temperature, not temperature at depth. GLSEA combines satellite observations and fills gaps using nearby dates and spatial smoothing.",
        },
    )
    blooms = prepare_blooms(chlorophyll_path, geometry, out_dir)
    metadata = {
        "product": "LAKE_ERIE",
        "scene": "Lake Erie · satellite observations",
        "bounds_wgs84": LAKE_BOUNDS,
        "overview_bbox": LAKE_BOUNDS,
        "default_layer": "blooms",
        "lake_outline": feature,
        "coverage_note": "Switch layers to explore Lake Erie. Each layer has its own date and coverage. Missing data is transparent.",
        "layers": {"radar": radar, "temperature": temperature, "blooms": blooms},
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def prepare_blooms(source: Path, geometry: dict, out_dir: Path) -> dict[str, Any]:
    """Use NOAA's detection/quality flags, never a chlorophyll cutoff.

    DN 0 is no detection; 1..250 are bloom signal; 251..255 are invalid.
    The reverse scaling and colours are supplied by the source GeoTIFF itself.
    """
    with rasterio.open(source) as src:
        tags = src.tags()
        if tags.get("SAPS_product_name") != "CI-CIcyano":
            raise ValueError(
                "Bloom layer requires a NOAA CI-CIcyano product, not background chlorophyll"
            )
        expected_flags = {
            "nodetect": 0,
            "saturated": 250,
            "ci_adj": 251,
            "land": 252,
            "cloud": 253,
            "mixed_pixel": 254,
            "nodata": 255,
        }
        for name, code in expected_flags.items():
            if tags.get(f"SAPS_product_flag_{name}") != str(code):
                raise ValueError(f"Unexpected NOAA bloom flag: {name}")
        if tags.get("SAPS_product_rev_scaling") != "10**(3.0 / 250.0 * DN - 4.2)":
            raise ValueError("Unsupported NOAA bloom-index scaling")
        source_name = tags.get("SAPS_product_src", "")
        stamp = re.search(r"sentinel-3\.(\d{7})\.(\d{4})\.(\d{4})(?:_(\d{4}))?C", source_name)
        if not stamp:
            raise ValueError("NOAA bloom source does not identify its observation date/time")
        day = datetime.strptime(stamp[1], "%Y%j").strftime("%Y-%m-%d")

        def timestamp(value):
            return f"{day}T{value[:2]}:{value[2:]}:00Z"

        raw = src.read(1)
        inside = _inside(geometry, raw.shape, src.transform, src.crs)
        detected = inside & (raw > 0) & (raw <= 250)
        observed = inside & (raw <= 250)
        physical = np.full(raw.shape, np.nan, dtype=np.float32)
        physical[detected] = np.power(10.0, 3.0 / 250.0 * raw[detected].astype(float) - 4.2)
        palette = src.colormap(1)
        source_file = source.name
        source_url = (
            "https://nccospublicstor.blob.core.windows.net/hab-data/data/web/olci_western_le/tif_archive/"
            + source_name.split(",")[0].split("/")[-1].replace(".CI.tif", ".CI-CIcyano.tif")
        )
        layer = _publish(
            physical,
            src.transform,
            src.crs,
            geometry,
            out_dir,
            "blooms",
            {
                "label": "Algae blooms",
                "unit": "CI",
                "rescale": [10 ** (-4.2 + 3 / 250), 10 ** (-1.2)],
                "render_encoding": "noaa_ci",
                "colormap": None,
                "colormap_entries": {str(i): list(palette[i]) for i in range(256)},
                "legend_labels": ["Low signal", "Medium", "High signal"],
                "legend_gradient": "linear-gradient(90deg, "
                + ", ".join(
                    f"rgb({palette[i][0]}, {palette[i][1]}, {palette[i][2]})"
                    for i in (1, 63, 125, 188, 250)
                )
                + ")",
                "legend_note": "NOAA bloom index. No detection, clouds, land and invalid pixels are transparent; transparency alone does not establish bloom absence.",
                "start": timestamp(stamp[3]),
                "stop": timestamp(stamp[4] or stamp[3]),
                "period": "Daily Sentinel-3 A/B composite",
                "status": "Satellite bloom signal",
                "source": "NOAA NCCOS · Sentinel-3 OLCI bloom index",
                "source_file": source_file,
                "source_variable": "CI-CIcyano",
                "source_download_url": source_url,
                "source_url": "https://coastalscience.noaa.gov/science-areas/habs/hab-monitoring-system/cyanobacteria-algal-bloom-from-satellite-in-western-lake-erie-basin/",
                "resolution_label": "300 m native grid",
                "description": "Only NOAA-detected bloom signal is coloured. No detection, clouds and invalid observations remain transparent. The index estimates relative biomass, not toxins or a swimming-safety classification.",
            },
        )
        status = np.where(inside, raw, np.nan).astype(np.float32)
        _write_cog(status, src.transform, src.crs, out_dir / "bloom_status.tif", nodata=np.nan)
        lake_pixels = int(inside.sum())
        layer["status_path"] = "bloom_status.tif"
        layer["observed_pixels"] = int(observed.sum())
        layer["detection_pixels"] = int(detected.sum())
        layer["observed_percent"] = round(int(observed.sum()) / lake_pixels * 100, 2)
        layer["coverage_text"] = (
            f"Detected bloom signal on {layer['coverage_percent']:.2f}% of mapped lake pixels; {layer['observed_percent']:.1f}% had usable observations. {layer['description']}"
        )
        layer["source_flag_counts"] = {
            str(i): int((inside & (raw == i)).sum()) for i in (0, 250, 251, 252, 253, 254, 255)
        }
        return layer
