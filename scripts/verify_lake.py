"""Compare three real source pixels per Lake Erie layer with each COG and API."""

import io
import json
from pathlib import Path

import h5py
import morecantile
import numpy as np
import rasterio
from fastapi.testclient import TestClient
from PIL import Image
from rasterio.warp import transform

from nisar_fetch.map.webapp import create_app


def main():
    out = Path("data/lake-erie")
    meta = json.loads((out / "metadata.json").read_text())
    api = TestClient(create_app(out, Path("web")))
    checks = []
    for name, config in meta["layers"].items():
        with rasterio.open(out / config["path"]) as cog:
            data = cog.read(1, masked=True)
            valid_rows, valid_cols = np.nonzero(~data.mask)
            samples = []
            for i in np.linspace(0, len(valid_rows) - 1, 3, dtype=int):
                row, col = int(valid_rows[i]), int(valid_cols[i])
                x, y = cog.xy(row, col)
                lon, lat = transform(cog.crs, "EPSG:4326", [x], [y])
                if name == "radar":
                    with h5py.File(Path("data/toledo") / config["source_file"]) as f:
                        group = f["science/LSAR/SME2/grids/radarData/frequencyA"]
                        source_col = int(np.argmin(np.abs(group["xCoordinates"][:] - x)))
                        source_row = int(np.argmin(np.abs(group["yCoordinates"][:] - y)))
                        raw = group["sigma0HH"][source_row, source_col]
                        expected = float(np.float32(10) * np.log10(raw))
                else:
                    with rasterio.open(out / "sources" / config["source_file"]) as source:
                        source_row, source_col = source.index(x, y)
                        expected = float(source.read(1)[source_row, source_col])
                actual = float(data[row, col])
                np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
                response = api.get(
                    "/api/point", params={"layer": name, "lon": lon[0], "lat": lat[0]}
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["value"] == actual
                assert payload["unit"] == config["unit"]
                samples.append(
                    {
                        "lon": lon[0],
                        "lat": lat[0],
                        "source_value": expected,
                        "cog_value": actual,
                        "api_value": payload["value"],
                    }
                )
            tile = morecantile.tms.get("WebMercatorQuad").tile(lon[0], lat[0], 9)
            response = api.get(f"/tiles/{name}/{tile.z}/{tile.x}/{tile.y}.png")
            assert response.status_code == 200
            image = np.array(Image.open(io.BytesIO(response.content)))
            assert image.shape == (256, 256, 4)
            assert (image[:, :, 3] > 0).any()
            # Inland point southwest of the lake must be transparent/missing.
            land = api.get("/api/point", params={"layer": name, "lon": -83.5, "lat": 41.3})
            assert land.status_code == 404 or land.json()["value"] is None
            checks.append(
                {
                    "layer": name,
                    "date": config["start"],
                    "coverage_percent": config["coverage_percent"],
                    "samples": samples,
                    "tile": "RGBA PNG; usable pixels present",
                    "land": "masked",
                }
            )
    (out / "verification.json").write_text(json.dumps(checks, indent=2))
    for check in checks:
        print(
            f"{check['layer']}: three source/COG/API values match; tile renders; land masked; {check['coverage_percent']}% coverage"
        )


if __name__ == "__main__":
    main()
