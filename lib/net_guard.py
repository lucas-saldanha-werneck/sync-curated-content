"""Guarda anti-SSRF: só http/https, host resolve para IP público, redirect re-checado a cada salto com IP pinado."""
from __future__ import annotations
import http.client, ipaddress, socket, ssl, urllib.error, urllib.parse, urllib.request
from typing import Callable

BLOCKED_NETS = [ipaddress.ip_network(n) for n in
    ["0.0.0.0/8", "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10",
     "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96"]]
MAX_BYTES = 50 * 1024 * 1024

class BlockedURL(Exception): ...

def is_public_ip(ip: str) -> bool:
    try: a = ipaddress.ip_address(ip)
    except ValueError: return False
    if a.is_multicast or a.is_reserved or a.is_unspecified: return False
    return not any(a in n for n in BLOCKED_NETS)

def check_url(url: str, resolver: Callable = socket.getaddrinfo) -> tuple[str, str]:
    """Validates URL and returns (hostname, pinned_ip)."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise BlockedURL(f"esquema/host inválido: {url}")
    # Check if hostname is an IP literal and validate it directly
    try:
        ip = ipaddress.ip_address(p.hostname)
        if not is_public_ip(str(ip)):
            raise BlockedURL(f"{p.hostname} é um IP privado")
        return p.hostname, str(ip)
    except ValueError:
        pass  # Not an IP literal, continue with DNS resolution
    try:
        infos = resolver(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError as e:
        raise BlockedURL(f"DNS falhou para {p.hostname}: {e}")
    ips = {i[4][0] for i in infos}
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise BlockedURL(f"{p.hostname} resolve para IP não público: {sorted(ips)}")
    return p.hostname, next(iter(ips))

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None

class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a pinned IP."""
    def __init__(self, host, pinned_ip, *args, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, *args, **kwargs)
    def connect(self):
        sock = socket.create_connection((self.pinned_ip, self.port), timeout=self.timeout)
        self.sock = sock

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that connects to a pinned IP."""
    def __init__(self, host, pinned_ip, *args, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, *args, **kwargs)
    def connect(self):
        sock = socket.create_connection((self.pinned_ip, self.port), timeout=self.timeout)
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)

def _make_http_handler(pinned_ip: str):
    """Factory for HTTPHandler with pinned IP."""
    class _PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(lambda host, *a, **k: _PinnedHTTPConnection(host, pinned_ip, *a, **k), req)
    return _PinnedHTTPHandler()

def _make_https_handler(pinned_ip: str):
    """Factory for HTTPSHandler with pinned IP."""
    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(lambda host, *a, **k: _PinnedHTTPSConnection(host, pinned_ip, *a, **k), req)
    return _PinnedHTTPSHandler()

def safe_open(url: str, timeout: int = 30, max_redirects: int = 5, headers: dict | None = None,
              resolver: Callable | None = None, opener: Callable | None = None) -> tuple[bytes, str, dict]:
    """Fetches URL with redirect validation and DNS-rebinding protection via IP pinning."""
    res = resolver or socket.getaddrinfo
    cur = url
    for _ in range(max_redirects + 1):
        hostname, pinned_ip = check_url(cur, res)
        req = urllib.request.Request(cur, headers={"User-Agent": "sync-raindrop/1.0", **(headers or {})})

        # Use injected opener if provided (for tests), otherwise use pinned IP handlers
        if opener:
            op = opener
            try:
                with op(req, timeout) as r:
                    status = getattr(r, "status", 200)
                    if status in (301, 302, 303, 307, 308):
                        loc = r.headers.get("Location")
                        if not loc: raise BlockedURL("redirect sem Location")
                        cur = urllib.parse.urljoin(cur, loc); continue
                    body = r.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES: raise BlockedURL("resposta > 50 MB")
                    return body, cur, dict(r.headers)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                    cur = urllib.parse.urljoin(cur, e.headers["Location"]); continue
                raise
        else:
            # Use pinned IP handlers
            p = urllib.parse.urlsplit(cur)
            handlers = [_NoRedirect()]
            if p.scheme == "https":
                handlers.append(_make_https_handler(pinned_ip))
            else:
                handlers.append(_make_http_handler(pinned_ip))
            opener_obj = urllib.request.build_opener(*handlers)
            try:
                with opener_obj.open(req, timeout=timeout) as r:
                    status = getattr(r, "status", 200)
                    if status in (301, 302, 303, 307, 308):
                        loc = r.headers.get("Location")
                        if not loc: raise BlockedURL("redirect sem Location")
                        cur = urllib.parse.urljoin(cur, loc); continue
                    body = r.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES: raise BlockedURL("resposta > 50 MB")
                    return body, cur, dict(r.headers)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                    cur = urllib.parse.urljoin(cur, e.headers["Location"]); continue
                raise
    raise BlockedURL("redirects demais")
