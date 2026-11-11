"""Native systemd liveness notification, without a privileged polling helper."""
from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Callable


def notify(message: str) -> bool:
    address = os.environ.get("NOTIFY_SOCKET", "")
    if os.name != "posix" or not address:
        return False
    if address.startswith("@"):
        address = "\x00" + address[1:]
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        return False
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as client:
            client.settimeout(1)
            client.connect(address)
            client.sendall(message.encode("ascii"))
        return True
    except OSError:
        return False


async def supervise(started: Callable[[], bool], stopping: Callable[[], bool]) -> None:
    """Notify only from the HTTP event loop. A blocked loop cannot fake continued liveness."""
    if not os.environ.get("NOTIFY_SOCKET"):
        return
    while not started():
        if stopping():
            return
        await asyncio.sleep(0.1)
    notify("READY=1")
    try:
        while not stopping():
            notify("WATCHDOG=1")
            await asyncio.sleep(10)
    finally:
        notify("STOPPING=1")
