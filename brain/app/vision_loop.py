from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .events import add_event
from .robot_io import capture_and_describe_view, get_latest_vision, read_robot_snapshot, snapshot_to_robot_state
from .schemas import RobotState, VisionConfig, VisionStatus


class VisionLoop:
    def __init__(
        self,
        *,
        vector_serial: str | None,
        ollama_base_url: str,
        vision_model: str,
        capture_dir: Path,
    ) -> None:
        self.vector_serial = vector_serial
        self.ollama_base_url = ollama_base_url
        self.vision_model = vision_model
        self.capture_dir = capture_dir
        self.config = VisionConfig()
        self.ticks = 0
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_capture_ts: float | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> VisionStatus:
        latest = get_latest_vision()
        ts = latest.get("ts") or self.last_capture_ts
        age = round(time.time() - float(ts), 1) if ts else None
        return VisionStatus(
            enabled=self.config.enabled,
            interval_seconds=self.config.interval_seconds,
            respect_sleep=self.config.respect_sleep,
            ticks=self.ticks,
            last_capture_ts=self.last_capture_ts,
            latest_age_seconds=age,
            latest_vision=latest or None,
            last_result=self.last_result,
            last_error=self.last_error,
        )

    async def start(self, config: VisionConfig) -> VisionStatus:
        self.config = config.model_copy(update={"enabled": True})
        self.last_error = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self.status()

    async def stop(self) -> VisionStatus:
        self.config = self.config.model_copy(update={"enabled": False})
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        return self.status()

    async def tick(self, config: VisionConfig | None = None) -> VisionStatus:
        if config is not None:
            self.config = config
        try:
            await self._tick_once()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            add_event("vision_error", {"error": self.last_error})
        return self.status()

    async def _run(self) -> None:
        while self.config.enabled:
            started = time.time()
            try:
                await self._tick_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                add_event("vision_error", {"error": self.last_error})
            elapsed = time.time() - started
            await asyncio.sleep(max(1.0, self.config.interval_seconds - elapsed))

    async def _tick_once(self) -> None:
        self.ticks += 1
        robot_state = await self._robot_state()
        if self.config.respect_sleep and self._should_skip_for_state(robot_state):
            add_event(
                "vision_skip",
                {
                    "tick": self.ticks,
                    "reason": "Vector is asleep, charging, on the charger, or live state is unavailable.",
                    "robot_state": robot_state.model_dump(exclude_none=True),
                },
            )
            return

        result = await capture_and_describe_view(
            self.vector_serial,
            ollama_base_url=self.ollama_base_url,
            model=self.vision_model,
            output_dir=self.capture_dir,
        )
        self.last_result = result
        if result.get("ok"):
            self.last_capture_ts = float(result.get("ts") or time.time())
        add_event(
            "vision",
            {
                "tick": self.ticks,
                "source": "vision_loop",
                "robot_state": robot_state.model_dump(exclude_none=True),
                **result,
            },
        )

    async def _robot_state(self) -> RobotState:
        snapshot = await read_robot_snapshot(self.vector_serial)
        return snapshot_to_robot_state(snapshot)

    def _should_skip_for_state(self, robot_state: RobotState) -> bool:
        return bool(
            robot_state.connected is False
            or robot_state.sleeping
            or robot_state.charging
            or robot_state.calm_power_mode
            or robot_state.on_charger
        )
