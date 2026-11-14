import argparse

import pytest

from nisar_fetch import cli, regions


def test_parse_bbox_valid() -> None:
    assert cli.parse_bbox("-80.6,43.4,-78.8,44.2") == (-80.6, 43.4, -78.8, 44.2)


@pytest.mark.parametrize("value", ["1,2,3", "a,b,c,d", "-78.8,43.4,-80.6,44.2"])
def test_parse_bbox_invalid(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_bbox(value)


def test_build_query_defaults_to_toronto() -> None:
    args = cli.build_parser().parse_args(["search"])
    query = cli.build_query(args)
    assert query.region == regions.DEFAULT_REGION
    assert query.product == "GCOV"
    assert query.maturity == "VALIDATED"


def test_build_query_honors_bbox() -> None:
    args = cli.build_parser().parse_args(["search", "--bbox", "-80.6,43.4,-78.8,44.2"])
    query = cli.build_query(args, max_results=1)
    assert query.bbox == (-80.6, 43.4, -78.8, 44.2)
    assert query.region is None
    assert query.max_results == 1


def test_region_and_bbox_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["search", "--region", "toronto", "--bbox", "-80.6,43.4,-78.8,44.2"]
        )


def test_help_exits_cleanly() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_auth_command_reports_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli.auth, "get_credentials", lambda: cli.auth.Credentials("alice", "secret")
    )
    monkeypatch.setattr(cli.auth, "get_session", lambda: object())
    assert cli.main(["auth"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "successfully" in out


def test_auth_command_failure_exits_2(monkeypatch, capsys) -> None:
    def fail() -> None:
        raise cli.auth.AuthError("bad credentials")

    monkeypatch.setattr(cli.auth, "get_credentials", fail)
    assert cli.main(["auth"]) == 2
    assert "bad credentials" in capsys.readouterr().err


def test_products_command_has_no_product_filter() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["products", "--product", "GCOV"])
