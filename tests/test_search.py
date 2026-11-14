from typing import ClassVar

import pytest

from nisar_fetch import search


class FakeProduct:
    properties: ClassVar[dict] = {
        "sceneName": "NISAR_L2_PR_GCOV_034_127_D_067_4005_DHDH_A_20261102T002507_P05036_N_F_J_001",
        "collectionName": "NISAR_L2_GCOV_V1",
        "processingLevel": "GCOV",
        "startTime": "2026-11-02T00:25:07Z",
        "stopTime": "2026-11-02T00:25:42Z",
        "mainBandPolarization": ["HH", "HV"],
        "flightDirection": "DESCENDING",
        "pathNumber": 127,
        "frameNumber": 67,
        "frameCoverage": "Full",
        "fileName": "scene.h5",
        "bytes": {"scene.h5": {"bytes": 5_989_466_112}},
        "url": "https://example.test/scene.h5",
    }

    def get_urls(self) -> list[str]:
        return ["https://example.test/scene.h5"]


def test_default_query_uses_toronto_gcov() -> None:
    query = search.SearchQuery()
    kwargs = query.asf_kwargs()
    assert kwargs["dataset"] == "NISAR"
    assert kwargs["processingLevel"] == "GCOV"
    assert kwargs["dataMaturity"] == "VALIDATED"
    assert "maxResults" not in kwargs
    assert query.aoi_label() == "toronto"


def test_query_maps_nisar_specific_filters() -> None:
    query = search.SearchQuery(
        product="GSLC",
        maturity="PROVISIONAL",
        region="bc",
        start="2026-06-17",
        end="2026-07-01",
        orbit=122,
        frame=67,
        polarization="hh",
        frame_coverage="full",
        max_results=3,
    )
    kwargs = query.asf_kwargs()
    assert kwargs["processingLevel"] == "GSLC"
    assert kwargs["dataMaturity"] == "PROVISIONAL"
    assert kwargs["relativeOrbit"] == 122
    assert kwargs["frame"] == 67
    assert kwargs["mainBandPolarization"] == "HH"
    assert kwargs["frameCoverage"] == "FULL"
    assert kwargs["maxResults"] == 3


def test_custom_bbox_overrides_named_region() -> None:
    query = search.SearchQuery(bbox=(-80.6, 43.4, -78.8, 44.2))
    assert query.aoi_label() == "custom-bbox"
    assert "POLYGON" in query.aoi_wkt()


def test_invalid_product_and_maturity_raise() -> None:
    with pytest.raises(ValueError, match="unknown product"):
        search.SearchQuery(product="NOPE")
    with pytest.raises(ValueError, match="unknown maturity"):
        search.SearchQuery(maturity="FINAL")


def test_summarize_extracts_fields() -> None:
    summary = search.summarize(FakeProduct())
    assert summary["product"] == "GCOV"
    assert summary["maturity"] == "VALIDATED"
    assert summary["polarizations"] == ["HH", "HV"]
    assert summary["bytes"] == 5_989_466_112
    assert summary["url"] == "https://example.test/scene.h5"
