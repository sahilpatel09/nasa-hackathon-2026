import json

import numpy as np
import pytest
import rasterio
import xarray as xr
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from nisar_fetch.map.lake import _publish, prepare_netcdf
from nisar_fetch.map.webapp import create_app


@pytest.fixture
def noaa(tmp_path):
    source = tmp_path / "temperature.nc"
    data = np.arange(100, dtype=np.float32).reshape(1, 10, 10) / 10 + 20
    data[0, 3, 3] = np.nan
    ds = xr.Dataset(
        {"sst": (("time", "latitude", "longitude"), data, {"units": "degree_C"})},
        coords={
            "time": [np.datetime64("2026-07-12T12:00:00")],
            "latitude": ("latitude", 42.095 - np.arange(10) * 0.01, {"units": "degrees_north"}),
            "longitude": ("longitude", -83.095 + np.arange(10) * 0.01, {"units": "degrees_east"}),
        },
        attrs={
            "time_coverage_start": "2026-07-12T12:00:00Z",
            "time_coverage_end": "2026-07-12T12:00:00Z",
        },
    )
    ds.to_netcdf(source, engine="h5netcdf")
    return source, tmp_path


def test_cf_source_preserves_values_time_grid_and_masks_shore(noaa):
    source, out = noaa
    geometry = mapping(box(-83.08, 42.02, -83.02, 42.08))
    config = {
        "source_variable": "sst",
        "source_units": ["degree_C"],
        "unit": "°C",
        "rescale": [0, 30],
        "colormap": "turbo",
    }
    layer = prepare_netcdf(source, geometry, out, "temperature", config)
    assert layer["start"] == "2026-07-12T12:00:00Z"
    assert layer["coverage_percent"] == pytest.approx(35 / 36 * 100, abs=0.01)
    assert layer["spacing"] == pytest.approx([0.01, 0.01])
    with rasterio.open(out / "temperature.tif") as f:
        actual = f.read(1)
        assert f.crs.to_epsg() == 4326
        assert tuple(f.transform) == pytest.approx(tuple(from_origin(-83.1, 42.1, 0.01, 0.01)))
        assert np.isnan(actual[0, 0])
        assert np.isnan(actual[3, 3])
        assert actual[4, 4] == pytest.approx(24.4)
    (out / "metadata.json").write_text(json.dumps({"layers": {"temperature": layer}}))
    api = TestClient(create_app(out, out / "web"))
    for row, col in [(2, 2), (4, 4), (7, 7)]:
        response = api.get(
            "/api/point",
            params={
                "layer": "temperature",
                "lon": -83.095 + col * 0.01,
                "lat": 42.095 - row * 0.01,
            },
        )
        assert response.status_code == 200
        assert response.json()["value"] == float(actual[row, col])
        assert response.json()["unit"] == "°C"
    missing = api.get(
        "/api/point", params={"layer": "temperature", "lon": -83.095, "lat": 42.095}
    ).json()
    assert missing["value"] is None


def test_cf_wrong_units_refused(noaa):
    with pytest.raises(ValueError, match="source units"):
        prepare_netcdf(
            noaa[0],
            mapping(box(-83.1, 42, -83, 42.1)),
            noaa[1],
            "temperature",
            {"source_variable": "sst", "source_units": ["kelvin"]},
        )


def test_no_lake_pixels_is_explanatory(tmp_path):
    with pytest.raises(ValueError, match="no usable measurements over Lake Erie"):
        _publish(
            np.full((10, 10), np.nan, dtype=np.float32),
            from_origin(-83.1, 42.1, 0.01, 0.01),
            rasterio.crs.CRS.from_epsg(4326),
            mapping(box(-83.1, 42, -83, 42.1)),
            tmp_path,
            "chlorophyll",
            {},
        )
