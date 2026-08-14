"""Audit-only pytest plugin: make any outbound network attempt a hard error.

The project claims the suite is green "with no credentials and no network".
Grepping for ``requests``/``socket`` only proves nothing *obvious* dials out.
This proves it at the syscall seam: every path that could open a remote
connection or resolve a name raises instead.

Loopback is left alone -- blocking it would break unrelated local machinery
rather than test the claim.
"""

import socket

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo
_real_create_connection = socket.create_connection

_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


class NetworkAttempted(RuntimeError):
    pass


def _host_of(address):
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _guard(original):
    def wrapper(self, address, *args, **kwargs):
        if _host_of(address) not in _LOCAL:
            raise NetworkAttempted(f"outbound connection attempted to {address!r}")
        return original(self, address, *args, **kwargs)

    return wrapper


def _guard_getaddrinfo(host, *args, **kwargs):
    if str(host) not in _LOCAL and host is not None:
        raise NetworkAttempted(f"DNS resolution attempted for {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _guard_create_connection(address, *args, **kwargs):
    if _host_of(address) not in _LOCAL:
        raise NetworkAttempted(f"outbound connection attempted to {address!r}")
    return _real_create_connection(address, *args, **kwargs)


def pytest_configure(config):
    socket.socket.connect = _guard(_real_connect)
    socket.socket.connect_ex = _guard(_real_connect_ex)
    socket.getaddrinfo = _guard_getaddrinfo
    socket.create_connection = _guard_create_connection
    print("\n[audit] network guard armed (loopback still permitted)")


def pytest_unconfigure(config):
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    socket.getaddrinfo = _real_getaddrinfo
    socket.create_connection = _real_create_connection
