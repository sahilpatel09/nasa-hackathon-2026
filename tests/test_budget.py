import pytest

from nisar_fetch import budget


class FakeProduct:
    def __init__(self, file_name: str, primary: int, extra: int = 0) -> None:
        self.properties = {
            "fileName": file_name,
            "bytes": {
                file_name: {"bytes": primary, "format": "HDF5"},
                "browse.png": {"bytes": extra, "format": "PNG"},
            },
        }


def test_granule_bytes_uses_primary_file() -> None:
    product = FakeProduct("scene.h5", 5_989_466_112, extra=3_000_000)
    assert budget.granule_bytes(product) == 5_989_466_112


def test_granule_bytes_sums_when_primary_missing() -> None:
    product = FakeProduct("scene.h5", 100, extra=25)
    product.properties["fileName"] = "missing.h5"
    assert budget.granule_bytes(product) == 125


def test_estimate_scales_sample_average() -> None:
    products = [FakeProduct("a.h5", 100), FakeProduct("b.h5", 300)]
    assert budget.estimate_total_bytes(products, 10) == 2000


def test_enforce_rejects_over_budget() -> None:
    with pytest.raises(budget.BudgetExceeded, match="--limit"):
        budget.enforce(5, 1_000, limit=1, max_gb=10)


def test_enforce_rejects_over_max_gb() -> None:
    with pytest.raises(budget.BudgetExceeded, match="--max-gb"):
        budget.enforce(1, 11_000_000_000, max_gb=10)


def test_enforce_force_overrides() -> None:
    budget.enforce(5, 11_000_000_000, limit=1, max_gb=10, force=True)


def test_enforce_allows_within_budget() -> None:
    budget.enforce(1, 6_000_000_000, limit=1, max_gb=10)
