import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    # pipeline nodes run in worker threads, so publish() hops back onto the server event loop
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def _broadcast(self, event: dict) -> None:
        for ws in list(self._connections):
            try:
                await ws.send_json(event)
            except Exception:
                self._connections.discard(ws)

    def publish(self, event: dict) -> None:
        if self._loop and self._connections:
            asyncio.run_coroutine_threadsafe(self._broadcast(event), self._loop)
