import pytest

from orc import credentials


@pytest.fixture(autouse=True)
def no_real_credentials(tmp_path, monkeypatch):
    """Never read or write the user's real ~/.config/orc/env, saved village or bundle cache, or inherit their tokens."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ORC_SESSION_TOKEN", raising=False)
    for name in credentials.KNOWN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "_sources", {})
