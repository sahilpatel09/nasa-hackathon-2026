"""FastAPI tile server for NISAR raster map layers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from rio_tiler.colormap import cmap
from rio_tiler.errors import PointOutsideBounds, TileOutsideBounds
from rio_tiler.io import Reader

DEFAULT_MAP_DIR = Path(os.environ.get("NISAR_MAP_DIR", "data/lake-erie"))
DEFAULT_WEB_DIR = Path(os.environ.get("NISAR_WEB_DIR", "web"))


def _metadata(map_dir: Path) -> dict[str, Any]:
    path = map_dir / "metadata.json"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="map data is not prepared; run `nisar-fetch map-prep` first",
        )
    return json.loads(path.read_text())


def _layer(metadata: dict[str, Any], layer: str) -> dict[str, Any]:
    layers = metadata.get("layers", {})
    if layer not in layers:
        raise HTTPException(status_code=404, detail=f"unknown layer: {layer}")
    return layers[layer]


def _layer_path(app: FastAPI, layer: dict[str, Any]) -> Path:
    path = app.state.map_dir / layer["path"]
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"missing raster: {path.name}")
    return path


def create_app(
    map_dir: Path | str | None = None,
    web_dir: Path | str | None = None,
) -> FastAPI:
    map_path = Path(map_dir).resolve() if map_dir else DEFAULT_MAP_DIR.resolve()
    web_path = Path(web_dir).resolve() if web_dir else DEFAULT_WEB_DIR.resolve()
    if not web_path.is_dir():
        web_path = None

    app = FastAPI(title="NISAR Map", version="0.1.0")
    app.state.map_dir = map_path
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if web_path is not None:
        app.mount("/static", StaticFiles(directory=str(web_path)), name="static")

    @app.get("/")
    def index() -> Response:
        if web_path is not None and (web_path / "index.html").is_file():
            return FileResponse(web_path / "index.html")
        return Response(
            content=json.dumps(
                {
                    "service": "nisar-fetch map",
                    "map_dir": str(map_path),
                    "hint": "run `nisar-fetch map-prep` and open the static frontend",
                }
            ),
            media_type="application/json",
        )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "prepared": (map_path / "metadata.json").is_file(),
        }

    @app.get("/api/granule")
    def granule() -> dict[str, Any]:
        return _metadata(map_path)

    def tile_url(name: str, config: dict[str, Any]) -> str:
        raster = map_path / config["path"]
        stamp = raster.stat().st_mtime_ns if raster.exists() else 0
        version = hashlib.sha256(
            (json.dumps(config, sort_keys=True) + str(stamp)).encode()
        ).hexdigest()[:12]
        return f"/tiles/{name}/{{z}}/{{x}}/{{y}}.png?v={version}"

    @app.get("/api/layers")
    def layers() -> list[dict[str, Any]]:
        metadata = _metadata(map_path)
        return [
            {
                "id": name,
                **config,
                "tile_url": tile_url(name, config),
                "tile_size": 256,
            }
            for name, config in metadata.get("layers", {}).items()
        ]

    @app.get("/tiles/{layer}/{z}/{x}/{y}.png")
    def tile(layer: str, z: int, x: int, y: int) -> Response:
        metadata = _metadata(map_path)
        config = _layer(metadata, layer)
        path = _layer_path(app, config)
        try:
            with Reader(str(path)) as src:
                if config.get("type") == "rgb":
                    image = src.tile(x, y, z, tilesize=256, indexes=[1, 2, 3])
                    content = image.render(add_mask=True)
                else:
                    image = src.tile(x, y, z, tilesize=256, indexes=1)
                    low, high = config["rescale"]
                    if config.get("render_encoding") == "noaa_ci":
                        image.array = np.ma.rint(
                            (np.ma.log10(np.ma.masked_less_equal(image.array, 0)) + 4.2) * 250 / 3
                        )
                        image.alpha_mask = None
                        low, high = 0, 255
                    elif config.get("scale") == "log":
                        image.array = np.ma.log10(np.ma.masked_less_equal(image.array, 0))
                        # Recompute alpha from the mask, including non-positive values.
                        image.alpha_mask = None
                        low, high = np.log10([low, high])
                    image.rescale(in_range=[(low, high)])
                    color_map = cmap.get(config["colormap"]) if config.get("colormap") else None
                    if config.get("colormap_entries"):
                        color_map = {
                            int(k): tuple(v) for k, v in config["colormap_entries"].items()
                        }
                    content = image.render(add_mask=True, colormap=color_map)
        except TileOutsideBounds as exc:
            raise HTTPException(status_code=404, detail="tile is outside the raster") from exc
        return Response(
            content=content,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/point")
    def point(layer: str, lon: float, lat: float) -> dict[str, Any]:
        metadata = _metadata(map_path)
        config = _layer(metadata, layer)
        path = _layer_path(app, config)
        indexes: int | list[int] = [1, 2, 3] if config.get("type") == "rgb" else 1
        try:
            with Reader(str(path)) as src:
                sample = src.point(lon, lat, indexes=indexes)
        except PointOutsideBounds as exc:
            raise HTTPException(status_code=404, detail="point is outside the raster") from exc
        values = [float(value) if np.isfinite(value) else None for value in sample.data]
        valid = [not bool(value) for value in np.atleast_1d(sample.array.mask)]
        payload: dict[str, Any] = {
            "layer": layer,
            "lon": lon,
            "lat": lat,
            "values": values,
            "mask": [1 if value else 0 for value in valid],
        }
        if config.get("type") != "rgb":
            value = values[0] if values and valid[0] else None
            payload["value"] = value if value is not None and np.isfinite(value) else None
            payload["unit"] = config.get("unit", "dB" if layer in ("hh", "hv") else "")
            if config.get("status_path"):
                if payload["value"] is not None:
                    payload["status"] = "bloom_signal"
                else:
                    with Reader(str(map_path / config["status_path"])) as status_src:
                        status_sample = status_src.point(lon, lat, indexes=1)
                    code = float(status_sample.data[0])
                    payload["status"] = {
                        0: "no_bloom_detected",
                        251: "invalid_observation",
                        252: "land",
                        253: "cloud",
                        254: "mixed_pixel",
                        255: "no_observation",
                    }.get(code, "no_observation")
        return payload

    return app


app = create_app()
