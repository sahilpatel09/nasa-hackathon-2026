import json
from pathlib import Path

import pytest

from nisar_fetch import budget
from nisar_fetch.download import NoResults, download_products
from nisar_fetch.search import SearchQuery

GB = 1_000_000_000


class FakeProduct:
    def __init__(self, scene: str, size: int) -> None:
        self.properties = {
            "sceneName": scene,
            "fileName": f"{scene}.h5",
            "bytes": {f"{scene}.h5": {"bytes": size}},
            "processingLevel": "GCOV",
            "collectionName": "NISAR_L2_PR_GCOV_VALIDATED",
            "startTime": "2026-11-02T00:25:07Z",
            "stopTime": "2026-11-02T00:25:42Z",
            "mainBandPolarization": ["HH", "HV"],
            "flightDirection": "ASCENDING",
            "pathNumber": 34,
            "frameNumber": 127,
            "frameCoverage": "FULL",
        }
        self._url = f"https://example.test/{scene}.h5"

    def get_urls(self) -> list[str]:
        return [self._url]


class FakeResults(list):
    def __init__(self, products: list[FakeProduct]) -> None:
        super().__init__(products)
        self.download_kwargs: dict[str, object] = {}

    def download(self, *, path: str, session: object, processes: int) -> None:
        self.download_kwargs = {
            "path": path,
            "session": session,
            "processes": processes,
        }
        Path(path, "scene.h5").write_bytes(b"data")


def test_download_writes_manifest(tmp_path: Path) -> None:
    products = FakeResults([FakeProduct("SCENE_A", 2 * GB)])
    query = SearchQuery(region="toronto", max_results=1)

    manifest = download_products(
        products,
        query,
        out_dir=tmp_path,
        max_gb=10,
        workers=2,
        session="session",
    )

    assert products.download_kwargs["session"] == "session"
    assert products.download_kwargs["processes"] == 2
    assert manifest["count"] == 1
    assert manifest["total_bytes"] == 2 * GB
    assert manifest["files"] == ["scene.h5"]
    assert manifest["granules"][0]["scene"] == "SCENE_A"

    stored = json.loads((tmp_path / "manifest.json").read_text())
    assert stored["query"]["aoi_label"] == "toronto"
    assert stored["limits"]["max_gb"] == 10


def test_download_refuses_over_budget(tmp_path: Path) -> None:
    products = FakeResults([FakeProduct("SCENE_A", 20 * GB)])
    query = SearchQuery(region="toronto", max_results=1)

    with pytest.raises(budget.BudgetExceeded):
        download_products(products, query, out_dir=tmp_path, max_gb=10, session="session")
    assert products.download_kwargs == {}


def test_download_force_overrides_budget(tmp_path: Path) -> None:
    products = FakeResults([FakeProduct("SCENE_A", 20 * GB)])
    query = SearchQuery(region="toronto", max_results=1)

    manifest = download_products(
        products, query, out_dir=tmp_path, max_gb=10, session="session", force=True
    )
    assert manifest["count"] == 1


def test_download_rejects_empty_results(tmp_path: Path) -> None:
    with pytest.raises(NoResults):
        download_products(
            FakeResults([]),
            SearchQuery(region="toronto"),
            out_dir=tmp_path,
            max_gb=10,
            session="session",
        )
