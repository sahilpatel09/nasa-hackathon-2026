"""NISAR search queries built on top of asf_search."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import asf_search as asf

from . import regions
from .budget import granule_bytes

DEFAULT_PRODUCT = "GCOV"
DEFAULT_MATURITY = "VALIDATED"
MATURITIES = ("VALIDATED", "PROVISIONAL", "BETA")

KNOWN_PRODUCTS: dict[str, dict[str, str]] = {
    "GCOV": {"level": "L2", "description": "Geocoded polarimetric covariance (backscatter)"},
    "GSLC": {"level": "L2", "description": "Geocoded single look complex (phase preserved)"},
    "GUNW": {"level": "L2", "description": "Geocoded unwrapped interferogram (displacement)"},
    "GOFF": {"level": "L2", "description": "Geocoded pixel offsets (cryosphere)"},
    "RSLC": {"level": "L1", "description": "Range-Doppler single look complex"},
    "RIFG": {"level": "L1", "description": "Range-Doppler wrapped interferogram"},
    "ROFF": {"level": "L1", "description": "Range-Doppler pixel offsets"},
    "RUNW": {"level": "L1", "description": "Range-Doppler unwrapped interferogram"},
    "SME2": {"level": "L3", "description": "Soil moisture (EASE-Grid 2.0)"},
}


@dataclass(frozen=True)
class SearchQuery:
    """Parameters shared by the search, download, and metadata commands."""

    product: str = DEFAULT_PRODUCT
    maturity: str = DEFAULT_MATURITY
    region: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    wkt: str | None = None
    start: str | None = None
    end: str | None = None
    orbit: int | None = None
    frame: int | None = None
    polarization: str | None = None
    frame_coverage: str | None = None
    max_results: int | None = None

    def __post_init__(self) -> None:
        product = self.product.upper()
        if product not in KNOWN_PRODUCTS:
            raise ValueError(
                f"unknown product {self.product!r}; choose one of: " + ", ".join(KNOWN_PRODUCTS)
            )
        object.__setattr__(self, "product", product)

        maturity = self.maturity.upper()
        if maturity not in MATURITIES:
            raise ValueError(
                f"unknown maturity {self.maturity!r}; choose one of: " + ", ".join(MATURITIES)
            )
        object.__setattr__(self, "maturity", maturity)

        if self.max_results is not None and self.max_results < 1:
            raise ValueError("max_results must be >= 1")
        if self.frame_coverage is not None:
            coverage = self.frame_coverage.upper()
            if coverage not in ("FULL", "PARTIAL"):
                raise ValueError("frame_coverage must be FULL or PARTIAL")
            object.__setattr__(self, "frame_coverage", coverage)

    def aoi_wkt(self) -> str:
        if self.wkt:
            return self.wkt
        if self.bbox:
            return regions.bbox_to_wkt(*self.bbox)
        return regions.get_region(self.region or regions.DEFAULT_REGION).to_wkt()

    def aoi_label(self) -> str:
        if self.wkt:
            return "custom-wkt"
        if self.bbox:
            return "custom-bbox"
        return self.region or regions.DEFAULT_REGION

    def asf_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "dataset": "NISAR",
            "processingLevel": self.product,
            "dataMaturity": self.maturity,
            "intersectsWith": self.aoi_wkt(),
        }
        if self.start:
            kwargs["start"] = self.start
        if self.end:
            kwargs["end"] = self.end
        if self.orbit is not None:
            kwargs["relativeOrbit"] = self.orbit
        if self.frame is not None:
            kwargs["frame"] = self.frame
        if self.polarization:
            kwargs["mainBandPolarization"] = self.polarization.upper()
        if self.frame_coverage:
            kwargs["frameCoverage"] = self.frame_coverage
        if self.max_results is not None:
            kwargs["maxResults"] = self.max_results
        return kwargs

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def count(query: SearchQuery) -> int:
    kwargs = query.asf_kwargs()
    kwargs.pop("maxResults", None)
    return int(asf.search_count(**kwargs))


def search(query: SearchQuery) -> asf.ASFSearchResults:
    return asf.search(**query.asf_kwargs())


def maturity_of(product: Any) -> str:
    collection = str((getattr(product, "properties", {}) or {}).get("collectionName", ""))
    if "PROVISIONAL" in collection.upper():
        return "PROVISIONAL"
    if "BETA" in collection.upper():
        return "BETA"
    return "VALIDATED"


def summarize(product: Any) -> dict[str, Any]:
    props = getattr(product, "properties", {}) or {}
    urls = product.get_urls() if hasattr(product, "get_urls") else []
    url = urls[0] if urls else props.get("url")
    return {
        "scene": props.get("sceneName") or props.get("fileID"),
        "product": props.get("processingLevel"),
        "maturity": maturity_of(product),
        "collection": props.get("collectionName"),
        "start": props.get("startTime"),
        "stop": props.get("stopTime"),
        "polarizations": props.get("mainBandPolarization") or props.get("polarization"),
        "flight_direction": props.get("flightDirection"),
        "path": props.get("pathNumber"),
        "frame": props.get("frameNumber"),
        "frame_coverage": props.get("frameCoverage"),
        "bytes": granule_bytes(product),
        "url": url,
    }


def available_products(
    query: SearchQuery, maturities: tuple[str, ...] = (DEFAULT_MATURITY,)
) -> list[dict[str, Any]]:
    """Count NISAR granules per product and maturity for an AOI/time range."""
    rows: list[dict[str, Any]] = []
    for product, meta in KNOWN_PRODUCTS.items():
        for maturity in maturities:
            probe = SearchQuery(
                product=product,
                maturity=maturity,
                region=query.region,
                bbox=query.bbox,
                wkt=query.wkt,
                start=query.start,
                end=query.end,
                orbit=query.orbit,
                frame=query.frame,
                polarization=query.polarization,
                frame_coverage=query.frame_coverage,
            )
            try:
                n = count(probe)
            except Exception as exc:  # noqa: BLE001  # pragma: no cover - network dependent
                rows.append(
                    {
                        "product": product,
                        "maturity": maturity,
                        "level": meta["level"],
                        "description": meta["description"],
                        "count": None,
                        "error": str(exc),
                    }
                )
                continue
            rows.append(
                {
                    "product": product,
                    "maturity": maturity,
                    "level": meta["level"],
                    "description": meta["description"],
                    "count": n,
                    "error": None,
                }
            )
    return rows


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
