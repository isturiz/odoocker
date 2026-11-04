"""Keep automatic proxy management away from the host during unit tests."""

import pytest

from odoocker import proxy
from odoocker.errors import OdxError


@pytest.fixture(autouse=True)
def isolated_host_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "host-data"))
    monkeypatch.setattr(proxy, "running", lambda: False)

    def unavailable(*args):
        raise OdxError("Caddy unavailable in unit tests")

    monkeypatch.setattr(proxy, "caddy", unavailable)
