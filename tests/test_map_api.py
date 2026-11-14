import morecantile
import pytest
from fastapi.testclient import TestClient
from helpers import make_granule
from rasterio.warp import transform

from nisar_fetch.map.prep import prepare_granule
from nisar_fetch.map.webapp import create_app


@pytest.fixture
def client(tmp_path):
    source = tmp_path / "granule.h5"
    out = tmp_path / "map"
    make_granule(source)
    prepare_granule(source, out, step=1)
    app = create_app(map_dir=out, web_dir=tmp_path / "web")
    return TestClient(app), out


def _center(metadata):
    west, south, east, north = metadata["bounds_utm"]
    lons, lats = transform(
        metadata["crs"],
        "EPSG:4326",
        [(west + east) / 2],
        [(south + north) / 2],
    )
    return lons[0], lats[0]


def test_layers_and_granule(client) -> None:
    api, _ = client
    granule = api.get("/api/granule")
    assert granule.status_code == 200
    assert granule.json()["scene"] == "TEST_GRANULE"

    layers = api.get("/api/layers")
    assert layers.status_code == 200
    ids = {layer["id"] for layer in layers.json()}
    assert ids == {"hh", "hv", "rgb"}
    assert all(layer["tile_url"].startswith("/tiles/") for layer in layers.json())


def test_tile_endpoint(client) -> None:
    api, _ = client
    metadata = api.get("/api/granule").json()
    lon, lat = _center(metadata)
    tile = morecantile.tms.get("WebMercatorQuad").tile(lon, lat, 8)

    for layer in ("hh", "hv", "rgb"):
        response = api.get(f"/tiles/{layer}/{tile.z}/{tile.x}/{tile.y}.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_point_endpoint(client) -> None:
    api, _ = client
    metadata = api.get("/api/granule").json()
    lon, lat = _center(metadata)

    response = api.get("/api/point", params={"layer": "hh", "lon": lon, "lat": lat})
    assert response.status_code == 200
    payload = response.json()
    assert payload["layer"] == "hh"
    assert payload["value"] is not None
    assert payload["mask"] == [1]
    assert payload["unit"] == "dB"


def test_unknown_layer(client) -> None:
    api, _ = client
    assert api.get("/tiles/nope/0/0/0.png").status_code == 404
    assert api.get("/api/point", params={"layer": "nope", "lon": 0, "lat": 0}).status_code == 404


def test_missing_map_directory(tmp_path) -> None:
    app = create_app(map_dir=tmp_path / "missing", web_dir=tmp_path / "web")
    api = TestClient(app)
    assert api.get("/healthz").json()["prepared"] is False
    assert api.get("/api/layers").status_code == 503


def test_logarithmic_tile_colours_preserve_point_values_and_mask(tmp_path):
    import io
    import json

    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.transform import from_bounds
    from rio_tiler.colormap import cmap

    tile = morecantile.tms.get("WebMercatorQuad").tile(-82.5, 41.8, 9)
    bounds = morecantile.tms.get("WebMercatorQuad").xy_bounds(tile)
    affine = from_bounds(*bounds, 16, 16)
    data = np.full((16, 16), np.nan, dtype=np.float32)
    data[:, :4], data[:, 4:8], data[:, 8:12], data[:, 12:14] = 0.3, 3, 30, 0
    with rasterio.open(
        tmp_path / "chl.tif",
        "w",
        driver="GTiff",
        width=16,
        height=16,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=affine,
        nodata=np.nan,
    ) as f:
        f.write(data, 1)
    metadata = {
        "layers": {
            "chlorophyll": {
                "path": "chl.tif",
                "type": "single",
                "unit": "mg/m³",
                "rescale": [0.3, 30],
                "scale": "log",
                "colormap": "viridis",
            }
        }
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    api = TestClient(create_app(tmp_path, tmp_path / "web"))
    url = api.get("/api/layers").json()[0]["tile_url"]
    response = api.get(
        url.replace("{z}", str(tile.z)).replace("{x}", str(tile.x)).replace("{y}", str(tile.y))
    )
    assert response.status_code == 200
    image = np.asarray(Image.open(io.BytesIO(response.content)))
    colors = cmap.get("viridis")
    np.testing.assert_allclose(image[128, 32, :3], colors[0][:3], atol=2)
    np.testing.assert_allclose(image[128, 96, :3], colors[127][:3], atol=2)
    np.testing.assert_allclose(image[128, 160, :3], colors[255][:3], atol=2)
    assert image[128, 208, 3] == image[128, 240, 3] == 0
    x, y = affine @ (6.5, 8.5)
    lon, lat = transform("EPSG:3857", "EPSG:4326", [x], [y])
    point = api.get(
        "/api/point", params={"layer": "chlorophyll", "lon": lon[0], "lat": lat[0]}
    ).json()
    assert point["value"] == 3
    assert point["unit"] == "mg/m³"
    metadata["layers"]["chlorophyll"]["rescale"] = [0.1, 100]
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    assert api.get("/api/layers").json()[0]["tile_url"] != url
