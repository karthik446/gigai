"""A request built from a STORED posting URL never reaches this machine or its network. (0.1.11.4 S2)

A posting's URL is the board's text, written by strangers. A request to it
(the company-page check, 7b) could be steered at an address the operator's
machine can reach and the internet cannot: ``127.0.0.1``, a ``10.x`` host,
the cloud metadata address ``169.254.169.254``. A name that LOOKS public can
resolve to any of them, and a name can answer one address to a check and
another to the connection that follows (DNS rebinding).

:func:`safe_public_target` is the one gate:

1. the URL is plain ``https`` at a public-looking name: default port, no
   login, no address literal, not ``localhost`` or a private suffix;
2. the name is resolved ONCE (``socket.getaddrinfo``, IPv4 and IPv6, at most
   :data:`RESOLVE_TIMEOUT_SECONDS`); no address, or ANY address that is not a
   public one (:func:`is_public_address`), and there is no target;
3. the target carries the vetted address, and :func:`pinned_stream` connects
   to THAT address: the request's URL names the address, so nothing resolves
   the name a second time. The ``Host`` header and the TLS server name
   (``sni_hostname``) stay the posting's host, so the certificate is still
   verified against the real name.

No redirect is followed: the next hop would be an unvetted name.

:func:`safe_path_segment` is the rule for a board token or a posting id that
becomes one path segment of a board's API address: it can never add a
segment, a query or a login to that address.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import socket
import threading
from typing import TYPE_CHECKING, ContextManager
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx


#: The one name lookup of a target waits this long at most.
RESOLVE_TIMEOUT_SECONDS = 3.0

_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".test", ".invalid")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
#: The catalog's own rule for a board token (``company_catalog._SAFE_TOKEN``): it starts with a letter or digit, so it is never ``.`` or ``..``.
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class PublicTarget:
    """A stored URL that may be asked: its host, and the ONE vetted address the request connects to."""

    url: str
    host: str
    address: str

    @property
    def pinned_url(self) -> str:
        """``url`` with the vetted address in the host's place (nothing is left to resolve)."""

        parsed = urlsplit(self.url)
        netloc = f"[{self.address}]" if ":" in self.address else self.address
        return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def safe_path_segment(value: object) -> bool:
    """True when ``value`` is a plain name (``[A-Za-z0-9._-]``, starting with a letter or digit) safe as ONE path segment."""

    return type(value) is str and _SAFE_SEGMENT.fullmatch(value) is not None


def public_looking_host(url: str) -> str | None:
    """The host of a plain ``https`` URL at a public-looking name, else ``None``. No lookup is made."""

    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or port not in (None, 443) or parsed.username is not None or parsed.password is not None:
        return None
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    if "." not in host or host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
        return None
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def _public(ip: _Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _public(ip.ipv4_mapped)
        # 6to4 and NAT64 carry an IPv4 address inside (where the packet ends up): no posting's site needs one, none is asked.
        if ip.sixtofour is not None or ip in _NAT64 or ip.is_site_local:
            return False
    elif ip in _CGNAT:
        return False
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return ip.is_global


def is_public_address(address: str) -> bool:
    """True only for an address on the public internet.

    Not loopback, private (``10/8``, ``172.16/12``, ``192.168/16``),
    link-local (``169.254/16``, ``fe80::/10``), CGNAT (``100.64/10``),
    unique-local (``fc00::/7``), unspecified, multicast or reserved; an
    IPv4-mapped IPv6 address is judged as its IPv4 one, and a 6to4 or NAT64
    address (an IPv4 address inside an IPv6 one) is never public here.
    """

    try:
        ip = ipaddress.ip_address(address.partition("%")[0])
    except (AttributeError, ValueError):
        return False
    return _public(ip)


def resolve_host(host: str, *, timeout: float | None = None) -> tuple[str, ...]:
    """Every address ``host`` resolves to (IPv4 and IPv6), in the resolver's order; ``()`` when it gives none in time. ONE lookup."""

    found: list[str] = []
    done = threading.Event()

    def look_up() -> None:
        try:
            for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
                address = info[4][0]
                if type(address) is str and address not in found:
                    found.append(address)
        except (OSError, UnicodeError, ValueError):
            found.clear()
        finally:
            done.set()

    # ``getaddrinfo`` has no timeout of its own: a resolver that hangs is left behind on a daemon thread.
    threading.Thread(target=look_up, name="gigai-outbound-resolve", daemon=True).start()
    if not done.wait(RESOLVE_TIMEOUT_SECONDS if timeout is None else timeout):
        return ()
    return tuple(found)


def safe_public_target(url: str) -> PublicTarget | None:
    """The vetted target of a request to the stored ``url``, or ``None`` when it must not be asked. Never raises.

    ``None``: not a plain ``https`` URL at a public-looking name, the name
    gave no address, or ANY of its addresses is not public (one private
    answer among public ones refuses the lot).
    """

    host = public_looking_host(url) if type(url) is str else None
    if host is None:
        return None
    addresses = resolve_host(host)
    if not addresses or not all(is_public_address(address) for address in addresses):
        return None
    return PublicTarget(url=url, host=host, address=addresses[0].partition("%")[0])


def pinned_stream(client: "httpx.Client", target: PublicTarget, method: str = "GET") -> "ContextManager[httpx.Response]":
    """``client.stream`` to the target's vetted ADDRESS: no second lookup, no redirect, the certificate checked against the real host."""

    return client.stream(
        method,
        target.pinned_url,
        headers={"Host": target.host},
        extensions={"sni_hostname": target.host},
        follow_redirects=False,
    )


__all__ = [
    "PublicTarget",
    "RESOLVE_TIMEOUT_SECONDS",
    "is_public_address",
    "pinned_stream",
    "public_looking_host",
    "resolve_host",
    "safe_path_segment",
    "safe_public_target",
]
