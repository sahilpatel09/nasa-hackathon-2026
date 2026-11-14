import io
import json

import h5py
import morecantile
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from helpers import make_sme2
from PIL import Image
from rasterio.warp import transform

from nisar_fetch import cli
from nisar_fetch.map.prep import prepare_granule
from nisar_fetch.map.webapp import create_app


@pytest.fixture
def sme2(tmp_path):
    path = tmp_path / "sme2.h5"
    bbox = make_sme2(path)
    return path, tmp_path / "map", bbox


def prepare(source):
    path, out, bbox = source
    return prepare_granule(path, out, step=4, aoi_name="toledo", aoi_bbox=bbox)


def test_native_projection_crop_values_and_quality(sme2):
    meta = prepare(sme2)
    path, out, _ = sme2
    assert meta["epsg"] == 6933
    assert meta["step"] == 1
    assert meta["source_spacing_m"] == meta["output_spacing_m"]
    assert meta["layers"]["soil_moisture"]["unit"] == "m³/m³"
    assert meta["layers"]["soil_moisture"]["rescale"] == [0, 0.6]
    assert all(meta["cog_valid"].values())
    assert meta["coverage"]["study_pixels"] == 42
    assert meta["coverage"]["usable_pixels"] == 38
    with h5py.File(path) as f, rasterio.open(out / "soil_moisture.tif") as raster:
        g = f["science/LSAR/SME2/grids"]
        assert raster.shape == (6, 7)
        assert raster.crs.to_epsg() == 6933
        assert raster.res == pytest.approx((200.17900467012078,) * 2)
        assert raster.xy(0, 0) == pytest.approx((g["xCoordinates"][2], g["yCoordinates"][2]))
        actual = raster.read(1)
        expected = g["soilMoisture"][2:8, 2:9]
        valid = np.ones(expected.shape, dtype=bool)
        valid[1, 1:3] = False
        valid[2, 2:4] = False
        np.testing.assert_array_equal(actual[valid], expected[valid])
        assert np.isnan(actual[~valid]).all()
        assert (raster.read_masks(1)[~valid] == 0).all()
    with rasterio.open(out / "uncertainty.tif") as raster:
        assert np.isnan(raster.read(1)[3, 3])
        assert raster.read(1)[0, 0] == pytest.approx(0.03)


def test_three_source_raster_api_comparisons_and_masked_json(sme2):
    prepare(sme2)
    path, out, _ = sme2
    api = TestClient(create_app(out, out / "web"))
    with rasterio.open(out / "soil_moisture.tif") as raster, h5py.File(path) as f:
        for row, col in [(0, 0), (3, 4), (5, 6), (1, 1)]:
            x, y = raster.xy(row, col)
            lon, lat = transform(raster.crs, "EPSG:4326", [x], [y])
            payload = api.get(
                "/api/point", params={"layer": "soil_moisture", "lon": lon[0], "lat": lat[0]}
            ).json()
            assert payload["unit"] == "m³/m³"
            if (row, col) == (1, 1):
                assert payload["value"] is None
                assert payload["values"] == [None]
                assert payload["mask"] == [0]
            else:
                source = float(f["science/LSAR/SME2/grids/soilMoisture"][row + 2, col + 2])
                assert payload["value"] == float(raster.read(1)[row, col]) == source
        tile = morecantile.tms.get("WebMercatorQuad").tile(lon[0], lat[0], 12)
        response = api.get(f"/tiles/soil_moisture/{tile.z}/{tile.x}/{tile.y}.png")
        assert response.status_code == 200
        image = np.array(Image.open(io.BytesIO(response.content)))
        assert image.shape == (256, 256, 4)
        assert (image[:, :, 3] == 0).any()
        assert (image[:, :, 3] == 255).any()


def test_no_usable_data_publishes_diagnostics_and_hides_old_layer(sme2):
    prepare(sme2)
    path, out, _ = sme2
    with h5py.File(path, "a") as f:
        f["science/LSAR/SME2/grids/retrievalQualityFlag"][:] = 8
    with pytest.raises(ValueError, match="no usable observation.*0 / 42"):
        prepare(sme2)
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["status"] == "no_usable_observation"
    assert meta["coverage"]["usable_percent"] == 0
    assert meta["layers"] == {}
    api = TestClient(create_app(out, out / "web"))
    assert api.get("/api/layers").json() == []
    assert api.get("/tiles/soil_moisture/0/0/0.png").status_code == 404


@pytest.mark.parametrize(
    "missing",
    [
        "soilMoisture",
        "soilMoistureUncertainty",
        "retrievalQualityFlag",
        "surfaceQualityFlag",
        "projection",
    ],
)
def test_missing_layers_fail_explanatorily(sme2, missing):
    with h5py.File(sme2[0], "a") as f:
        del f["science/LSAR/SME2/grids"][missing]
    with pytest.raises(ValueError, match=f"missing required.*{missing}"):
        prepare(sme2)
    assert not (sme2[1] / "metadata.json").exists()


def test_no_overlap_counts_entire_study_as_missing(sme2):
    with pytest.raises(ValueError, match="no usable observation"):
        prepare_granule(sme2[0], sme2[1], aoi_bbox=(-84, 41.4, -83.95, 41.45))
    meta = json.loads((sme2[1] / "metadata.json").read_text())
    assert meta["coverage"]["measurement_pixels"] == 0
    assert meta["coverage"]["study_pixels"] > 0


def test_projection_must_not_default_to_toronto(sme2):
    with h5py.File(sme2[0], "a") as f:
        del f["science/LSAR/SME2/grids/projection"].attrs["epsg_code"]
    with pytest.raises(ValueError, match="refusing to guess"):
        prepare(sme2)


def test_toledo_cli_empty_observation_exits_two(sme2, capsys):
    # The fixture does not overlap most of the real study area; all clear flags
    # are disabled to exercise the user's actual download's empty-data path.
    with h5py.File(sme2[0], "a") as f:
        f["science/LSAR/SME2/grids/surfaceQualityFlag"][:] = 1
    assert (
        cli.main(["map-prep", "--input", str(sme2[0]), "--out", str(sme2[1]), "--region", "toledo"])
        == 2
    )
    assert "no usable observation" in capsys.readouterr().err
