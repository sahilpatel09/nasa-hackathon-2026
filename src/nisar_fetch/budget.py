"""Download size estimation and safety limits."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised when a download would exceed the requested limits."""


def granule_bytes(product: Any) -> int:
    """Size of the primary science file for one ASF product, in bytes."""
    props = getattr(product, "properties", {}) or {}
    file_name = props.get("fileName")
    sizes = props.get("bytes") or {}

    if file_name and isinstance(sizes, dict) and file_name in sizes:
        entry = sizes[file_name]
        if isinstance(entry, dict):
            return int(entry.get("bytes") or 0)

    total = 0
    if isinstance(sizes, dict):
        for entry in sizes.values():
            if isinstance(entry, dict):
                total += int(entry.get("bytes") or 0)
    return total


def total_bytes(products: Iterable[Any]) -> int:
    return sum(granule_bytes(product) for product in products)


def estimate_total_bytes(products: list[Any], total_count: int) -> int:
    """Estimate the full result size from a sample of products."""
    if not products or total_count <= 0:
        return 0
    average = total_bytes(products) / len(products)
    return int(average * total_count)


def to_gb(num_bytes: int) -> float:
    """Decimal gigabytes (1 GB = 1,000,000,000 bytes)."""
    return num_bytes / 1_000_000_000


def human_gb(num_bytes: int) -> str:
    return f"{to_gb(num_bytes):,.2f} GB"


def enforce(
    count: int,
    size_bytes: int,
    *,
    limit: int | None = None,
    max_gb: float | None = None,
    force: bool = False,
) -> None:
    """Raise BudgetExceeded if the requested download exceeds the caps."""
    problems: list[str] = []
    if limit is not None and count > limit:
        problems.append(f"{count} granules exceed --limit {limit}")
    if max_gb is not None and to_gb(size_bytes) > max_gb:
        problems.append(f"{human_gb(size_bytes)} exceeds --max-gb {max_gb:g}")

    if problems and not force:
        raise BudgetExceeded("; ".join(problems) + ". Lower the scope or pass --force to override.")
