"""Admission control: serve at most as many API requests at once as there are
database connections (NFR-01).

FastAPI runs each synchronous dependency and endpoint on a shared pool of 40 threads,
handing the thread back in between. A request's session takes a connection in its
first dependency (``current_user`` reads the account) and keeps it until the response
is sent. Under a burst - fifty students submitting when a quiz timer ends - every
thread can end up waiting for a connection while every connection belongs to a
request waiting for a thread. Nothing moves until the pool's 30-second timeout fails
the requests.

Admitting at most ``max_connections()`` requests (fewer than the 40 threads) breaks
that cycle: an admitted request always finds a connection and a thread. The others
wait here, before they hold anything, and are served in arrival order.
"""

import asyncio

from starlette.types import ASGIApp, Receive, Scope, Send


class RequestLimit:
    """ASGI middleware letting at most ``limit`` requests under ``prefix`` run at once."""

    def __init__(self, app: ASGIApp, limit: int, prefix: str = "/api/") -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.app = app
        self.limit = limit
        self.prefix = prefix
        self._loop: asyncio.AbstractEventLoop | None = None
        self._slots: asyncio.Semaphore | None = None

    def _semaphore(self) -> asyncio.Semaphore:
        # A semaphore belongs to one event loop; the test client starts a new loop for
        # each client, so make a fresh one when the loop changes.
        loop = asyncio.get_running_loop()
        if self._slots is None or self._loop is not loop:
            self._loop, self._slots = loop, asyncio.Semaphore(self.limit)
        return self._slots

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        async with self._semaphore():
            await self.app(scope, receive, send)
