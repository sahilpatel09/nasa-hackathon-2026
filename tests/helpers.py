import h5py
import numpy as np


def make_granule(path) -> None:
    height, width = 80, 100
    x = 621267.24 + np.arange(width) * 10.0
    y = 4817434.19 - np.arange(height) * 10.0
    mask = np.full((height, width), 255, dtype=np.uint8)
    mask[10:60, 20:80] = 1
    mask[12:14, 22:24] = 0
    hh = np.zeros((height, width), dtype=np.float32)
    hv = np.zeros((height, width), dtype=np.float32)
    hh[10:60, 20:80] = np.linspace(0.1, 10.0, 50 * 60, dtype=np.float32).reshape(50, 60)
    hv[10:60, 20:80] = np.linspace(0.05, 2.0, 50 * 60, dtype=np.float32).reshape(50, 60)

    with h5py.File(path, "w") as handle:
        grid = handle.create_group("science/LSAR/GCOV/grids/frequencyA")
        grid.create_dataset("xCoordinates", data=x)
        grid.create_dataset("yCoordinates", data=y)
        grid.create_dataset("xCoordinateSpacing", data=10.0)
        grid.create_dataset("yCoordinateSpacing", data=-10.0)
        grid.create_dataset("mask", data=mask)
        grid.create_dataset("HHHH", data=hh)
        grid.create_dataset("HVHV", data=hv)
        grid.create_dataset("listOfPolarizations", data=np.array([b"HH", b"HV"]))
        projection = grid.create_dataset("projection", data=np.float32(0.0))
        projection.attrs["epsg_code"] = 32617

        ident = handle.create_group("science/LSAR/identification")
        ident.create_dataset("granuleId", data=np.bytes_("TEST_GRANULE"))
        ident.create_dataset("productType", data=np.bytes_("GCOV"))
        ident.create_dataset("productLevel", data=np.bytes_("L2"))
        ident.create_dataset("zeroDopplerStartTime", data=np.bytes_("2026-11-02T00:25:07"))
        ident.create_dataset("zeroDopplerEndTime", data=np.bytes_("2026-11-02T00:25:42"))
        ident.create_dataset("orbitPassDirection", data=np.bytes_("Descending"))
        ident.create_dataset("lookDirection", data=np.bytes_("Left"))
        ident.create_dataset("absoluteOrbitNumber", data=np.uint32(6627))
        ident.create_dataset("trackNumber", data=np.uint32(127))
        ident.create_dataset("frameNumber", data=np.uint16(67))
        ident.create_dataset(
            "boundingPolygon",
            data=np.bytes_("POLYGON ((-80 43 0, -79 43 0, -79 44 0, -80 44 0, -80 43 0))"),
        )


def make_sme2(path):
    """Small baseline SME2 with the real product's CRS, spacing, and fill conventions."""
    from rasterio.warp import transform

    dx = 200.17900467012078
    x0, y0 = transform("EPSG:4326", "EPSG:6933", [-83.9], [41.55])
    x = x0[0] + np.arange(12) * dx
    y = y0[0] - np.arange(10) * dx
    moisture = np.linspace(0.02, 0.6, 120, dtype=np.float32).reshape(10, 12)
    moisture[3, 3] = -9999
    moisture[3, 4] = np.nan
    with h5py.File(path, "w") as f:
        g = f.create_group("science/LSAR/SME2/grids")
        for name, data in [
            ("xCoordinates", x),
            ("yCoordinates", y),
            ("xCoordinateSpacing", dx),
            ("yCoordinateSpacing", -dx),
        ]:
            g.create_dataset(name, data=data)
        g.create_dataset("projection", data=6933).attrs["epsg_code"] = 6933
        arrays = {
            "soilMoisture": moisture,
            "soilMoistureUncertainty": np.full((10, 12), 0.03, dtype=np.float32),
            "retrievalQualityFlag": np.zeros((10, 12), dtype=np.int16),
            "surfaceQualityFlag": np.zeros((10, 12), dtype=np.int16),
        }
        arrays["retrievalQualityFlag"][4, 4] = 8
        arrays["surfaceQualityFlag"][4, 5] = 1
        arrays["soilMoistureUncertainty"][5, 5] = -9999
        for name, data in arrays.items():
            g.create_dataset(name, data=data).attrs["_FillValue"] = -9999
        ident = f.create_group("science/LSAR/identification")
        ident.create_dataset("productType", data=np.bytes_("SME2"))
        ident.create_dataset("granuleId", data=np.bytes_("TEST_PR_SME2_FIXTURE"))
    # Centers strictly inside columns 2..8, rows 2..7, avoiding rounding at edges.
    from rasterio.warp import transform_bounds

    bbox = transform_bounds(
        "EPSG:6933", "EPSG:4326", x[2] - dx * 0.2, y[7] - dx * 0.2, x[8] + dx * 0.2, y[2] + dx * 0.2
    )
    return bbox
