import pytest

from nisar_fetch import auth


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)


def test_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EARTHDATA_USERNAME", "alice")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "secret")
    assert auth.get_credentials() == auth.Credentials("alice", "secret")


def test_partial_env_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_env(monkeypatch)
    monkeypatch.setenv("EARTHDATA_USERNAME", "alice")
    with pytest.raises(auth.AuthError, match="both"):
        auth.get_credentials()


def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_env(monkeypatch)
    with pytest.raises(auth.AuthError, match="No Earthdata Login credentials"):
        auth.get_credentials()


def test_netrc_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_env(monkeypatch)
    netrc_path = tmp_path / ".netrc"
    netrc_path.write_text("machine urs.earthdata.nasa.gov login bob password hunter2\n")
    netrc_path.chmod(0o600)
    assert auth.get_credentials() == auth.Credentials("bob", "hunter2")


def test_netrc_without_earthdata_host(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_env(monkeypatch)
    netrc_path = tmp_path / ".netrc"
    netrc_path.write_text("machine example.com login bob password hunter2\n")
    netrc_path.chmod(0o600)
    with pytest.raises(auth.AuthError):
        auth.get_credentials()


def test_get_session_reports_underlying_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EARTHDATA_USERNAME", "alice")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "secret")

    class FailingSession:
        def auth_with_creds(self, username: str, password: str) -> None:
            raise RuntimeError("Pre authorization required for this application")

    monkeypatch.setattr(auth, "ASFSession", FailingSession)

    with pytest.raises(auth.AuthError) as exc:
        auth.get_session()

    message = str(exc.value)
    assert "Pre authorization required" in message
    assert "approve_app" in message
    assert "No Earthdata Login credentials found" not in message
