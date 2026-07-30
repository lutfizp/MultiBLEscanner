from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any


class RealtimeBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._closing = False

    def start(self) -> None:
        self._closing = False

    def request_shutdown(self) -> None:
        self._closing = True
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._closing:
            return
        message = {
            "type": event_type,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        data = f"event: {event_type}\ndata: {json.dumps(message, default=str)}\n\n"
        stale: list[asyncio.Queue[str | None]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    async def stream(self) -> AsyncGenerator[str, None]:
        if self._closing:
            return
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            yield "event: connected\ndata: {\"status\":\"connected\"}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20)
                    if item is None:
                        return
                    yield item
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            self._subscribers.discard(queue)


class TopicRealtimeBroker:
    """Best-effort SSE delivery isolated by tracking-session ID."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str | None]]] = {}
        self._closing = False

    def start(self) -> None:
        self._closing = False

    def request_shutdown(self) -> None:
        self._closing = True
        for queues in tuple(self._subscribers.values()):
            for queue in tuple(queues):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(None)

    async def publish(self, topic: str, event_type: str, payload: dict[str, Any]) -> None:
        if self._closing:
            return
        message = {
            "type": event_type,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        data = f"event: {event_type}\ndata: {json.dumps(message, default=str)}\n\n"
        for queue in tuple(self._subscribers.get(topic, ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(data)

    async def stream(self, topic: str) -> AsyncGenerator[str, None]:
        if self._closing:
            return
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=50)
        self._subscribers.setdefault(topic, set()).add(queue)
        try:
            yield "event: connected\ndata: {\"status\":\"connected\"}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20)
                    if item is None:
                        return
                    yield item
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            subscribers = self._subscribers.get(topic)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(topic, None)


broker = RealtimeBroker()
tracking_broker = TopicRealtimeBroker()
