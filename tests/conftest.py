import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Any accidental outbound connection or DNS lookup fails the test."""

    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in tests")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
