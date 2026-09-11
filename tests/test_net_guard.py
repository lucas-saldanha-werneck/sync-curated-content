import pytest
from unittest.mock import Mock, patch
from lib.net_guard import is_public_ip, check_url, BlockedURL, safe_open, _PinnedHTTPConnection

def test_private_ips_blocked():
    for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1", "::1", "fd00::1", "fe80::1", "0.0.0.0"]:
        assert not is_public_ip(ip), ip
    assert is_public_ip("8.8.8.8") and is_public_ip("2606:4700::1111")

def test_check_url_rejects_scheme_and_private_dns():
    resolver = lambda host, *a, **k: [(None, None, None, None, ("10.0.0.5", 0))]
    with pytest.raises(BlockedURL): check_url("ftp://x.com/a")
    with pytest.raises(BlockedURL): check_url("http://internal.example/a", resolver=resolver)
    pub = lambda host, *a, **k: [(None, None, None, None, ("93.184.216.34", 0))]
    hostname, ip = check_url("https://example.com/a", resolver=pub)
    assert hostname == "example.com" and ip == "93.184.216.34"

def test_safe_open_rechecks_redirect(monkeypatch):
    seen = []
    class R:
        def __init__(self, status, headers, body=b""): self.status, self.headers, self._b = status, headers, body
        def read(self, n=-1): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(req, timeout):
        seen.append(req.full_url)
        if "first" in req.full_url: return R(302, {"Location": "http://169.254.169.254/latest"})
        return R(200, {}, b"ok")
    pub = lambda host, *a, **k: [(None, None, None, None, ("93.184.216.34", 0))]
    with pytest.raises(BlockedURL):
        safe_open("https://example.com/first", resolver=pub, opener=opener)
    assert seen == ["https://example.com/first"]           # não seguiu o redirect proibido

def test_ip_pinning():
    """Verify that connections use pinned IPs, not independent DNS resolution."""
    called_with = []

    def mock_create_connection(address, timeout=None):
        called_with.append(address)
        sock = Mock()
        return sock

    with patch('lib.net_guard.socket.create_connection', side_effect=mock_create_connection):
        conn = _PinnedHTTPConnection("example.com", "93.184.216.34")
        conn.connect()

    # Verify that socket.create_connection was called with pinned IP, not hostname
    assert called_with == [("93.184.216.34", 80)]
    # Verify that the connection still knows its hostname
    assert conn.host == "example.com"
