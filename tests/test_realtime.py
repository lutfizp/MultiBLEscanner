from __future__ import annotations

import asyncio

from backend.app.realtime import RealtimeBroker


def test_shutdown_releases_sse_subscriber() -> None:
    async def exercise() -> None:
        broker = RealtimeBroker()
        stream = broker.stream()

        connected = await stream.__anext__()
        assert "event: connected" in connected

        pending_item = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0)
        broker.request_shutdown()

        try:
            await asyncio.wait_for(pending_item, timeout=0.5)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("SSE stream did not close during broker shutdown")

    asyncio.run(exercise())
