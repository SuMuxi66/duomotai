"""Telemetry bus & demo data generator for the observability monitor.

This module is intentionally self-contained: it does **not** import any
core runtime modules (glasses_cam / cooking_core / ws_handlers.* / main),
so wiring it in only requires registering the ``/ws/telemetry`` route —
see :mod:`app.ws_handlers.telemetry`.

Two public surfaces:

* :class:`TelemetryBus` — a small pub/sub hub. Real subsystems (VLM / ASR /
  cooking / device / latency) can call ``await telemetry_bus.emit(type,
  payload)`` to push updates to every subscribed monitor client.
* :class:`DemoDataGenerator` — generates a synthetic event stream so the
  monitor panel remains demoable when no real data is flowing.

The singletons :data:`telemetry_bus` and :data:`demo_generator` are meant
to be imported by the ws handler.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from .logger import backend_log


# ---------------------------------------------------------------------------
# TelemetryBus
# ---------------------------------------------------------------------------
class TelemetryBus:
    """Broadcasts telemetry events to all subscribed WebSocket clients.

    Events are sent as JSON ``{"type": <event_type>, "payload": <payload>}``
    which matches the envelope used by the rest of the backend's WS layer.
    """

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        backend_log(f"[telemetry] client registered, total={len(self._clients)}")

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        backend_log(f"[telemetry] client unregistered, total={len(self._clients)}")

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast ``{type, payload}`` to every connected client.

        Failing/stale sockets are silently pruned.
        """
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        message = {"type": event_type, "payload": payload}
        results = await asyncio.gather(
            *(self._send(c, message) for c in clients), return_exceptions=True
        )
        dead = [c for c, r in zip(clients, results) if isinstance(r, Exception)]
        if dead:
            async with self._lock:
                for c in dead:
                    self._clients.discard(c)

    async def _send(self, ws: WebSocket, message: dict[str, Any]) -> None:
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                return
            await ws.send_json(message)
        except Exception as exc:  # noqa: BLE001 — surfaced to caller via gather
            backend_log(f"[telemetry] send failed: {exc}")
            raise


# Singleton bus used by the whole app
telemetry_bus = TelemetryBus()


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------
_VLM_SCENES: list[tuple[str, list[str], str]] = [
    ("切菜中", ["菜刀", "砧板", "青菜"], "low"),
    ("热油起锅", ["炒锅", "食用油", "蒜末"], "medium"),
    ("翻炒中", ["炒锅", "食材", "锅铲"], "low"),
    ("装盘点缀", ["盘子", "菜品"], "low"),
    ("监测到锅盖剧烈跳动", ["锅盖", "蒸汽"], "high"),
    ("油烟浓度偏高", ["油烟", "炒锅"], "medium"),
    ("加水炖煮", ["汤锅", "清水"], "low"),
]

_ASR_SENTENCES: list[str] = [
    "小云，帮我看看锅里的水开了没有",
    "现在转到小火再煮三分钟",
    "帮我设一个五分钟的定时器",
    "下一步该做什么",
    "今天的菜看起来不错",
    "提醒我五分钟后翻面",
]

_STEP_TITLES: list[str] = [
    "准备食材与厨具",
    "切配主料",
    "热油爆香",
    "下锅翻炒",
    "调味收汁",
    "装盘点缀",
]


class DemoDataGenerator:
    """Generates a synthetic telemetry stream for demo / preview purposes.

    Idempotent: calling :meth:`start` multiple times only spawns one task
    chain. The generator keeps running once started so the panel always
    has something to render; stop it with :meth:`stop`.
    """

    def __init__(self, bus: TelemetryBus) -> None:
        self.bus = bus
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._step = 1
        self._total_steps = 6
        self._asr_sentence_idx = 0
        self._asr_revealed = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            self._task = asyncio.create_task(self._run())
        except RuntimeError:
            # No running loop — caller is responsible for scheduling.
            self._running = False
            raise

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _run(self) -> None:
        backend_log("[telemetry] demo generator started")
        vlm_tick = 0
        asr_tick = 0
        device_tick = 0
        step_tick = 0
        try:
            while self._running:
                now = int(time.time() * 1000)
                vlm_tick += 1
                asr_tick += 1
                device_tick += 1
                step_tick += 1

                # VLM ~ every 2s
                if vlm_tick % 2 == 0:
                    await self._emit_vlm(now)
                # ASR ~ every 0.7s (tick is 1s here, so every tick)
                if asr_tick % 1 == 0:
                    await self._emit_asr(now)
                # Step ~ every 5s
                if step_tick % 5 == 0:
                    await self._emit_step(now)
                # Device ~ every 3s
                if device_tick % 3 == 0:
                    await self._emit_device(now)
                # Latency every tick
                await self._emit_latency(now)

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            backend_log("[telemetry] demo generator cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            backend_log(f"[telemetry] demo generator error: {exc}")
        finally:
            self._running = False

    async def _emit_vlm(self, now: int) -> None:
        scene, items, danger = random.choice(_VLM_SCENES)
        await self.bus.emit(
            "vlm_recognition",
            {
                "scene": scene,
                "items": items,
                "danger_level": danger,
                "confidence": round(random.uniform(0.78, 0.98), 3),
                "timestamp": now,
            },
        )

    async def _emit_asr(self, now: int) -> None:
        sentence = _ASR_SENTENCES[self._asr_sentence_idx]
        self._asr_revealed = min(len(sentence), self._asr_revealed + random.randint(1, 3))
        if self._asr_revealed >= len(sentence):
            await self.bus.emit(
                "asr_transcript",
                {"text": sentence, "is_final": True, "timestamp": now},
            )
            self._asr_sentence_idx = (self._asr_sentence_idx + 1) % len(_ASR_SENTENCES)
            self._asr_revealed = 0
        else:
            await self.bus.emit(
                "asr_transcript",
                {"text": sentence[: self._asr_revealed], "is_final": False, "timestamp": now},
            )

    async def _emit_step(self, now: int) -> None:
        if random.random() < 0.35 and self._step < self._total_steps:
            self._step += 1
        title = _STEP_TITLES[min(self._step - 1, len(_STEP_TITLES) - 1)]
        await self.bus.emit(
            "step_progress",
            {
                "current_step": self._step,
                "total_steps": self._total_steps,
                "step_title": title,
                "progress": round(self._step / self._total_steps, 3),
                "timestamp": now,
            },
        )

    async def _emit_device(self, now: int) -> None:
        await self.bus.emit(
            "device_status",
            {
                "fan_on": random.random() < 0.7,
                "temp_sensor_active": random.random() < 0.9,
                "weight_sensor_active": random.random() < 0.8,
                "gas_alarm": random.random() < 0.05,
                "temperature_c": round(random.uniform(95.0, 185.0), 1),
                "weight_g": random.randint(120, 880),
                "timestamp": now,
            },
        )

    async def _emit_latency(self, now: int) -> None:
        vlm = random.randint(80, 240)
        stt = random.randint(60, 180)
        llm = random.randint(220, 640)
        tts = random.randint(90, 260)
        e2e = vlm + stt + llm + tts + random.randint(20, 80)
        await self.bus.emit(
            "latency",
            {
                "e2e_ms": e2e,
                "vlm_ms": vlm,
                "stt_ms": stt,
                "llm_ms": llm,
                "tts_ms": tts,
                "timestamp": now,
            },
        )


# Singleton demo generator bound to the singleton bus
demo_generator = DemoDataGenerator(telemetry_bus)
