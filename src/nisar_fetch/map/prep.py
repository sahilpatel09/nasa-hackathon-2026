"""Convert a NISAR GCOV or SME2 HDF5 granule into map-ready Cloud-Optimized GeoTIFFs."""

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
from rasterio.transform import Affine
from rasterio.warp import transform_bounds
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles
from shapely.geometry import Polygon, box

_POINT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)")
_GRID = "science/LSAR/GCOV/grids/frequencyA"
_IDENT = "science/LSAR/identification"


def find_input(data_dir: Path, input_path: Path | None = None) -> Path:
    if input_path is not None:
        if not input_path.is_file():
            raise ValueError(f"input file not found: {input_path}")
        return input_path
    candidates = sorted([*data_dir.glob("*.h5"), *data_dir.glob("*.hdf5")])
    if not candidates:
        raise ValueError(f"no .h5 files found in {data_dir}")
    return candidates[0]


def _decode(value: Any) -> str:
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _scalar(group: h5py.Group | None, name: str, default: Any = None) -> Any:
    if group is None:
        return default
    dataset = group.get(name)
    if not isinstance(dataset, h5py.Dataset):
        return default
    value = dataset[()]
    if isinstance(value, np.ndarray) and value.shape == ():
        value = value.item()
    return value


def _text_scalar(group: h5py.Group | None, name: str, default: str = "") -> str:
    value = _scalar(group, name, default)
    return _decode(value) if value is not None else default


def _decode_list(group: h5py.Group | None, name: str) -> list[str]:
    if group is None:
        return []
    dataset = group.get(name)
    if not isinstance(dataset, h5py.Dataset):
        return []
    return [_decode(value) for value in dataset[()]]


def _int_value(value: Any) -> int | None:
    return int(value) if value is not None else None


def _coverage_note(
    footprint: list[list[float]],
    aoi_bbox: tuple[float, float, float, float] | None,
    aoi_name: str,
    aoi_label: str,
) -> str:
    if not aoi_bbox:
        return ""
    shape = Polygon(footprint)
    if not shape.is_valid:
        shape = shape.buffer(0)
    aoi = box(*aoi_bbox)
    if aoi.area <= 0:
        return ""
    coverage = shape.intersection(aoi).area / aoi.area
    percent = round(coverage * 100)
    if coverage >= 0.95:
        note = f"This granule covers essentially the entire {aoi_label}."
    elif coverage >= 0.5:
        note = f"This granule covers about {percent}% of the {aoi_label}."
    elif coverage > 0:
        note = f"This granule intersects only about {percent}% of the {aoi_label}."
    else:
        note = f"This granule does not intersect the {aoi_label}."
    if coverage < 0.2 and aoi_name == "toronto":
        note += " Toronto proper is outside the swath."
    elif coverage < 0.5:
        note += " Parts of the AOI are outside the swath."
    return note


def _parse_footprint(wkt: str) -> list[list[float]] | None:
    points = [[float(lon), float(lat)] for lon, lat, _ in _POINT_RE.findall(wkt)]
    if len(points) < 4:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def _valid_window(mask: h5py.Dataset) -> tuple[int, int, int, int]:
    height, width = mask.shape
    stride = max(1, min(64, max(1, height // 4), max(1, width // 4)))
    sample = mask[::stride, ::stride]
    rows, cols = np.nonzero(sample == 1)
    if len(rows) == 0:
        raise ValueError("granule contains no valid pixels")
    row0 = max(int(rows.min()) * stride - stride, 0)
    row1 = min((int(rows.max()) + 1) * stride + stride, height)
    col0 = max(int(cols.min()) * stride - stride, 0)
    col1 = min((int(cols.max()) + 1) * stride + stride, width)
    return row0, row1, col0, col1


def _value_range(data: np.ndarray, valid: np.ndarray) -> tuple[float, float]:
    values = data[valid]
    low, high = np.percentile(values, [2, 98])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
    if high <= low:
        high = low + 1.0
    return round(float(low), 2), round(float(high), 2)


def _stats(data: np.ndarray, valid: np.ndarray) -> list[float]:
    values = data[valid]
    p2, p50, p98 = np.percentile(values, [2, 50, 98])
    return [round(float(v), 2) for v in (p2, p50, p98)]


def _normalize(data: np.ndarray, valid: np.ndarray, low: float, high: float) -> np.ndarray:
    output = np.zeros(data.shape, dtype=np.uint8)
    scaled = ((data[valid] - low) / (high - low)) * 255.0
    output[valid] = np.clip(scaled, 0, 255).astype(np.uint8)
    return output


def _write_cog(
    array: np.ndarray,
    transform: Affine,
    crs: CRS,
    out_path: Path,
    *,
    nodata: float | None,
    mask: np.ndarray | None = None,
    photometric: str | None = None,
) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_name(f".{out_path.name}.tmp.tif")
    temp_path.unlink(missing_ok=True)
    out_path.unlink(missing_ok=True)

    height, width = array.shape[-2:]
    block = 512 if min(width, height) >= 512 else 16
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1 if array.ndim == 2 else array.shape[0],
        "dtype": array.dtype,
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": block,
        "blockysize": block,
        "compress": "DEFLATE",
        "predictor": 3 if array.dtype == np.float32 else 2,
        "BIGTIFF": "IF_SAFER",
    }
    if nodata is not None:
        profile["nodata"] = nodata
    if photometric:
        profile["photometric"] = photometric
        profile["interleave"] = "pixel"

    with rasterio.open(temp_path, "w", **profile) as dst:
        if array.ndim == 2:
            dst.write(array, 1)
        else:
            dst.write(array)
        if mask is not None:
            dst.write_mask(mask)

    cog_profile = cog_profiles.get("deflate").copy()
    cog_profile.update(
        {
            "predictor": profile["predictor"],
            "blockxsize": block,
            "blockysize": block,
            "BIGTIFF": "IF_SAFER",
        }
    )
    if photometric:
        cog_profile.update({"photometric": photometric, "interleave": "pixel"})
    cog_translate(
        temp_path,
        out_path,
        cog_profile,
        add_mask=mask is not None,
        overview_resampling="average",
        quiet=True,
    )
    temp_path.unlink(missing_ok=True)
    return bool(cog_validate(out_path)[0])


def prepare_granule(
    h5_path: Path,
    out_dir: Path,
    *,
    step: int = 4,
    force: bool = False,
    aoi_name: str = "toronto",
    aoi_bbox: tuple[float, float, float, float] | None = None,
    aoi_label: str | None = None,
) -> dict[str, Any]:
    if step < 1:
        raise ValueError("step must be >= 1")
    with h5py.File(h5_path, "r") as handle:
        if "science/LSAR/SME2" in handle:
            return _prepare_soil_moisture(
                handle, h5_path, out_dir, aoi_name, aoi_bbox, aoi_label or aoi_name
            )
    aoi_label = aoi_label or aoi_name
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = out_dir / "metadata.json"
    outputs = [out_dir / "hh.tif", out_dir / "hv.tif", out_dir / "rgb.tif"]
    if not force and metadata_path.exists() and all(path.exists() for path in outputs):
        existing = json.loads(metadata_path.read_text())
        if (
            existing.get("source_file") == h5_path.name
            and existing.get("step") == step
            and existing.get("aoi_name") == aoi_name
            and existing.get("aoi_label") == aoi_label
        ):
            return existing

    with h5py.File(h5_path, "r") as handle:
        grid = handle.get(_GRID)
        if grid is None:
            raise LookupError(f"missing {_GRID} in {h5_path}")
        assert isinstance(grid, h5py.Group)
        mask_dataset = grid.get("mask")
        hh_dataset = grid.get("HHHH")
        hv_dataset = grid.get("HVHV")
        if not all(isinstance(ds, h5py.Dataset) for ds in (mask_dataset, hh_dataset, hv_dataset)):
            raise ValueError(f"missing mask or polarization datasets in {h5_path}")
        assert isinstance(mask_dataset, h5py.Dataset)
        assert isinstance(hh_dataset, h5py.Dataset)
        assert isinstance(hv_dataset, h5py.Dataset)

        x_coords = np.asarray(grid["xCoordinates"][()], dtype=np.float64)
        y_coords = np.asarray(grid["yCoordinates"][()], dtype=np.float64)
        x_spacing = float(np.asarray(grid["xCoordinateSpacing"][()]))
        y_spacing = float(np.asarray(grid["yCoordinateSpacing"][()]))

        row0, row1, col0, col1 = _valid_window(mask_dataset)
        window = (slice(row0, row1, step), slice(col0, col1, step))
        mask = np.asarray(mask_dataset[window])
        hh = np.asarray(hh_dataset[window], dtype=np.float32)
        hv = np.asarray(hv_dataset[window], dtype=np.float32)

        valid = (mask == 1) & np.isfinite(hh) & (hh > 0) & np.isfinite(hv) & (hv > 0)
        if not valid.any():
            raise ValueError("granule contains no valid HH/HV pixels")

        hh_db = np.full(hh.shape, np.nan, dtype=np.float32)
        hv_db = np.full(hv.shape, np.nan, dtype=np.float32)
        hh_db[valid] = 10.0 * np.log10(hh[valid])
        hv_db[valid] = 10.0 * np.log10(hv[valid])
        ratio_db = hh_db - hv_db

        hh_range = _value_range(hh_db, valid)
        hv_range = _value_range(hv_db, valid)
        ratio_range = _value_range(ratio_db, valid)

        left = float(x_coords[col0]) - x_spacing / 2.0
        top = float(y_coords[row0]) - y_spacing / 2.0
        transform = Affine(x_spacing * step, 0.0, left, 0.0, y_spacing * step, top)
        height, width = hh_db.shape
        right = left + width * x_spacing * step
        bottom = top + height * y_spacing * step

        epsg_value = grid.get("projection")
        epsg = int(epsg_value.attrs.get("epsg_code", 32617)) if epsg_value else 32617
        crs = CRS.from_epsg(epsg)

        hh_ok = _write_cog(hh_db, transform, crs, out_dir / "hh.tif", nodata=np.nan)
        hv_ok = _write_cog(hv_db, transform, crs, out_dir / "hv.tif", nodata=np.nan)
        rgb = np.stack(
            [
                _normalize(hh_db, valid, *hh_range),
                _normalize(hv_db, valid, *hv_range),
                _normalize(ratio_db, valid, *ratio_range),
            ]
        )
        rgb_ok = _write_cog(
            rgb,
            transform,
            crs,
            out_dir / "rgb.tif",
            nodata=None,
            mask=valid.astype(np.uint8) * 255,
            photometric="RGB",
        )

        ident = handle.get(_IDENT)
        if not isinstance(ident, h5py.Group):
            ident = None

        footprint = _parse_footprint(_text_scalar(ident, "boundingPolygon"))
        if footprint is None:
            west, south, east, north = transform_bounds(
                crs, CRS.from_epsg(4326), left, bottom, right, top
            )
            footprint = [[west, south], [east, south], [east, north], [west, north], [west, south]]
        lons = [point[0] for point in footprint]
        lats = [point[1] for point in footprint]
        bounds_wgs84 = [min(lons), min(lats), max(lons), max(lats)]

        metadata: dict[str, Any] = {
            "scene": _text_scalar(ident, "granuleId", h5_path.stem),
            "source_file": h5_path.name,
            "product": _text_scalar(ident, "productType", "GCOV"),
            "product_level": _text_scalar(ident, "productLevel", "L2"),
            "start": _text_scalar(ident, "zeroDopplerStartTime"),
            "stop": _text_scalar(ident, "zeroDopplerEndTime"),
            "orbit_direction": _text_scalar(ident, "orbitPassDirection"),
            "look_direction": _text_scalar(ident, "lookDirection"),
            "absolute_orbit": _int_value(_scalar(ident, "absoluteOrbitNumber")),
            "track": _int_value(_scalar(ident, "trackNumber")),
            "frame": _int_value(_scalar(ident, "frameNumber")),
            "polarizations": _decode_list(grid, "listOfPolarizations"),
            "epsg": epsg,
            "crs": crs.to_string(),
            "source_spacing_m": x_spacing,
            "output_spacing_m": x_spacing * step,
            "step": step,
            "bounds_utm": [left, bottom, right, top],
            "bounds_wgs84": bounds_wgs84,
            "footprint": footprint,
            "aoi_name": aoi_name,
            "aoi_label": aoi_label,
            "aoi_bbox": list(aoi_bbox) if aoi_bbox else None,
            "coverage_note": _coverage_note(footprint, aoi_bbox, aoi_name, aoi_label),
            "layers": {
                "hh": {
                    "path": "hh.tif",
                    "label": "HH (dB)",
                    "unit": "dB",
                    "type": "single",
                    "rescale": list(hh_range),
                    "colormap": "viridis",
                },
                "hv": {
                    "path": "hv.tif",
                    "label": "HV (dB)",
                    "unit": "dB",
                    "type": "single",
                    "rescale": list(hv_range),
                    "colormap": "inferno",
                },
                "rgb": {
                    "path": "rgb.tif",
                    "label": "False color (HH/HV/ratio)",
                    "type": "rgb",
                    "rescale": None,
                    "colormap": None,
                },
            },
            "stats": {
                "hh_db": _stats(hh_db, valid),
                "hv_db": _stats(hv_db, valid),
                "hh_hv_db": _stats(ratio_db, valid),
            },
            "cog_valid": {"hh": hh_ok, "hv": hv_ok, "rgb": rgb_ok},
            "generated_at": datetime.now(UTC).isoformat(),
        }

    metadata_path.write_text(json.dumps(metadata, indent=2))
    return metadata


def _measurement_valid(dataset: h5py.Dataset, data: np.ndarray) -> np.ndarray:
    valid = np.isfinite(data)
    for key in ("_FillValue", "missing_value"):
        if key in dataset.attrs:
            for fill in np.atleast_1d(dataset.attrs[key]):
                valid &= data != fill
    return valid


def _prepare_soil_moisture(
    handle: h5py.File,
    h5_path: Path,
    out_dir: Path,
    aoi_name: str,
    aoi_bbox: tuple[float, float, float, float] | None,
    aoi_label: str,
) -> dict[str, Any]:
    """Crop baseline SME2 on its native grid; never decimate or convert to dB.

    Empty observations publish diagnostic metadata without a display layer, then
    fail explicitly. This lets the viewer explain a real acquisition's absence.
    """
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from rasterio.windows import Window, from_bounds
    from rasterio.windows import transform as window_transform
    from shapely.geometry import mapping

    if aoi_bbox is None:
        raise ValueError("SME2 preparation requires a study-area bounding box")
    grid = handle["science/LSAR/SME2/grids"]
    names = (
        "soilMoisture",
        "soilMoistureUncertainty",
        "retrievalQualityFlag",
        "surfaceQualityFlag",
    )
    required = (
        *names,
        "xCoordinates",
        "yCoordinates",
        "xCoordinateSpacing",
        "yCoordinateSpacing",
        "projection",
    )
    missing = [name for name in required if not isinstance(grid.get(name), h5py.Dataset)]
    if missing:
        raise ValueError("SME2 missing required baseline layers/grid: " + ", ".join(missing))
    projection = grid["projection"]
    epsg = projection.attrs.get("epsg_code")
    if epsg is None:
        raise ValueError("SME2 projection is missing epsg_code; refusing to guess the CRS")
    crs = CRS.from_epsg(int(epsg))
    x, y = grid["xCoordinates"][:], grid["yCoordinates"][:]
    dx, dy = float(grid["xCoordinateSpacing"][()]), float(grid["yCoordinateSpacing"][()])
    if dx <= 0 or dy >= 0 or len(x) < 2 or len(y) < 2:
        raise ValueError("SME2 requires an eastward, north-up coordinate grid")
    if not np.allclose(np.diff(x), dx) or not np.allclose(np.diff(y), dy):
        raise ValueError("SME2 coordinates do not match the declared native spacing")
    if any(grid[name].shape != (len(y), len(x)) for name in names):
        raise ValueError("SME2 baseline layers must share the native coordinate grid")
    native = Affine(dx, 0, float(x[0]) - dx / 2, 0, dy, float(y[0]) - dy / 2)
    requested = from_bounds(*transform_bounds("EPSG:4326", crs, *aoi_bbox), native)
    # Use the entire study window, including nodata beyond the source swath, so
    # coverage uses the whole study area rather than just the overlapping data.
    col0, row0 = int(np.floor(requested.col_off)), int(np.floor(requested.row_off))
    col1 = int(np.ceil(requested.col_off + requested.width))
    row1 = int(np.ceil(requested.row_off + requested.height))
    window = Window(col0, row0, col1 - col0, row1 - row0)
    if window.width * window.height > 25_000_000:
        raise ValueError("SME2 study window exceeds 25 million pixels; choose a smaller study area")
    affine = window_transform(window, native)
    shape = (int(window.height), int(window.width))
    inside = geometry_mask(
        [transform_geom("EPSG:4326", crs, mapping(box(*aoi_bbox)))],
        out_shape=shape,
        transform=affine,
        invert=True,
    )
    if not inside.any():
        raise ValueError("SME2 study area contains no native pixel centers")
    r0, r1, c0, c1 = max(row0, 0), min(row1, len(y)), max(col0, 0), min(col1, len(x))
    arrays = {}
    for name in names:
        array = np.full(shape, np.nan, dtype=np.float32)
        if r1 > r0 and c1 > c0:
            array[r0 - row0 : r1 - row0, c0 - col0 : c1 - col0] = grid[name][r0:r1, c0:c1]
        arrays[name] = array
    moisture = arrays["soilMoisture"]
    measurement = inside & _measurement_valid(grid["soilMoisture"], moisture)
    valid = measurement.copy()
    for name in names[2:]:
        valid &= _measurement_valid(grid[name], arrays[name]) & (arrays[name] == 0)
    usable, total = int(valid.sum()), int(inside.sum())
    percent = usable / total * 100
    bounds = rasterio.transform.array_bounds(*shape, affine)
    west, south, east, north = transform_bounds(crs, "EPSG:4326", *bounds)
    footprint = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    ident = handle.get(_IDENT)
    scene = _text_scalar(ident, "granuleId", h5_path.stem)
    # The catalogue manifest is authoritative for collection maturity.
    maturity = "UNKNOWN"
    manifest_path = h5_path.parent / "manifest.json"
    if manifest_path.exists():
        for record in json.loads(manifest_path.read_text()).get("granules", []):
            if record.get("scene") == scene:
                maturity = record.get("maturity", "UNKNOWN")
    if maturity == "UNKNOWN" and "_PR_SME2_" in scene:
        maturity = "PROVISIONAL"
    quality_counts = {
        name: {
            str(int(v)): int(n)
            for v, n in zip(
                *np.unique(arrays[name][inside & np.isfinite(arrays[name])], return_counts=True)
            )
        }
        for name in names[2:]
    }
    metadata = {
        "scene": scene,
        "source_file": h5_path.name,
        "product": "SME2",
        "product_level": "L3",
        "maturity": maturity,
        "start": _text_scalar(ident, "zeroDopplerStartTime"),
        "stop": _text_scalar(ident, "zeroDopplerEndTime"),
        "orbit_direction": _text_scalar(ident, "orbitPassDirection"),
        "track": _int_value(_scalar(ident, "trackNumber")),
        "frame": _int_value(_scalar(ident, "frameNumber")),
        "epsg": int(epsg),
        "crs": crs.to_string(),
        "source_spacing_m": abs(dx),
        "output_spacing_m": abs(dx),
        "source_spacing_xy_m": [dx, dy],
        "step": 1,
        "bounds_native": list(bounds),
        "bounds_wgs84": [west, south, east, north],
        "footprint": footprint,
        "aoi_name": aoi_name,
        "aoi_label": aoi_label,
        "aoi_bbox": list(aoi_bbox),
        "overview_bbox": [-83.65, 41.25, -78.75, 43.05],
        "status": "ready" if usable else "no_usable_observation",
        "quality_rule": "Finite, non-fill baseline soilMoisture; retrievalQualityFlag = 0 and surfaceQualityFlag = 0",
        "coverage": {
            "usable_pixels": usable,
            "study_pixels": total,
            "usable_percent": percent,
            "measurement_pixels": int(measurement.sum()),
            "flag_counts": quality_counts,
            "method": "Native pixel centers inside the entire study area; outside-source pixels count as missing",
        },
        "coverage_note": f"{percent:.2f}% usable study-area coverage ({usable:,} / {total:,} native pixels). Both quality flags must equal 0.",
        "layers": {},
        "cog_valid": {},
        "generated_at": datetime.now(UTC).isoformat(),
    }
    if usable:
        moisture = np.where(valid, moisture, np.nan).astype(np.float32)
        uncertainty = arrays["soilMoistureUncertainty"]
        uncertainty = np.where(
            valid & _measurement_valid(grid[names[1]], uncertainty), uncertainty, np.nan
        ).astype(np.float32)
        outputs = {
            "soil_moisture": (moisture, "soil_moisture.tif"),
            "uncertainty": (uncertainty, "uncertainty.tif"),
            "retrieval_quality": (
                np.where(inside, arrays[names[2]], np.nan),
                "retrieval_quality.tif",
            ),
            "surface_quality": (np.where(inside, arrays[names[3]], np.nan), "surface_quality.tif"),
        }
        for name, (data, filename) in outputs.items():
            metadata["cog_valid"][name] = _write_cog(
                data, affine, crs, out_dir / filename, nodata=np.nan
            )
        metadata["ancillary_rasters"] = {
            name: filename for name, (_, filename) in outputs.items() if name != "soil_moisture"
        }
        metadata["layers"]["soil_moisture"] = {
            "path": "soil_moisture.tif",
            "label": "Soil moisture",
            "type": "single",
            "unit": "m³/m³",
            "rescale": [0, 0.6],
            "colormap": "ylgnbu",
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    if not usable:
        raise ValueError(
            f"SME2 has no usable observation in {aoi_label}: 0 / {total:,} pixels (0%). "
            f"{int(measurement.sum()):,} finite non-fill moisture pixels were found, but none "
            "pass retrievalQualityFlag = 0 AND surfaceQualityFlag = 0. "
            f"Diagnostic metadata written to {out_dir / 'metadata.json'}; no overlay published."
        )
    return metadata
