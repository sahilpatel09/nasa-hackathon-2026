import json

import numpy as np
import pytest
import rasterio
from helpers import make_granule

from nisar_fetch.map.prep import find_input, prepare_granule


def test_find_input(tmp_path) -> None:
    path = tmp_path / "granule.h5"
    make_granule(path)
    assert find_input(tmp_path) == path
    assert find_input(tmp_path, path) == path


def test_prepare_granule_writes_cogs_and_metadata(tmp_path) -> None:
    source = tmp_path / "granule.h5"
    out = tmp_path / "map"
    make_granule(source)

    metadata = prepare_granule(
        source,
        out,
        step=1,
        aoi_bbox=(-80.6, 43.4, -78.8, 44.2),
    )

    assert metadata["scene"] == "TEST_GRANULE"
    assert metadata["epsg"] == 32617
    assert metadata["bounds_wgs84"] == pytest.approx([-80.0, 43.0, -79.0, 44.0])
    assert metadata["layers"]["hh"]["rescale"][0] < metadata["layers"]["hh"]["rescale"][1]
    assert (out / "hh.tif").is_file()
    assert (out / "hv.tif").is_file()
    assert (out / "rgb.tif").is_file()
    assert json.loads((out / "metadata.json").read_text())["scene"] == "TEST_GRANULE"

    with rasterio.open(out / "hh.tif") as src:
        assert src.crs.to_epsg() == 32617
        assert src.shape == (80, 100)
        data = src.read(1)
    assert np.isnan(data[0, 0])
    assert np.isfinite(data[20, 30])

    with rasterio.open(out / "rgb.tif") as src:
        assert src.count == 3
        assert src.dtypes[0] == "uint8"
        assert src.read_masks(1)[0, 0] == 0
        assert src.read_masks(1)[20, 30] == 255


def test_prepare_granule_reuses_outputs(tmp_path) -> None:
    source = tmp_path / "granule.h5"
    out = tmp_path / "map"
    make_granule(source)
    first = prepare_granule(source, out, step=1)
    second = prepare_granule(source, out, step=1)
    assert first["scene"] == second["scene"]
    assert first["layers"] == second["layers"]
