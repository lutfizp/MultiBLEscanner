from __future__ import annotations

import asyncio

from backend.app.realtime import RealtimeBroker, TopicRealtimeBroker


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


def test_tracking_topic_stream_is_isolated_and_keeps_latest_sample() -> None:
    async def exercise() -> None:
        broker = TopicRealtimeBroker()
        stream = broker.stream("session-a")
        connected = await stream.__anext__()
        assert "event: connected" in connected

        await broker.publish("session-b", "tracking_sample", {"rssi": -90})
        await broker.publish("session-a", "tracking_sample", {"rssi": -61})
        item = await asyncio.wait_for(stream.__anext__(), timeout=0.5)

        assert "event: tracking_sample" in item
        assert '"rssi": -61' in item
        broker.request_shutdown()
        await stream.aclose()

    asyncio.run(exercise())
