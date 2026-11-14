"""Download NISAR products to local storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import auth, budget
from .search import SearchQuery, now_utc, summarize


class NoResults(RuntimeError):
    """Raised when a query matches no granules."""


def build_manifest(
    products: Any,
    query: SearchQuery,
    out_dir: Path,
    *,
    total_bytes: int,
    max_gb: float,
    workers: int,
    force: bool,
) -> dict[str, Any]:
    files = sorted(
        str(path.relative_to(out_dir))
        for path in out_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    return {
        "generated_at": now_utc(),
        "query": {
            **query.to_dict(),
            "aoi_label": query.aoi_label(),
            "aoi_wkt": query.aoi_wkt(),
        },
        "limits": {"max_gb": max_gb, "workers": workers, "force": force},
        "count": len(products),
        "total_bytes": total_bytes,
        "total_gb": round(budget.to_gb(total_bytes), 3),
        "granules": [summarize(product) for product in products],
        "files": files,
    }


def download_products(
    products: Any,
    query: SearchQuery,
    *,
    out_dir: str | Path,
    max_gb: float,
    workers: int = 1,
    force: bool = False,
    session: Any | None = None,
) -> dict[str, Any]:
    """Download an ASFSearchResults object and write manifest.json next to it."""
    if not products:
        raise NoResults("query matched no granules")

    size = budget.total_bytes(products)
    budget.enforce(
        len(products),
        size,
        limit=query.max_results,
        max_gb=max_gb,
        force=force,
    )

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    session = session or auth.get_session()
    products.download(path=str(out_path), session=session, processes=workers)

    manifest = build_manifest(
        products,
        query,
        out_path,
        total_bytes=size,
        max_gb=max_gb,
        workers=workers,
        force=force,
    )
    (out_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
