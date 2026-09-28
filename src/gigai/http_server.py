"""HTTP server classes that bind without a reverse-DNS lookup.

Stock ``HTTPServer.server_bind`` sets ``server_name`` to
``socket.getfqdn(host)`` after ``bind()`` and before ``listen()``, so the port
refuses every connection for as long as the host's resolver takes. On GitHub's
macOS runners that lookup of ``127.0.0.1`` blocks for more than 30 s
(actions/setup-python#1223). Every HTTP server in the product is built from
the classes here. Nothing in the product reads ``server_name`` (stdlib only
uses it for CGI), so it is the bound host.
"""

from __future__ import annotations

from http.server import HTTPServer, ThreadingHTTPServer
from socketserver import TCPServer


class NoLookupHTTPServer(HTTPServer):
    """An ``HTTPServer`` that binds without a reverse-DNS lookup."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class NoLookupThreadingHTTPServer(NoLookupHTTPServer, ThreadingHTTPServer):
    """A ``ThreadingHTTPServer`` that binds without a reverse-DNS lookup."""


__all__ = ["NoLookupHTTPServer", "NoLookupThreadingHTTPServer"]
