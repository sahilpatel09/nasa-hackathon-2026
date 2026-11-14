import xarray as xr

from nisar_fetch.inspect import describe_tree


def test_describe_tree_lists_groups_and_variables() -> None:
    datatree = xr.DataTree.from_dict(
        {
            "/": xr.Dataset({"root_var": ("x", [1, 2, 3])}),
            "/science": xr.Dataset({"science_var": ("y", [4, 5])}),
        }
    )
    text = describe_tree(datatree)
    assert "/" in text
    assert "/science" in text
    assert "root_var" in text
    assert "science_var" in text
