"""Stream a remote NISAR HDF5 file and describe its contents."""

from __future__ import annotations

from typing import Any

import aiohttp
import asf_search as asf
import fsspec
import xarray as xr

from . import auth
from .search import summarize


def resolve_granule(granule: str) -> Any:
    results = asf.granule_search([granule])
    if not results:
        raise LookupError(f"granule not found: {granule}")
    return results[0]


def open_datatree(url: str, *, block_size_mb: int = 16) -> xr.DataTree:
    creds = auth.get_credentials()
    filesystem = fsspec.filesystem(
        "https",
        client_kwargs={"auth": aiohttp.BasicAuth(creds.username, creds.password)},
    )
    handle = filesystem.open(
        url,
        cache_type="background",
        block_size=block_size_mb * 1024 * 1024,
    )
    return xr.open_datatree(
        handle,
        engine="h5netcdf",
        decode_timedelta=False,
        phony_dims="access",
    )


def describe_tree(datatree: xr.DataTree) -> str:
    """Return a readable list of HDF5 groups and variables."""
    lines: list[str] = []
    groups = list(getattr(datatree, "groups", ()) or ())
    if "/" not in groups:
        groups = ["/", *groups]

    for group in groups:
        lines.append(str(group))
        node = datatree[group]
        for name, variable in getattr(node, "variables", {}).items():
            lines.append(
                f"    {name}  dims={tuple(variable.dims)} "
                f"shape={tuple(variable.shape)} dtype={variable.dtype}"
            )
    return "\n".join(lines)


def inspect(
    *,
    granule: str | None = None,
    url: str | None = None,
    block_size_mb: int = 16,
) -> tuple[dict[str, Any] | None, xr.DataTree]:
    if not granule and not url:
        raise ValueError("provide either a granule name or a URL")

    info = None
    if granule:
        product = resolve_granule(granule)
        info = summarize(product)
        url = info["url"]
    assert url is not None
    return info, open_datatree(url, block_size_mb=block_size_mb)
