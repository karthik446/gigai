"""A name resolver for tests: ``socket.getaddrinfo`` answers from a table, and no name lookup leaves the process.

``install_fake_dns(monkeypatch, {"www.example-co.com": ["93.184.216.34"]})`` replaces ``socket.getaddrinfo`` for the
test. A name in the table gets its addresses; a name that is not gets ``default`` (``None``: "no such host"). A table
value may be a function of the lookup's number for that name (0, 1, ...), for a name that changes its answer. An
address literal and ``localhost`` (the test's own in-process server) go to the real function, which needs no network
for them. ``calls`` lists every NAME that was looked up, in order.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable, Mapping

import pytest

#: A public address no test ever connects to (the transport is a fake one).
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"

Answers = Iterable[str] | Callable[[int], Iterable[str]]


class FakeDNS:
    def __init__(self, table: Mapping[str, Answers] | None = None, *, default: Iterable[str] | None = None) -> None:
        self.table: dict[str, Answers] = dict(table or {})
        self.default = None if default is None else tuple(default)
        self.calls: list[str] = []
        self._real = socket.getaddrinfo

    def __call__(self, host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else host
        if name is None or name == "localhost" or _is_literal(name):
            return self._real(host, port, *args, **kwargs)
        seen = self.calls.count(name)
        self.calls.append(name)
        answers = self.table.get(name, self.default)
        if callable(answers):
            answers = answers(seen)
        found = tuple(answers or ())
        if not found:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known (fake resolver)")
        number = port if isinstance(port, int) else 0
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, number, 0, 0)) if ":" in address
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, number))
            for address in found
        ]


def _is_literal(name: str) -> bool:
    try:
        ipaddress.ip_address(name.partition("%")[0])
    except ValueError:
        return False
    return True


def install_fake_dns(
    monkeypatch: pytest.MonkeyPatch, table: Mapping[str, Answers] | None = None, *, default: Iterable[str] | None = None
) -> FakeDNS:
    dns = FakeDNS(table, default=default)
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    return dns
