"""WebSocket route for the observability telemetry stream.

Registers ``/ws/telemetry`` on the FastAPI app. The handler accepts the
connection, registers the client with :data:`app.telemetry.telemetry_bus`
(so any ``emit()`` reaches it), and starts :data:`demo_generator` so the
panel always has something to render even without real data.

------------------------------------------------------------------------
How to wire this into the app **without** modifying existing core files
------------------------------------------------------------------------
This module exposes :func:`register` rather than decorating a route at
import time. From ``backend/app/main.py`` (next to the existing
``@app.websocket("/ws")`` registration), add two lines:

    from .ws_handlers.telemetry import register as register_telemetry_ws
    register_telemetry_ws(app)

That single call adds the ``/ws/telemetry`` endpoint. No other core
module needs to change. Real subsystems can later push live events by
calling ``await telemetry_bus.emit(event_type, payload)`` from anywhere
in the backend (e.g. inside ``ws/vision_handler.py`` after a VLM result,
or ``ws/audio_handler.py`` after an ASR final) — no edits to this file
required.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..telemetry import demo_generator, telemetry_bus

logger = logging.getLogger(__name__)


async def _telemetry_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await telemetry_bus.register(ws)

    # Start the demo stream whenever the first client connects so the
    # panel is never empty during a demo.
    if not demo_generator.running:
        try:
            demo_generator.start()
        except RuntimeError:
            # No running event loop — shouldn't happen inside a ws handler.
            logger.warning("[telemetry ws] could not start demo generator (no event loop)")

    try:
        # We don't expect client messages, but reading lets us detect
        # disconnects promptly and keeps the socket alive.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("[telemetry ws] error: %s", exc)
    finally:
        await telemetry_bus.unregister(ws)


def register(app: FastAPI) -> None:
    """Register the ``/ws/telemetry`` route on the given FastAPI app.

    Call this once at app startup, e.g. in ``backend/app/main.py``::

        from .ws_handlers.telemetry import register as register_telemetry_ws
        register_telemetry_ws(app)
    """

    @app.websocket("/ws/telemetry")
    async def _ws_telemetry(ws: WebSocket) -> None:  # pragma: no cover
        await _telemetry_endpoint(ws)

    logger.info("[telemetry] registered /ws/telemetry route")
