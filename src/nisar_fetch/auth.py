"""Earthdata Login (EDL) credential handling."""

from __future__ import annotations

import netrc
import os
from dataclasses import dataclass

from asf_search import ASFSession

EARTHDATA_HOST = "urs.earthdata.nasa.gov"

_CREDENTIALS_HELP = (
    "No Earthdata Login credentials found. Do one of the following:\n"
    "  1. Create ~/.netrc with:\n"
    "       machine urs.earthdata.nasa.gov login <USER> password <PASSWORD>\n"
    "     then run: chmod 600 ~/.netrc\n"
    "  2. Export both environment variables:\n"
    "       export EARTHDATA_USERNAME=<USER>\n"
    "       export EARTHDATA_PASSWORD=<PASSWORD>\n"
    "Then authorize the ASF application for your account at "
    "https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g"
)

_AUTH_HELP = (
    "Check your username/password, then authorize the ASF application for your "
    "account:\n"
    "  https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g\n"
    "On that page, tick the EULA checkbox and click Authorize/Agree - merely opening "
    "the page does not approve the application. Also approve 'Alaska Satellite "
    "Facility Data Access Egress Control' under Applications -> Approve More "
    "Applications."
)


class AuthError(RuntimeError):
    """Raised when Earthdata Login credentials are missing or unusable."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def get_credentials() -> Credentials:
    """Return EDL credentials from environment variables or ~/.netrc."""
    username = os.environ.get("EARTHDATA_USERNAME")
    password = os.environ.get("EARTHDATA_PASSWORD")

    if username and password:
        return Credentials(username, password)
    if bool(username) != bool(password):
        raise AuthError(
            "Set both EARTHDATA_USERNAME and EARTHDATA_PASSWORD (or neither).\n" + _CREDENTIALS_HELP
        )

    try:
        auth = netrc.netrc().authenticators(EARTHDATA_HOST)
    except FileNotFoundError as exc:
        raise AuthError(_CREDENTIALS_HELP) from exc
    except netrc.NetrcParseError as exc:
        raise AuthError(
            f"Could not parse ~/.netrc ({exc}). Make sure it is not group/world readable "
            "(chmod 600 ~/.netrc).\n" + _CREDENTIALS_HELP
        ) from exc

    if not auth:
        raise AuthError(_CREDENTIALS_HELP)

    login, _account, netrc_password = auth
    return Credentials(login, netrc_password)


def get_session() -> ASFSession:
    """Build an authenticated asf_search session."""
    creds = get_credentials()
    session = ASFSession()
    try:
        session.auth_with_creds(creds.username, creds.password)
    except Exception as exc:  # pragma: no cover - network/credential dependent
        raise AuthError(f"Earthdata Login authentication failed: {exc}\n" + _AUTH_HELP) from exc
    return session
