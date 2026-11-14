"""Command line interface for nisar-fetch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import auth, budget, regions, search
from .download import NoResults, download_products
from .inspect import describe_tree, inspect

PRODUCTS = list(search.KNOWN_PRODUCTS)


def _normalize_bbox_args(argv: list[str]) -> list[str]:
    """Allow `--bbox -80.6,43.4,...` without argparse mistaking it for a flag."""
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--bbox" and index + 1 < len(argv):
            value = argv[index + 1]
            if value.startswith("-") and "," in value:
                normalized.append(f"--bbox={value}")
                index += 2
                continue
        normalized.append(token)
        index += 1
    return normalized


class _ArgumentParser(argparse.ArgumentParser):
    def parse_known_args(
        self, args: list[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        argv = sys.argv[1:] if args is None else args
        return super().parse_known_args(_normalize_bbox_args(list(argv)), namespace)


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected W,S,E,N (four comma-separated numbers)")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox values must be numbers") from exc
    try:
        regions.bbox_to_wkt(west, south, east, north)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return (west, south, east, north)


def add_query_args(parser: argparse.ArgumentParser, *, include_product: bool = True) -> None:
    if include_product:
        parser.add_argument(
            "--product",
            default=search.DEFAULT_PRODUCT,
            choices=PRODUCTS,
            help=f"NISAR product type (default: {search.DEFAULT_PRODUCT})",
        )
    parser.add_argument(
        "--maturity",
        default=search.DEFAULT_MATURITY,
        choices=search.MATURITIES,
        help=f"data maturity (default: {search.DEFAULT_MATURITY})",
    )
    aoi = parser.add_mutually_exclusive_group()
    aoi.add_argument(
        "--region",
        choices=sorted(regions.REGIONS),
        help=f"named AOI (default: {regions.DEFAULT_REGION})",
    )
    aoi.add_argument("--bbox", type=parse_bbox, help="custom W,S,E,N bounding box")
    aoi.add_argument("--wkt", help="custom WKT polygon in lon/lat")
    parser.add_argument("--start", help="start date, e.g. 2026-10-21")
    parser.add_argument("--end", help="end date, e.g. 2026-11-08")
    parser.add_argument("--orbit", type=int, help="relative orbit / path number")
    parser.add_argument("--frame", type=int, help="frame number")
    parser.add_argument("--polarization", choices=["HH", "HV", "VH", "VV"])
    parser.add_argument(
        "--frame-coverage", choices=["FULL", "PARTIAL"], help="NISAR frame coverage"
    )


def build_query(args: argparse.Namespace, *, max_results: int | None = None) -> search.SearchQuery:
    region = getattr(args, "region", None)
    bbox = getattr(args, "bbox", None)
    wkt = getattr(args, "wkt", None)
    if region is None and bbox is None and wkt is None:
        region = regions.DEFAULT_REGION
    return search.SearchQuery(
        product=args.product,
        maturity=args.maturity,
        region=region,
        bbox=bbox,
        wkt=wkt,
        start=args.start,
        end=args.end,
        orbit=args.orbit,
        frame=args.frame,
        polarization=args.polarization,
        frame_coverage=args.frame_coverage,
        max_results=max_results,
    )


def _print_header(query: search.SearchQuery) -> None:
    print(
        f"NISAR {query.product} ({query.maturity}) | AOI: {query.aoi_label()} | "
        f"{query.start or 'any'} -> {query.end or 'now'}"
    )


def cmd_auth(args: argparse.Namespace) -> int:
    creds = auth.get_credentials()
    print(f"Found Earthdata Login credentials for {creds.username!r}.")
    auth.get_session()
    print("Authenticated ASF session created successfully.")
    return 0


def cmd_products(args: argparse.Namespace) -> int:
    query = build_query(args)
    maturities = search.MATURITIES if args.all_maturities else (query.maturity,)
    _print_header(query)
    rows = search.available_products(query, maturities)
    print(f"{'PRODUCT':8} {'MATURITY':12} {'LEVEL':5} {'COUNT':>8}  DESCRIPTION")
    for row in rows:
        count = "n/a" if row["count"] is None else f"{row['count']:,}"
        print(
            f"{row['product']:8} {row['maturity']:12} {row['level']:5} "
            f"{count:>8}  {row['description']}"
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    query = build_query(args)
    total = search.count(query)
    _print_header(query)

    if total == 0:
        print("No granules matched.")
        return 1

    fetch_n = min(total, max(args.max_results, 50))
    products = search.search(replace(query, max_results=fetch_n))
    if total <= len(products):
        size = budget.total_bytes(products)
        size_note = "exact"
    else:
        size = budget.estimate_total_bytes(products, total)
        size_note = "estimated"

    starts = [p.properties.get("startTime") for p in products if p.properties.get("startTime")]
    stops = [p.properties.get("stopTime") for p in products if p.properties.get("stopTime")]
    if starts and stops:
        print(f"Time span: {min(starts)} -> {max(stops)}")
    print(f"Matched: {total:,} granule(s), ~{budget.human_gb(size)} ({size_note})")

    print(f"\n{'START':20} {'PRODUCT':8} {'POL':10} {'SIZE':>10}  SCENE")
    for product in products[: args.max_results]:
        info = search.summarize(product)
        polarizations = ",".join(info["polarizations"] or []) or "-"
        print(
            f"{str(info['start'])[:19]:20} {info['product']!s:8} "
            f"{polarizations:10} {budget.human_gb(info['bytes']):>10}  {info['scene']}"
        )

    if args.json:
        payload = {
            "query": {
                **query.to_dict(),
                "aoi_label": query.aoi_label(),
                "aoi_wkt": query.aoi_wkt(),
            },
            "count": total,
            "estimated_bytes": size,
            "size_note": size_note,
            "granules": [search.summarize(product) for product in products],
        }
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {path}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    query = build_query(args, max_results=args.limit)
    total = search.count(query)
    _print_header(query)

    if total == 0:
        print("No granules matched.")
        return 1

    products = search.search(query)
    if not products:
        raise NoResults("query matched no granules")
    size = budget.total_bytes(products)

    print(
        f"Matched {total:,} granule(s); selected {len(products)} "
        f"for download (~{budget.human_gb(size)})."
    )
    budget.enforce(
        len(products),
        size,
        limit=args.limit,
        max_gb=args.max_gb,
        force=args.force,
    )

    if args.dry_run:
        print("\nDry run - nothing will be downloaded:")
        for product in products:
            info = search.summarize(product)
            print(f"  {info['scene']}  {budget.human_gb(info['bytes'])}")
        return 0

    out_dir = Path(args.out) if args.out else Path("data") / query.aoi_label()
    manifest = download_products(
        products,
        query,
        out_dir=out_dir,
        max_gb=args.max_gb,
        workers=args.workers,
        force=args.force,
    )
    print(f"\nDownloaded {manifest['count']} granule(s) to {out_dir}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    info, datatree = inspect(
        granule=args.granule,
        url=args.url,
        block_size_mb=args.block_size_mb,
    )
    if info:
        print(f"Scene: {info['scene']}")
        print(f"Start: {info['start']}  Stop: {info['stop']}")
        print(f"Product: {info['product']} ({info['maturity']})")
        print(f"Size: {budget.human_gb(info['bytes'])}")
        print(f"URL: {info['url']}\n")
    print(describe_tree(datatree))
    return 0


def cmd_map_prep(args: argparse.Namespace) -> int:
    from .map.prep import find_input, prepare_granule

    input_path = find_input(Path(args.data_dir), Path(args.input) if args.input else None)
    region = regions.REGIONS[args.region]
    metadata = prepare_granule(
        input_path,
        Path(args.out),
        step=args.step,
        force=args.force,
        aoi_name=args.region,
        aoi_bbox=region.bbox,
        aoi_label=region.description,
    )
    print(f"Prepared {metadata['scene']}")
    print(f"Output directory: {args.out}")
    print(f"Bounds (WGS84): {metadata['bounds_wgs84']}")
    for name, layer in metadata["layers"].items():
        if layer.get("rescale"):
            low, high = layer["rescale"]
            print(f"  {name}: {low:+.2f} .. {high:+.2f} {layer.get('unit', '')}")
    print(f"Metadata: {Path(args.out) / 'metadata.json'}")
    return 0


def cmd_lake_prep(args: argparse.Namespace) -> int:
    from .map.lake import prepare_lake

    metadata = prepare_lake(
        Path(args.radar),
        Path(args.temperature),
        Path(args.blooms),
        Path(args.outline),
        Path(args.out),
    )
    for name, config in metadata["layers"].items():
        print(f"{name}: {config['start']} · {config['coverage_percent']}% lake coverage")
    print(f"Prepared Lake Erie layers in {args.out}")
    return 0


def cmd_map_serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ["NISAR_MAP_DIR"] = str(Path(args.map_dir).resolve())
    os.environ["NISAR_WEB_DIR"] = str(Path(args.web_dir).resolve())
    uvicorn.run(
        "nisar_fetch.map.webapp:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="nisar-fetch",
        description="Search and download NASA NISAR L-band products from the ASF DAAC.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser(
        "auth", help="verify Earthdata Login credentials without downloading"
    )
    auth_parser.set_defaults(func=cmd_auth)

    products_parser = subparsers.add_parser(
        "products", help="show available NISAR product types and granule counts"
    )
    add_query_args(products_parser, include_product=False)
    products_parser.set_defaults(product=search.DEFAULT_PRODUCT)
    products_parser.add_argument(
        "--all-maturities",
        action="store_true",
        help="count VALIDATED, PROVISIONAL, and BETA collections",
    )
    products_parser.set_defaults(func=cmd_products)

    search_parser = subparsers.add_parser("search", help="search and preview granules")
    add_query_args(search_parser)
    search_parser.add_argument(
        "--max-results", type=int, default=10, help="rows to display (default: 10)"
    )
    search_parser.add_argument("--json", help="write results to this JSON manifest")
    search_parser.set_defaults(func=cmd_search)

    download_parser = subparsers.add_parser("download", help="download granules")
    add_query_args(download_parser)
    download_parser.add_argument(
        "--limit", type=int, default=1, help="maximum granules to download (default: 1)"
    )
    download_parser.add_argument(
        "--max-gb",
        type=float,
        default=10.0,
        help="refuse downloads larger than this many GB (default: 10)",
    )
    download_parser.add_argument("--out", help="output directory (default: data/<aoi>)")
    download_parser.add_argument(
        "--workers", type=int, default=1, help="parallel downloads (default: 1)"
    )
    download_parser.add_argument(
        "--force", action="store_true", help="override --limit/--max-gb safety checks"
    )
    download_parser.add_argument(
        "--dry-run", action="store_true", help="show what would be downloaded"
    )
    download_parser.set_defaults(func=cmd_download)

    inspect_parser = subparsers.add_parser(
        "inspect", help="stream a granule and list its HDF5 groups/variables"
    )
    source = inspect_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--granule", help="NISAR scene/granule name")
    source.add_argument("--url", help="direct HTTPS URL to an HDF5 file")
    inspect_parser.add_argument(
        "--block-size-mb", type=int, default=16, help="streaming block size (default: 16)"
    )
    inspect_parser.set_defaults(func=cmd_inspect)

    map_prep_parser = subparsers.add_parser(
        "map-prep", help="convert a GCOV or SME2 granule into map-ready COGs"
    )
    map_prep_parser.add_argument(
        "--data-dir", default="data/toronto", help="directory containing .h5 granules"
    )
    map_prep_parser.add_argument("--input", help="explicit path to a .h5 granule")
    map_prep_parser.add_argument("--out", default="data/map", help="COG output directory")
    map_prep_parser.add_argument(
        "--step",
        type=int,
        default=4,
        help="GCOV decimation factor (default: 4); SME2 always keeps native spacing",
    )
    map_prep_parser.add_argument(
        "--region",
        default=regions.DEFAULT_REGION,
        choices=sorted(regions.REGIONS),
        help=f"AOI used for the coverage overlay (default: {regions.DEFAULT_REGION})",
    )
    map_prep_parser.add_argument(
        "--force", action="store_true", help="rebuild existing COGs and metadata"
    )
    map_prep_parser.set_defaults(func=cmd_map_prep)

    lake_parser = subparsers.add_parser(
        "lake-prep", help="prepare Lake Erie radar, temperature and bloom detections"
    )
    lake_parser.add_argument(
        "--radar", required=True, help="local SME2 HDF5 with sigma0HH radar input"
    )
    lake_parser.add_argument(
        "--temperature", required=True, help="NOAA GLSEA latitude/longitude NetCDF subset"
    )
    lake_parser.add_argument("--blooms", required=True, help="NOAA NCCOS CI-CIcyano bloom GeoTIFF")
    lake_parser.add_argument("--outline", default="web/lake-erie.geojson")
    lake_parser.add_argument("--out", default="data/lake-erie")
    lake_parser.set_defaults(func=cmd_lake_prep)

    map_serve_parser = subparsers.add_parser(
        "map-serve", help="serve the COG tile API and MapLibre frontend"
    )
    map_serve_parser.add_argument(
        "--map-dir", default="data/lake-erie", help="prepared COG directory"
    )
    map_serve_parser.add_argument("--web-dir", default="web", help="static frontend directory")
    map_serve_parser.add_argument("--host", default="127.0.0.1")
    map_serve_parser.add_argument("--port", type=int, default=8000)
    map_serve_parser.add_argument("--reload", action="store_true", help="reload on code changes")
    map_serve_parser.set_defaults(func=cmd_map_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (auth.AuthError, budget.BudgetExceeded, NoResults, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
