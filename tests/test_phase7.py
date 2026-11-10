import pytest

from core.updater import UpdateChecker, version_tuple


def test_update_checker_validates_versions_and_digest(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"version":"0.2.0","download_url":"https://example.com/setup.exe","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","notes":"Security fixes"}'
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    result = UpdateChecker("0.1.0", "https://example.com/manifest.json").check()
    assert result.available and result.version == "0.2.0"


def test_invalid_version_is_rejected():
    with pytest.raises(ValueError):
        version_tuple("1.bad.0")
