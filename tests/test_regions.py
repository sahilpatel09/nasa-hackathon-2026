import pytest

from nisar_fetch import regions


def test_bbox_to_wkt_is_closed_polygon() -> None:
    wkt = regions.bbox_to_wkt(-80.6, 43.4, -78.8, 44.2)
    assert wkt.startswith("POLYGON((")
    assert wkt.endswith("))")
    assert wkt == "POLYGON((-80.6 43.4,-78.8 43.4,-78.8 44.2,-80.6 44.2,-80.6 43.4))"


@pytest.mark.parametrize(
    "bbox",
    [
        (-78.8, 43.4, -80.6, 44.2),  # west >= east
        (-80.6, 44.2, -78.8, 43.4),  # south >= north
        (-200.0, 43.4, -78.8, 44.2),  # longitude out of range
        (-80.6, -100.0, -78.8, 44.2),  # latitude out of range
    ],
)
def test_bbox_to_wkt_rejects_invalid(bbox: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValueError):
        regions.bbox_to_wkt(*bbox)


def test_toronto_region_matches_documented_bbox() -> None:
    toronto = regions.get_region("toronto")
    assert toronto.bbox == (-80.6, 43.4, -78.8, 44.2)


def test_unknown_region_lists_choices() -> None:
    with pytest.raises(ValueError, match="unknown region"):
        regions.get_region("atlantis")
