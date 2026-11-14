"""Named areas of interest and bounding-box helpers."""

from __future__ import annotations

from dataclasses import dataclass

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Region:
    """A named bounding box in decimal degrees (west, south, east, north)."""

    name: str
    west: float
    south: float
    east: float
    north: float
    description: str = ""

    @property
    def bbox(self) -> BBox:
        return (self.west, self.south, self.east, self.north)

    def to_wkt(self) -> str:
        return bbox_to_wkt(*self.bbox)


def bbox_to_wkt(west: float, south: float, east: float, north: float) -> str:
    """Convert a bounding box to a closed WKT POLYGON in lon/lat order."""
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("longitudes must be between -180 and 180")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("latitudes must be between -90 and 90")
    if west >= east:
        raise ValueError("west must be < east (split dateline-crossing AOIs)")
    if south >= north:
        raise ValueError("south must be < north")
    return f"POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))"


REGIONS: dict[str, Region] = {
    "canada": Region("canada", -141.0, 41.7, -52.0, 83.1, "All of Canada (very large)"),
    "bc": Region("bc", -139.1, 48.3, -114.0, 60.1, "British Columbia"),
    "alberta": Region("alberta", -120.0, 49.0, -110.0, 60.0, "Alberta"),
    "ontario": Region("ontario", -95.2, 41.7, -74.3, 56.9, "Ontario"),
    "quebec": Region("quebec", -79.8, 44.9, -57.1, 62.6, "Quebec"),
    "yukon": Region("yukon", -141.0, 60.0, -123.8, 69.7, "Yukon"),
    "toledo": Region("toledo", -83.90, 41.40, -83.70, 41.55, "Farmland southwest of Toledo"),
    "toronto": Region("toronto", -80.6, 43.4, -78.8, 44.2, "Greater Toronto Area"),
    "vancouver": Region("vancouver", -123.4, 49.0, -122.3, 49.5, "Vancouver area"),
}

DEFAULT_REGION = "toronto"


def get_region(name: str) -> Region:
    try:
        return REGIONS[name.lower()]
    except KeyError as exc:
        choices = ", ".join(sorted(REGIONS))
        raise ValueError(f"unknown region {name!r}; choose one of: {choices}") from exc
