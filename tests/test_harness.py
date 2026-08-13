"""Tests for the test harness itself.

A network guard that silently stopped working would be worse than no guard,
because the suite would still claim to be offline. So the guard is tested.
"""

from __future__ import annotations

import socket

import pytest


def test_the_guard_refuses_to_connect() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="tried to open a network connection"):
        sock.connect(("example.invalid", 80))


def test_the_guard_refuses_connect_ex() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="tried to open a network connection"):
        sock.connect_ex(("example.invalid", 80))


def test_the_guard_refuses_create_connection() -> None:
    with pytest.raises(RuntimeError, match="tried to open a network connection"):
        socket.create_connection(("example.invalid", 80))


def test_constructing_a_socket_is_still_allowed() -> None:
    # Deliberately permitted: libraries build SSL contexts and socket objects
    # without going anywhere. Blocking that caught innocent code and taught us
    # nothing about whether a request was made.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()


@pytest.mark.network
def test_the_marker_opts_out_of_the_guard() -> None:
    """The opt-out has to actually opt out.

    Asserting that `connect` merely exists proves nothing: the guard replaces
    it with a function, so it is not None either way and the test passed
    whether or not the marker worked. What separates a guarded socket from an
    unguarded one is the *kind* of failure. The guard raises `RuntimeError`
    without touching anything; a real socket refused by the operating system
    raises `OSError`. `RuntimeError` is not an `OSError`, so a guard still in
    place here fails this test rather than satisfying it.

    Port 0 is reserved and connectable nowhere, and this stays on the
    loopback interface, so the one test permitted to reach the network does
    not actually leave the machine.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        with pytest.raises(OSError):
            sock.connect(("127.0.0.1", 0))
    finally:
        sock.close()
