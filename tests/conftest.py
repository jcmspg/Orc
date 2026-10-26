import pytest

from orc import credentials


@pytest.fixture(autouse=True)
def no_real_credentials(tmp_path, monkeypatch):
    """Never read or write the user's real ~/.config/orc/env, or inherit their tokens."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ORC_SESSION_TOKEN", raising=False)
    for name in credentials.KNOWN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "_sources", {})
